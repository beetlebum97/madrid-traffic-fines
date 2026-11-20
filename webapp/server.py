"""Servidor local para la demo web de traficfines.

Ejecutar desde la raíz del proyecto con:
    uv run python webapp/server.py
"""

from __future__ import annotations

import json
import re
import socket
import sys
import threading
import time
import unicodedata
from datetime import date
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from traficfines import Cache, CacheError, MadridError, MadridFines  # noqa: E402

ADDRESS_DIRECTORY_URL = (
    "https://datos.madrid.es/dataset/213605-0-callejero-oficial-madrid/"
    "resource/213605-4-callejero-oficial-madrid-csv/download/"
    "213605-4-callejero-oficial-madrid-csv.csv"
)
ADDRESS_CACHE_NAME = "madrid-address-directory.csv"
ADDRESS_CACHE_DAYS = 30
ADDRESS_CACHE_MAX_AGE_MS = ADDRESS_CACHE_DAYS * 24 * 60 * 60 * 1000
DATASET_INFO_URL = "https://datos.madrid.es/dataset/210104-0-multas-circulacion-detalle/information"
DATASET_COVERAGE_TTL_SECONDS = 6 * 60 * 60
SPANISH_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_coverage_cache: dict[str, object] = {"expires_at": 0.0, "payload": None}
_coverage_lock = threading.Lock()
ADDRESS_PARTICLES = {"DE", "DEL", "LA", "LAS", "EL", "LOS", "Y"}
LOCATION_PREFIXES = {
    "CL", "C", "CALLE", "AV", "AVDA", "AVENIDA", "PASEO", "PS", "PO", "P",
    "PL", "PZ", "PLAZA", "CTRA", "CR", "CARRETERA", "CAMINO", "CM", "CMNO",
    "TRAVESIA", "TRVA", "RONDA", "RDA", "GLORIETA", "GTA", "PZA", "CARRERA",
    "CARR", "CUESTA", "CST", "PASAJE", "PJ", "PLAZUELA", "GT", "RO", "TRAV",
}


@lru_cache(maxsize=1)
def _load_month(year: int, month: int) -> MadridFines:
    """Reutiliza en memoria el último mes solicitado por el dashboard."""
    fines = MadridFines(app_name="traficfines_web", obsolescence=7)
    fines.add(year, month)
    return fines


@lru_cache(maxsize=1)
def _load_address_index() -> dict[tuple[str, str], list[tuple[float, float]]]:
    """Descarga una vez y normaliza el callejero oficial con coordenadas UTM."""
    cache = Cache(app_name="traficfines_web", obsolescence=ADDRESS_CACHE_DAYS)
    if cache.exists(ADDRESS_CACHE_NAME):
        age_ms = cache.how_old(ADDRESS_CACHE_NAME)
        if age_ms <= ADDRESS_CACHE_MAX_AGE_MS:
            directory_text = cache.load(ADDRESS_CACHE_NAME)
        else:
            directory_text = _download_address_directory(cache)
    else:
        directory_text = _download_address_directory(cache)

    required = ("VIA_PAR", "VIA_NOMBRE_ACENTOS", "VIA_NOMBRE", "NUMERO", "CALIFICADOR", "UTMX_ETRS", "UTMY_ETRS")
    directory = pd.read_csv(StringIO(directory_text), sep=";", dtype=str, keep_default_na=False, usecols=lambda name: name.strip() in required)
    index: dict[tuple[str, str], list[tuple[float, float]]] = {}
    columns = {str(name).strip(): position for position, name in enumerate(directory.columns)}
    if not set(required).issubset(columns):
        raise ValueError("El formato del callejero oficial ha cambiado.")

    records = directory.itertuples(index=False, name=None)
    for row in records:
        via_name = row[columns["VIA_NOMBRE_ACENTOS"]] or row[columns["VIA_NOMBRE"]]
        road = _normalise_address(f"{row[columns['VIA_PAR']]} {via_name}")
        number = _normalise_number(row[columns["NUMERO"]], row[columns["CALIFICADOR"]])
        if not road or not number:
            continue
        try:
            x = float(row[columns["UTMX_ETRS"]].replace(",", "."))
            y = float(row[columns["UTMY_ETRS"]].replace(",", "."))
        except (TypeError, ValueError, AttributeError):
            continue
        index.setdefault((road, number), []).append((x, y))
    return index


def _download_address_directory(cache: Cache) -> str:
    response = requests.get(
        ADDRESS_DIRECTORY_URL,
        headers={"User-Agent": "traficfines-portfolio/0.1 (public data visualization)"},
        timeout=120,
    )
    response.raise_for_status()
    # The published CSV contains Windows-1252 bytes despite its UTF-8 header.
    response.encoding = "cp1252"
    text = response.text
    cache.set(ADDRESS_CACHE_NAME, text)
    return text


def _normalise_address(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text).strip()
    words = re.sub(r"\s+", " ", text).split()
    return " ".join(word for word in words if word not in ADDRESS_PARTICLES)


def _normalise_number(number: object, qualifier: object = "") -> str:
    number_text = _normalise_address(number)
    qualifier_text = _normalise_address(qualifier)
    return f"{number_text}{qualifier_text}"


def _location_key(value: object) -> tuple[str, str] | None:
    text = _normalise_address(value)
    words = text.split()
    if words and words[0] in LOCATION_PREFIXES:
        text = " ".join(words[1:])
    text = re.sub(r"\s+N(?:UM(?:ERO)?)?\s+", " ", text)
    match = re.match(r"^(.*?)\s+(\d+)\s*([A-Z]?)$", text)
    if not match:
        return None
    road = match.group(1).strip()
    number = f"{match.group(2)}{match.group(3)}"
    return (road, number) if road else None


class DemoHandler(SimpleHTTPRequestHandler):
    """Sirve la página y expone un endpoint de análisis mensual."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path == "/api/month":
            self._month_summary(parse_qs(parsed.query))
            return
        if parsed.path == "/api/coverage":
            self._dataset_coverage()
            return
        if parsed.path == "/api/locations":
            self._month_locations(parse_qs(parsed.query))
            return
        if parsed.path.startswith("/api/"):
            self._send_json({"error": "Endpoint no encontrado"}, status=404)
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def _month_summary(self, query: dict[str, list[str]]) -> None:
        period = self._parse_period(query)
        if period is None:
            return
        year, month = period

        try:
            fines = _load_month(year, month)
            data = fines.data
            categories = fines.fines_calification()
            payments = fines.total_payment()
            key = (month, year)
            category_row = categories.loc[key] if key in categories.index else {}
            payment_row = payments.loc[key] if key in payments.index else {}
            hours = data["fecha"].dt.hour.value_counts().reindex(range(24), fill_value=0)

            payload = {
                "year": year,
                "month": month,
                "records": int(len(data)),
                "categories": {str(name): int(value) for name, value in category_row.items()},
                "payments": {
                    "maximum": float(payment_row.get("IMP_MAX", 0)),
                    "minimum": float(payment_row.get("IMP_MIN", 0)),
                },
                "hourly": [int(value) for value in hours.tolist()],
            }
            self._send_json(payload)
        except (MadridError, OSError, KeyError, ValueError) as exc:
            friendly_error = _friendly_portal_error(exc)
            error_message = friendly_error or f"No se pudieron cargar esos datos: {exc}"
            self._send_json({"error": error_message}, status=502)

    def _dataset_coverage(self) -> None:
        try:
            self._send_json(_load_dataset_coverage())
        except (requests.RequestException, ValueError) as exc:
            friendly_error = _friendly_portal_error(exc)
            error_message = friendly_error or f"No se pudo consultar la cobertura publicada: {exc}"
            self._send_json({"error": error_message}, status=502)

    def _month_locations(self, query: dict[str, list[str]]) -> None:
        period = self._parse_period(query)
        if period is None:
            return
        year, month = period

        try:
            data = _load_month(year, month).data
            try:
                address_index = _load_address_index()
                address_error = ""
            except (CacheError, requests.RequestException, OSError, ValueError, pd.errors.ParserError) as exc:
                # If the official address list is unavailable, avoid plotting
                # unverified CSV coordinates and report that the map is empty.
                address_index = {}
                address_error = _friendly_portal_error(exc) or str(exc)

            selected_x: list[float] = []
            selected_y: list[float] = []
            sources: list[str] = []
            for position, place in enumerate(data.get("LUGAR", pd.Series("", index=data.index)).tolist()):
                raw_location = _location_key(place)
                candidates = address_index.get(raw_location, []) if raw_location else []
                address_point = None
                if candidates:
                    if len(candidates) == 1:
                        address_point = candidates[0]
                    else:
                        address_point = (
                            sum(point[0] for point in candidates) / len(candidates),
                            sum(point[1] for point in candidates) / len(candidates),
                        )

                if address_point:
                    selected_x.append(address_point[0]); selected_y.append(address_point[1]); sources.append("Callejero oficial")
                else:
                    selected_x.append(float("nan")); selected_y.append(float("nan")); sources.append("")

            locations = data.copy()
            locations["x_m"] = selected_x
            locations["y_m"] = selected_y
            locations["location_source"] = sources
            valid = locations["x_m"].between(410_000, 470_000) & locations["y_m"].between(4_450_000, 4_520_000)
            geolocated = locations.loc[valid].copy()
            # Keep the address-directory coordinates; clustering only changes
            # how close markers are displayed at low zoom levels.
            geolocated["point_x"] = geolocated["x_m"].round(2)
            geolocated["point_y"] = geolocated["y_m"].round(2)
            severity_order = ["LEVE", "GRAVE", "MUY GRAVE"]
            geolocated["severity_type"] = geolocated["CALIFICACION"].fillna("").map(_as_text).str.upper()
            severity_types = [
                {"name": name, "count": int((geolocated["severity_type"] == name).sum())}
                for name in severity_order
            ]

            points = []
            for (point_x, point_y), group in geolocated.groupby(["point_x", "point_y"], sort=False):
                severity_breakdown = []
                for severity in severity_order:
                    severity_rows = group.loc[group["severity_type"] == severity]
                    if severity_rows.empty:
                        continue
                    samples = severity_rows.head(1)
                    severity_breakdown.append({
                        "name": severity,
                        "count": int(len(severity_rows)),
                        "examples": [{
                            "place": _as_text(record.get("LUGAR")),
                            "severity": _as_text(record.get("CALIFICACION")),
                            "offense": _as_text(record.get("HECHO-BOL")),
                            "hour": _format_hour(record.get("HORA")),
                            "amount": _as_number(record.get("IMP_BOL")),
                            "source": _as_text(record.get("location_source")),
                        } for _, record in samples.iterrows()],
                    })
                points.append({"x": float(point_x), "y": float(point_y), "severities": severity_breakdown})

            self._send_json({
                "schema_version": 2,
                "records": int(len(data)),
                "geolocated_records": int(valid.sum()),
                "without_location": int(len(data) - valid.sum()),
                "address_matches": int(sum(source == "Callejero oficial" for source in sources)),
                "address_directory_available": bool(address_index),
                "address_directory_error": address_error,
                "severity_types": severity_types,
                "points": points,
            })
        except (CacheError, MadridError, OSError, KeyError, ValueError) as exc:
            friendly_error = _friendly_portal_error(exc)
            error_message = friendly_error or f"No se pudieron cargar las ubicaciones: {exc}"
            self._send_json({"error": error_message}, status=502)

    def _parse_period(self, query: dict[str, list[str]]) -> tuple[int, int] | None:
        try:
            year = int(query.get("year", [""])[0])
            month = int(query.get("month", [""])[0])
            if year < 2015:
                raise ValueError("El conjunto de datos comienza en enero de 2015.")
            current_year = date.today().year
            if year > current_year:
                raise ValueError(f"No hay datos disponibles para {year}: es un año futuro. El último año consultable es {current_year}.")
            if month not in range(1, 13):
                raise ValueError("El mes debe estar entre 1 y 12.")
            return year, month
        except (ValueError, TypeError) as exc:
            self._send_json({"error": str(exc) or "Indica un año y mes válidos."}, status=400)
            return None

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _as_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", "").strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else text


def _friendly_portal_error(error: BaseException) -> str | None:
    """Devuelve un mensaje de usuario si el error apunta a indisponibilidad externa."""
    if isinstance(error, MadridError) and "no se encontró el fichero csv" in str(error).lower():
        return (
            "El portal municipal no ha facilitado el fichero de ese periodo. "
            "Puede estar temporalmente en mantenimiento o haber cambiado el recurso; "
            "inténtalo de nuevo más tarde."
        )
    pending: list[BaseException] = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, (requests.Timeout, requests.ConnectionError)):
            return (
                "El portal de datos del Ayuntamiento no está respondiendo ahora. "
                "Puede estar temporalmente fuera de servicio o tardar más de lo habitual; "
                "inténtalo de nuevo en unos minutos."
            )
        if isinstance(current, requests.HTTPError) and current.response is not None:
            if current.response.status_code == 429 or current.response.status_code >= 500:
                return (
                    "El portal de datos del Ayuntamiento no está respondiendo ahora. "
                    "Puede estar temporalmente fuera de servicio; inténtalo de nuevo en unos minutos."
                )
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return None


def _load_dataset_coverage() -> dict[str, object]:
    """Lee la cobertura temporal oficial y la conserva en memoria durante seis horas."""
    now = time.monotonic()
    with _coverage_lock:
        cached_payload = _coverage_cache["payload"]
        if cached_payload is not None and now < float(_coverage_cache["expires_at"]):
            return dict(cached_payload)

        response = requests.get(
            DATASET_INFO_URL,
            headers={"User-Agent": "traficfines-portfolio/0.1 (public data visualization)"},
            timeout=15,
        )
        response.raise_for_status()
        page_text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
        coverage_match = re.search(
            r"Cobertura temporal\s+.*?\bHasta\s+\d{1,2}\s+de\s+([A-Za-zÁÉÍÓÚáéíóú]+)\s+de\s+(\d{4})",
            page_text,
            re.IGNORECASE,
        )
        update_match = re.search(
            r"Fecha de actualización\s+(\d{1,2}\s+de\s+[A-Za-zÁÉÍÓÚáéíóú]+\s+de\s+\d{4})",
            page_text,
            re.IGNORECASE,
        )
        if not coverage_match:
            raise ValueError("No se encontró el campo «Cobertura temporal» en la ficha municipal.")
        month_name = coverage_match.group(1).lower()
        month = SPANISH_MONTHS.get(month_name)
        if month is None:
            raise ValueError(f"Mes de cobertura desconocido: {month_name}.")
        year = int(coverage_match.group(2))
        payload: dict[str, object] = {
            "available_through": {"year": year, "month": month, "label": f"{month_name} {year}"},
            "updated_at": update_match.group(1) if update_match else None,
        }
        _coverage_cache["payload"] = payload
        _coverage_cache["expires_at"] = now + DATASET_COVERAGE_TTL_SECONDS
        return dict(payload)


def _as_number(value) -> float | None:
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


def _format_hour(value) -> str:
    number = _as_number(value)
    if number is None:
        return ""
    hour = int(number)
    minute = round((number - hour) * 100)
    return f"{hour:02d}:{minute:02d}"


if __name__ == "__main__":
    address = ("0.0.0.0", 8000)
    print(f"Servidor escuchando en todas las interfaces (puerto {address[1]}).")
    print(f"Abre en este equipo: http://localhost:{address[1]}")
    try:
        # UDP connect only asks Windows which interface it would use; no data is sent.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            local_ip = probe.getsockname()[0]
        if local_ip and not local_ip.startswith("127."):
            print(f"Desde otro dispositivo de la red: http://{local_ip}:{address[1]}")
    except OSError:
        print("Para acceder desde otro dispositivo, usa la IP local de este equipo.")
    ThreadingHTTPServer(address, DemoHandler).serve_forever()
