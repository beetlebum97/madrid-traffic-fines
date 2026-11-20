"""Genera una demo estática en docs/ para publicar en GitHub Pages.

Uso desde la raíz del proyecto:
    uv run python webapp/build_demo.py
    uv run python webapp/build_demo.py --year 2024 --month 12
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_SRC = PROJECT_ROOT / "webapp" / "static"
DOCS_DIR = PROJECT_ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"
REPO_URL = "https://github.com/beetlebum97/madrid-traffic-fines"

SPANISH_MONTH_NAMES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

sys.path.insert(0, str(PROJECT_ROOT))
from webapp.server import DemoHandler  # noqa: E402


def _fetch_json(base_url: str, endpoint: str) -> dict:
    response = requests.get(f"{base_url}{endpoint}", timeout=300)
    response.raise_for_status()
    return response.json()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _adapt_static_files(year: int, month: int, coverage: dict) -> None:
    if DOCS_DIR.exists():
        for item in DOCS_DIR.iterdir():
            if item.name == "data":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    for src_file in STATIC_SRC.iterdir():
        if src_file.is_file():
            shutil.copy2(src_file, DOCS_DIR / src_file.name)

    month_label = f"{SPANISH_MONTH_NAMES[month - 1].capitalize()} de {year}"
    updated_at = coverage.get("updated_at")
    updated_text = (
        f"Datos oficiales del portal municipal · Ficha actualizada el {updated_at}"
        if updated_at
        else "Datos oficiales del portal municipal"
    )
    repo_link = (
        f'<a href="{REPO_URL}" target="_blank" rel="noopener noreferrer" '
        'style="color:var(--green);font-weight:600">el repositorio</a>'
    )

    # 1. Adaptar rutas y textos de demo en docs/index.html
    index_path = DOCS_DIR / "index.html"
    html = index_path.read_text(encoding="utf-8")
    html = (
        html.replace('href="/favicon.svg"', 'href="./favicon.svg"')
        .replace('href="/styles.css', 'href="./styles.css')
        .replace('src="/app.js', 'src="./app.js')
        .replace('href="/"', 'href="./"')
        .replace(
            'Ver datos <span aria-hidden="true">→</span>',
            'Ver datos <span aria-hidden="true">↓</span>',
        )
        .replace(
            '<p class="coverage-heading">Disponibilidad de los datos</p>',
            '<p class="coverage-heading">Demo estática online</p>',
        )
        .replace(
            '<p id="coverage-range" class="coverage-range">Enero de 2015 — Junio de 2026</p>',
            f'<p id="coverage-range" class="coverage-range">Periodo precargado: {month_label}</p>',
        )
        .replace(
            '<p class="coverage-detail">El Ayuntamiento publica la información con un decalaje de tres meses para consolidar datos de distintas fuentes.</p>',
            f'<p class="coverage-detail">Esta vista en GitHub Pages muestra una captura estática de un mes real. '
            f'Para consultar cualquier periodo desde enero de 2015, ejecuta {repo_link} en local.</p>',
        )
        .replace(
            '<p id="coverage-updated" class="coverage-updated">Ficha actualizada el 20 de octubre de 2026</p>',
            f'<p id="coverage-updated" class="coverage-updated">{updated_text}</p>',
        )
    )
    index_path.write_text(html, encoding="utf-8")

    # 2. Adaptar docs/app.js: rutas locales, carga inicial y scroll suave en botones
    app_path = DOCS_DIR / "app.js"
    js = app_path.read_text(encoding="utf-8")
    js = (
        js.replace("loadCachedCoverage();", "// Cobertura fija en modo demo estática")
        .replace("loadCoverage();", "")
        .replace(
            'fetch(`/api/month?year=${encodeURIComponent(year)}&month=${encodeURIComponent(month)}`)',
            'fetch("./data/month.json")',
        )
        .replace(
            'fetch(`/api/locations?year=${encodeURIComponent(year)}&month=${encodeURIComponent(month)}`)',
            'fetch("./data/locations.json")',
        )
    )

    static_boot = f"""
// Configuración automática para la demo estática de GitHub Pages
periodInputTouched = true;
yearInput.value = "{year}";
monthInput.value = "{month}";
yearInput.disabled = true;
monthInput.disabled = true;

window.addEventListener("submit", (event) => {{
  if (event.target !== form) return;
  event.preventDefault();
  event.stopPropagation();
  if (!results.hidden) {{
    results.scrollIntoView({{ behavior: "smooth", block: "start" }});
  }}
}}, true);

const mapCard = document.querySelector(".map-card");
mapButton.addEventListener("click", () => {{
  if (mapCard) {{
    window.setTimeout(() => {{
      mapCard.scrollIntoView({{ behavior: "smooth", block: "start" }});
    }}, 80);
  }}
}});

window.addEventListener("DOMContentLoaded", async () => {{
  try {{
    const response = await fetch("./data/month.json");
    const data = await response.json();
    if (!response.ok) throw new Error("No se pudieron cargar los datos de la demo.");
    render(data);
    results.hidden = false;
    message.hidden = true;
  }} catch (error) {{
    message.hidden = false;
    message.className = "message error";
    message.textContent = error.message;
  }}
}});
"""
    app_path.write_text(js + static_boot, encoding="utf-8")


def build_demo(year: int | None, month: int | None) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        print("1/4 Consultando cobertura publicada...")
        coverage = _fetch_json(base_url, "/api/coverage")

        if year is None or month is None:
            latest = coverage["available_through"]
            year = int(latest["year"])
            month = int(latest["month"])
        print(f"    Periodo seleccionado para la demo: {month:02d}/{year}")

        print("2/4 Generando resumen mensual (month.json)...")
        month_data = _fetch_json(base_url, f"/api/month?year={year}&month={month}")

        print("3/4 Geolocalizando multas con el callejero oficial (locations.json)...")
        locations_data = _fetch_json(base_url, f"/api/locations?year={year}&month={month}")

        _write_json(DATA_DIR / "coverage.json", coverage)
        _write_json(DATA_DIR / "month.json", month_data)
        _write_json(DATA_DIR / "locations.json", locations_data)

        print("4/4 Copiando y adaptando frontend en docs/...")
        _adapt_static_files(year, month, coverage)

        size_mb = (DATA_DIR / "locations.json").stat().st_size / (1024 * 1024)
        print(f"\n[OK] Demo estática lista en {DOCS_DIR}")
        print(f"     Tamaño de locations.json: {size_mb:.2f} MB")
    finally:
        server.shutdown()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera la demo estática en docs/.")
    parser.add_argument("--year", type=int, help="Año de la demo (por defecto: último publicado)")
    parser.add_argument("--month", type=int, help="Mes de la demo (por defecto: último publicado)")
    args = parser.parse_args()
    build_demo(args.year, args.month)


if __name__ == "__main__":
    main()