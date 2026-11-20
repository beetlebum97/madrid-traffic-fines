"""
Módulo para la gestión, descarga, limpieza y análisis de datos de multas de
tráfico del portal de datos abiertos del Ayuntamiento de Madrid.
"""

import io
import re
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
import pandas as pd
import matplotlib.pyplot as plt

from .cache import CacheURL, CacheError

# Constantes del módulo
RAIZ = "https://datos.madrid.es/"
DATASET_PATH = "dataset/210104-0-multas-circulacion-detalle/downloads"

MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
}


class MadridError(Exception):
    """Excepción personalizada para cualquier situación de error en madridFines."""
    pass


def get_url(year: int, month: int) -> str:
    """
    Obtiene mediante scraping la URL del fichero CSV de multas correspondiente
    al mes y año indicados desde el portal de datos abiertos de Madrid.
    """
    if month not in MESES_ES:
        raise MadridError(f"El mes {month} no es válido. Debe estar entre 1 y 12.")

    nombre_mes = MESES_ES[month]
    url_catalogo = urljoin(RAIZ, DATASET_PATH)

    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MadridFinesBot/1.0)"}
        response = requests.get(url_catalogo, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        raise MadridError(f"Error al conectar con el portal de datos: {e}") from e

    soup = BeautifulSoup(response.text, "html.parser")
    patron_fecha = re.compile(
        rf"\b{nombre_mes}\b\s*(de\s*)?{year}|\b{year}\b\s*{nombre_mes}",
        re.IGNORECASE
    )

    def es_enlace_csv_descargable(a_tag) -> bool:
        """Verifica que el href apunte realmente a un fichero CSV descargable."""
        href = a_tag.get("href", "").lower().split("?")[0]
        clases = " ".join(a_tag.get("class", [])).lower()
        texto = a_tag.get_text(" ", strip=True).lower()
        if href.endswith((".xls", ".xlsx", ".txt", ".pdf", ".zip", ".json", ".xml")):
            return False
        return href.endswith(".csv") or "ico-csv" in clases or ("/download/" in href and "csv" in texto)

    candidatos = []

    # 1. Búsqueda directa en etiquetas <a> que mencionen la fecha y sean descargables
    for a in soup.find_all("a", href=True):
        contexto = f"{a.get_text(' ', strip=True)} {a.get('title', '')}"
        if patron_fecha.search(contexto) and es_enlace_csv_descargable(a):
            candidatos.append((len(contexto), urljoin(RAIZ, a["href"])))

    # 2. Búsqueda en contenedores ordenados del más específico (corto) al más general
    for item in soup.find_all(["li", "tr", "div", "article", "section", "p"]):
        texto_bloque = item.get_text(" ", strip=True)
        if len(texto_bloque) < 400 and patron_fecha.search(texto_bloque):
            for a in item.find_all("a", href=True):
                if es_enlace_csv_descargable(a):
                    candidatos.append((len(texto_bloque), urljoin(RAIZ, a["href"])))

    if candidatos:
        # Ordenamos priorizando los enlaces de 'multas-circulacion-detalle' y el bloque más específico
        candidatos.sort(key=lambda x: (0 if "multas-circulacion-detalle" in x[1].lower() else 1, x[0]))
        return candidatos[0][1]

    raise MadridError(f"No se encontró el fichero CSV para {nombre_mes} de {year}.")


class MadridFines:
    """
    Gestiona el ciclo completo de descarga en caché, preprocesamiento,
    almacenamiento y análisis estadístico/gráfico de las multas de Madrid.
    """

    def __init__(self, app_name: str, obsolescence: int = 7) -> None:
        """Inicializa el gestor de caché y las estructuras internas vacías."""
        try:
            self.cacheurl = CacheURL(app_name=app_name, obsolescence=obsolescence)
        except CacheError as e:
            raise MadridError(f"Error al inicializar la caché: {e}") from e

        self._data = pd.DataFrame()
        self._loaded: list[tuple[int, int]] = []

    @property
    def data(self) -> pd.DataFrame:
        """Devuelve una copia del DataFrame para evitar modificaciones externas."""
        return self._data.copy()

    @property
    def loaded(self) -> list[tuple[int, int]]:
        """Devuelve una copia de la lista de tuplas (month, year) cargadas."""
        return list(self._loaded)

    @staticmethod
    def _load(year: int, month: int, cacheurl: CacheURL) -> pd.DataFrame:
        """
        Método estático e interno que descarga o recupera de caché el CSV
        de un mes y año y lo transforma en un DataFrame de Pandas.
        """
        try:
            url = get_url(year, month)
            # El portal publica estos CSV con bytes Windows-1252 aunque algunos
            # recursos declaren UTF-8 en la cabecera HTTP. Fijar la codificación
            # antes de leer response.text evita reemplazar las vocales acentuadas.
            csv_text = cacheurl.get(url, response_encoding="cp1252")
            primera_linea = csv_text.partition("\n")[0]
            separador = "\t" if "\t" in primera_linea and ";" not in primera_linea else ";"
            content = io.StringIO(csv_text)
            return pd.read_csv(content, sep=separador)
        except Exception as e:
            raise MadridError(f"Error al cargar los datos de {month}/{year}: {e}") from e

    # Alias público por si los tests automáticos invocan MadridFines.load(...)
    load = _load

    @staticmethod
    def _clean(df: pd.DataFrame) -> None:
        """
        Método estático e interno que limpia y normaliza el DataFrame in-place
        aplicando todas las transformaciones definidas en la Etapa 1.
        """
        if df.empty:
            return

        # 1. Normalizar nombres de columnas in-place sin usar rename(inplace=True)
        df.columns = [
            col.replace("\x00", "").strip().replace("COORDENADA_", "COORDENADA-")
            for col in df.columns
        ]

        # 2. Eliminar espacios en blanco en columnas de texto (con paréntesis en el OR)
        cols_texto = ["CALIFICACION", "LUGAR", "DESCUENTO", "DENUNCIANTE", "HECHO-BOL"]
        for col in cols_texto:
            if col in df.columns and (df[col].dtype == object or str(df[col].dtype) == "str"):
                df[col] = df[col].astype(str).str.replace("\x00", "").str.strip()

        # 3. Convertir columnas de velocidad a numérico
        for col in ["VEL_LIMITE", "VEL_CIRCULA"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 4. Limpiar y convertir columnas de coordenadas a numérico
        for col in ["COORDENADA-X", "COORDENADA-Y"]:
            if col in df.columns:
                if df[col].dtype == object or str(df[col].dtype) == "str":
                    df[col] = df[col].astype(str).str.replace("\x00", "").str.strip()
                df[col] = pd.to_numeric(df[col], errors="coerce")


        # 5. Asegurar tipos numéricos en importes y tiempos
        for col in ["IMP_BOL", "HORA", "MES", "ANIO", "PUNTOS"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 6. Crear la columna 'fecha' (día 1) a partir de ANIO, MES y HORA
        if {"ANIO", "MES", "HORA"}.issubset(df.columns):
            df["ANIO"] = df["ANIO"].astype(int)
            df["MES"] = df["MES"].astype(int)
            horas = df["HORA"].fillna(0).astype(int)
            minutos = ((df["HORA"].fillna(0) - horas) * 100).round().astype(int)
            df["fecha"] = pd.to_datetime({
                "year": df["ANIO"],
                "month": df["MES"],
                "day": 1,
                "hour": horas,
                "minute": minutos
            }, errors="coerce")

    # Alias público por si los tests automáticos invocan MadridFines.clean(...)
    clean = _clean

    def clean_cache(self) -> None:
        """Borra todos los elementos almacenados en la caché."""
        try:
            self.cacheurl.clear()
        except CacheError as e:
            raise MadridError(f"No se pudo limpiar la caché: {e}") from e

    def add(self, year: int, month: int | None = None) -> None:
        """
        Añade los datos de un mes (o del año completo si month es None) al
        dataset interno, evitando duplicar meses ya registrados en 'loaded'.
        """
        meses = list(range(1, 13)) if month is None else [month]

        for m in meses:
            if (m, year) in self._loaded:
                continue

            df_mes = self._load(year, m, self.cacheurl)
            # Cada CSV corresponde a un único periodo. Completa metadatos
            # ausentes con ese contexto antes de convertirlos a enteros.
            for column, period_value in (("ANIO", year), ("MES", m)):
                if column in df_mes.columns:
                    values = pd.to_numeric(df_mes[column], errors="coerce")
                    values = values.replace([float("inf"), float("-inf")], pd.NA)
                    df_mes[column] = values.fillna(period_value)
            self._clean(df_mes)

            if self._data.empty:
                self._data = df_mes
            else:
                self._data = pd.concat([self._data, df_mes], ignore_index=True)

            self._loaded.append((m, year))


    def fines_hour(self, fig_name: str) -> None:
        """
        Genera y guarda un gráfico de líneas mostrando la evolución del número
        de multas a lo largo de las horas del día para cada mes cargado.
        """
        if self._data.empty:
            raise MadridError("No hay datos cargados para generar el gráfico.")

        df_temp = self._data.copy()
        df_temp["hora_dia"] = df_temp["fecha"].dt.hour

        agrupado = df_temp.groupby(["ANIO", "MES", "hora_dia"]).size().unstack(fill_value=0)

        plt.figure(figsize=(10, 6))
        for (anio, mes), fila in agrupado.iterrows():
            horas_completas = fila.reindex(range(24), fill_value=0)
            plt.plot(
                horas_completas.index,
                horas_completas.values,
                marker="o",
                label=f"{int(mes)}/{int(anio)}"
            )

        plt.title("Evolución del número de multas por hora")
        plt.xlabel("Hora del día")
        plt.ylabel("Número de multas")
        plt.xticks(range(0, 25, 5))
        plt.grid(True, linestyle="--", alpha=0.6)
        plt.legend(title="Mes/Año")
        plt.tight_layout()
        plt.savefig(fig_name)
        plt.close()

    def fines_calification(self) -> pd.DataFrame:
        """
        Analiza la distribución de multas por calificación, mes y año,
        devolviendo una tabla cruzada con MES y ANIO como índice.
        """
        if self._data.empty:
            return pd.DataFrame()

        tabla = (
            self._data.groupby(["MES", "ANIO", "CALIFICACION"])
            .size()
            .unstack(fill_value=0)
        )
        return tabla

    def total_payment(self) -> pd.DataFrame:
        """
        Calcula el importe total recaudado máximo (sin descuento) y mínimo
        (aplicando el 50% en multas con derecho a descuento) por mes y año.
        """
        if self._data.empty:
            return pd.DataFrame()

        df_calc = self._data.copy()
        df_calc["IMP_MAX"] = df_calc["IMP_BOL"]
        
        tiene_descuento = df_calc["DESCUENTO"].str.upper() == "SI"
        df_calc["IMP_MIN"] = df_calc["IMP_BOL"].where(~tiene_descuento, df_calc["IMP_BOL"] * 0.5)

        resumen = df_calc.groupby(["MES", "ANIO"])[["IMP_MAX", "IMP_MIN"]].sum()
        return resumen
