"""
Tests unitarios para las clases Cache, CacheURL y la excepción CacheError.
"""

import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import requests

from traficfines.cache import Cache, CacheURL, CacheError
from conftest import TEST_DATA_DIR


class TestCache:
    """Pruebas unitarias para la clase base Cache."""

    def test_init_and_readonly_properties(self):
        """Verifica la creación del directorio en tests/data y las propiedades @property."""
        c = Cache(app_name="test_app", obsolescence=10)
        assert c.app_name == "test_app"
        assert c.obsolescence == 10
        assert Path(c.cache_dir) == TEST_DATA_DIR / "test_app"
        assert Path(c.cache_dir).is_dir()

        # Comprobar que los atributos son de solo lectura
        with pytest.raises(AttributeError):
            c.app_name = "otro"
        with pytest.raises(AttributeError):
            c.cache_dir = "/tmp"
        with pytest.raises(AttributeError):
            c.obsolescence = 5

    def test_set_exists_and_load(self):
        """Comprueba el ciclo completo de guardado, verificación y lectura."""
        c = Cache("test_app")
        assert not c.exists("fichero.txt")

        c.set("fichero.txt", "contenido de prueba")
        assert c.exists("fichero.txt")
        assert c.load("fichero.txt") == "contenido de prueba"

    def test_load_and_how_old_non_existent_raises_error(self):
        """Verifica que cargar o medir la edad de un fichero inexistente lance CacheError."""
        c = Cache("test_app")
        with pytest.raises(CacheError):
            c.load("no_existe.txt")
        with pytest.raises(CacheError):
            c.how_old("no_existe.txt")

    def test_how_old_calculation(self):
        """Verifica que la edad del fichero se calcule correctamente en milisegundos."""
        c = Cache("test_app")
        c.set("edad.txt", "datos")
        time.sleep(0.05)
        edad = c.how_old("edad.txt")
        assert edad >= 40.0  # Al menos ~50 ms

    def test_delete_and_clear(self):
        """Comprueba el borrado individual (incluso si no existe) y el vaciado completo."""
        c = Cache("test_app")
        c.set("f1.txt", "1")
        c.set("f2.txt", "2")
        sub = Path(c.cache_dir) / "subdir"
        sub.mkdir()

        c.delete("f1.txt")
        assert not c.exists("f1.txt")
        # Borrar un archivo que no existe no debe lanzar excepción
        c.delete("f1.txt")

        c.clear()
        assert not c.exists("f2.txt")
        assert not sub.exists()

    def test_os_errors_raise_cache_error(self):
        """Fuerza fallos de permisos/disco para cubrir los bloques except CacheError."""
        c = Cache("test_app")
        c.set("f1.txt", "datos")

        with patch.object(Path, "mkdir", side_effect=OSError("Fallo mkdir")):
            with pytest.raises(CacheError):
                Cache("app_fallida")

        with patch.object(Path, "write_text", side_effect=OSError("Fallo escritura")):
            with pytest.raises(CacheError):
                c.set("error.txt", "data")

        with patch.object(Path, "read_text", side_effect=OSError("Fallo lectura")):
            with pytest.raises(CacheError):
                c.load("f1.txt")

        with patch.object(Path, "stat", side_effect=OSError("Fallo stat")):
            with pytest.raises(CacheError):
                c.how_old("f1.txt")

        with patch.object(Path, "unlink", side_effect=OSError("Fallo unlink")):
            with pytest.raises(CacheError):
                c.delete("f1.txt")

        with patch.object(Path, "iterdir", side_effect=OSError("Fallo iterdir")):
            with pytest.raises(CacheError):
                c.clear()


class TestCacheURL:
    """Pruebas unitarias para la clase CacheURL simulando peticiones a Internet."""

    @patch("traficfines.cache.requests.get")
    def test_get_downloads_once_and_reuses_cache(self, mock_get):
        """Verifica que la primera llamada descargue de red y la segunda use la caché local."""
        mock_response = MagicMock()
        mock_response.text = "respuesta simulada"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        cu = CacheURL("test_url_app", obsolescence=7)
        url = "https://datos.madrid.es/test.csv?param=1"

        # 1ª llamada: descarga de internet
        res1 = cu.get(url, params={"a": 1})
        assert res1 == "respuesta simulada"
        assert mock_get.call_count == 1
        assert cu.exists(url, params={"a": 1})

        # 2ª llamada: recupera de caché sin llamar a requests.get
        res2 = cu.get(url, params={"a": 1})
        assert res2 == "respuesta simulada"
        assert mock_get.call_count == 1

        # Verificar métodos redefinidos
        assert cu.load(url, params={"a": 1}) == "respuesta simulada"
        assert cu.how_old(url, params={"a": 1}) >= 0
        cu.delete(url, params={"a": 1})
        assert not cu.exists(url, params={"a": 1})

    @patch("traficfines.cache.requests.get")
    def test_get_redownloads_when_obsolete(self, mock_get):
        """Verifica que si el fichero supera los días de obsolescence se vuelva a descargar."""
        mock_response = MagicMock()
        mock_response.text = "contenido actualizado"
        mock_get.return_value = mock_response

        # Obsolescencia de 0 días fuerza que caduque inmediatamente
        cu = CacheURL("test_obs", obsolescence=0)
        url = "https://datos.madrid.es/caducado.csv"

        cu.get(url)
        time.sleep(0.01)
        cu.get(url)
        assert mock_get.call_count == 2

    @patch("traficfines.cache.requests.get")
    def test_get_network_error_raises_cache_error(self, mock_get):
        """Comprueba que un fallo de red de requests se transforme en CacheError."""
        mock_get.side_effect = requests.RequestException("Sin conexión")
        cu = CacheURL("test_err")

        with pytest.raises(CacheError):
            cu.get("https://datos.madrid.es/fallo.csv")
