"""
Configuración global de pytest y fixtures compartidas para la suite de tests.
"""

import shutil
from pathlib import Path
import pytest
import traficfines.cache as cache_module

# Ruta hacia el directorio 'data' dentro de la carpeta 'tests'
TEST_DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture(autouse=True)
def redirect_cache_to_test_data(monkeypatch):
    """
    Redirige automáticamente CACHE_DIR a tests/data antes de cada test
    y limpia el contenido al finalizar para garantizar tests aislados.
    """
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cache_module, "CACHE_DIR", TEST_DATA_DIR)
    yield
    # Limpieza posterior al test manteniendo la carpeta 'data'
    for item in TEST_DATA_DIR.iterdir():
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)


@pytest.fixture
def sample_csv_raw() -> str:
    """
    Devuelve un CSV simulado con las imperfecciones reales del Ayuntamiento de Madrid
    (espacios en nombres de columnas, bytes nulos \\x00, velocidades vacías, etc.).
    """
    cabecera = (
        "CALIFICACION;LUGAR;MES;ANIO;HORA;IMP_BOL;DESCUENTO;"
        " PUNTOS;DENUNCIANTE;HECHO-BOL;VEL_LIMITE;VEL_CIRCULA ;"
        "COORDENADA-X;COORDENADA-Y   \x00\x00\n"
    )
    fila1 = "LEVE   ;CL ALCALA 1 ;12;2024;20.23;60.0;SI ;0;SER ;ESTACIONAR MAL; ; ; ; \x00\n"
    fila2 = "GRAVE  ;M-30 KM 5   ;12;2024;16.30;200.0;SI;4;POLICIA;EXCESO VELOCIDAD;70;95;4407.67;44748.29\x00\n"
    fila3 = "MUY GRAVE;PASEO PRADO;12;2024;09.05;500.0;NO;6;AGENTES;ALCOHOLEMIA;50;80;4397.66;44751.88\n"
    return cabecera + fila1 + fila2 + fila3


@pytest.fixture
def sample_html_madrid() -> str:
    """Devuelve un fragmento HTML simulando el portal de datos abiertos de Madrid."""
    return """
    <html>
        <body>
            <div class="resource-item">
                <p>Datos de multas de Diciembre de 2024</p>
                <a class="ico-xls" href="/dataset/multas/diciembre_2024.xls">Descargar Excel</a>
                <a class="ico-csv" href="/dataset/multas/diciembre_2024.csv">Descargar CSV</a>
            </div>
            <div class="resource-item">
                <a title="Mayo 2019 CSV" href="/dataset/multas/mayo_2019.csv">Enlace directo</a>
            </div>
        </body>
    </html>
    """
