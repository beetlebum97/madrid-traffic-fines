"""
Tests unitarios para el módulo madridFines (get_url, MadridFines y MadridError).
"""

from unittest.mock import patch, MagicMock
import pandas as pd
import pytest
import requests

from traficfines.madridFines import get_url, MadridFines, MadridError, RAIZ
from traficfines.cache import CacheError
from conftest import TEST_DATA_DIR


class TestGetUrl:
    """Pruebas para la función de scraping get_url."""

    def test_invalid_month_raises_error(self):
        """Un mes fuera del rango 1-12 debe lanzar MadridError inmediatamente."""
        with pytest.raises(MadridError):
            get_url(2024, 13)

    @patch("traficfines.madridFines.requests.get")
    def test_get_url_success_block_and_direct(self, mock_get, sample_html_madrid):
        """Verifica la localización del enlace CSV tanto en bloques como en enlaces directos."""
        mock_resp = MagicMock()
        mock_resp.text = sample_html_madrid
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        # Caso 1: localizado dentro de un bloque contenedor (Diciembre 2024)
        url_dic = get_url(2024, 12)
        assert url_dic == RAIZ + "dataset/multas/diciembre_2024.csv"

        # Caso 2: localizado directamente en el atributo title del enlace (Mayo 2019)
        url_may = get_url(2019, 5)
        assert url_may == RAIZ + "dataset/multas/mayo_2019.csv"

    @patch("traficfines.madridFines.requests.get")
    def test_get_url_not_found_or_network_error(self, mock_get, sample_html_madrid):
        """Verifica que lance MadridError si el mes no existe en el HTML o si falla la red."""
        mock_resp = MagicMock()
        mock_resp.text = sample_html_madrid
        mock_get.return_value = mock_resp

        with pytest.raises(MadridError):
            get_url(2020, 1)  # Enero 2020 no está en sample_html_madrid

        mock_get.side_effect = requests.RequestException("Caída del servidor")
        with pytest.raises(MadridError):
            get_url(2024, 12)


class TestMadridFines:
    """Pruebas unitarias para la clase MadridFines."""

    def test_init_and_encapsulation(self):
        """Comprueba la inicialización y que 'data' y 'loaded' no sean modificables desde fuera."""
        mf = MadridFines("madrid_test", obsolescence=5)
        assert mf.data.empty
        assert mf.loaded == []

        # Intentar mutar la lista devuelta por la propiedad no debe alterar el estado interno
        lista_externa = mf.loaded
        lista_externa.append((12, 2024))
        assert mf.loaded == []

        with pytest.raises(AttributeError):
            mf.data = pd.DataFrame()
        with pytest.raises(AttributeError):
            mf.loaded = [(1, 2024)]

    def test_init_cache_error(self):
        """Verifica que un fallo al iniciar CacheURL lance MadridError."""
        with patch("traficfines.madridFines.CacheURL", side_effect=CacheError("Fallo")):
            with pytest.raises(MadridError):
                MadridFines("app_error")

    def test_clean_empty_and_2019_columns(self):
        """Comprueba que clean maneje DataFrames vacíos y normalice COORDENADA_X (Mayo 2019)."""
        df_vacio = pd.DataFrame()
        MadridFines.clean(df_vacio)
        assert df_vacio.empty

        df_2019 = pd.DataFrame({
            "CALIFICACION ": [" LEVE "],
            "ANIO": [2019],
            "MES": [5],
            "HORA": [10.30],
            "IMP_BOL": [100.0],
            "DESCUENTO": [" SI "],
            "COORDENADA_X": [" 4400.5 "],
            "COORDENADA_Y\x00": [" 44700.1\x00 "]
        })
        MadridFines.clean(df_2019)
        assert "COORDENADA-X" in df_2019.columns
        assert "COORDENADA-Y" in df_2019.columns
        assert df_2019["CALIFICACION"].iloc[0] == "LEVE"
        assert df_2019["COORDENADA-X"].iloc[0] == 4400.5
        assert df_2019["fecha"].iloc[0] == pd.Timestamp("2019-05-01 10:30:00")

    @patch("traficfines.madridFines.get_url")
    def test_add_single_month_duplicate_and_full_year(self, mock_get_url, sample_csv_raw):
        """Verifica la carga de un mes, prevención de duplicados y carga de año completo."""
        mock_get_url.return_value = "https://datos.madrid.es/simulado.csv"
        mf = MadridFines("madrid_test")

        with patch.object(mf.cacheurl, "get", return_value=sample_csv_raw) as mock_cache_get:
            # Añadir diciembre 2024
            mf.add(2024, 12)
            assert mf.loaded == [(12, 2024)]
            assert len(mf.data) == 3
            assert mock_cache_get.call_count == 1

            # Volver a añadir el mismo mes no debe hacer nada
            mf.add(2024, 12)
            assert len(mf.data) == 3
            assert mock_cache_get.call_count == 1

            # Añadir año completo (month=None) cargará los 11 meses restantes (1 al 11)
            mf.add(2024, None)
            assert len(mf.loaded) == 12

    def test_load_and_clean_cache_errors(self):
        """Comprueba que fallos en load() o clean_cache() lancen MadridError."""
        mf = MadridFines("madrid_test")
        mf.clean_cache()

        with patch.object(mf.cacheurl, "clear", side_effect=CacheError("Error borrado")):
            with pytest.raises(MadridError):
                mf.clean_cache()

        with patch("traficfines.madridFines.get_url", side_effect=MadridError("URL error")):
            with pytest.raises(MadridError):
                MadridFines.load(2024, 12, mf.cacheurl)

    @patch("traficfines.madridFines.get_url", return_value="https://datos.madrid.es/dic24.csv")
    def test_analyses_and_plot_generation(self, mock_url, sample_csv_raw):
        """
        Verifica fines_calification, total_payment y que fines_hour guarde
        el gráfico dentro de tests/data.
        """
        mf = MadridFines("madrid_test")

        # En vacío debe devolver DataFrames vacíos y lanzar error al graficar
        assert mf.fines_calification().empty
        assert mf.total_payment().empty
        with pytest.raises(MadridError):
            mf.fines_hour(str(TEST_DATA_DIR / "fallo.png"))

        with patch.object(mf.cacheurl, "get", return_value=sample_csv_raw):
            mf.add(2024, 12)

        # 1. Distribución por calificación
        df_cal = mf.fines_calification()
        assert df_cal.loc[(12, 2024), "LEVE"] == 1
        assert df_cal.loc[(12, 2024), "GRAVE"] == 1
        assert df_cal.loc[(12, 2024), "MUY GRAVE"] == 1

        # 2. Recaudación máxima y mínima (60 con SI->30, 200 con SI->100, 500 con NO->500)
        df_pay = mf.total_payment()
        assert df_pay.loc[(12, 2024), "IMP_MAX"] == 760.0
        assert df_pay.loc[(12, 2024), "IMP_MIN"] == 630.0

        # 3. Gráfico guardado dentro de tests/data
        ruta_grafico = TEST_DATA_DIR / "evolucion_multas.png"
        mf.fines_hour(str(ruta_grafico))
        assert ruta_grafico.is_file()
