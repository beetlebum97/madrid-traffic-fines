"""
Módulo de gestión de caché local y descarga eficiente de URLs.

Proporciona las clases Cache y CacheURL para almacenar y recuperar ficheros
en disco, evitando descargas repetitivas y controlando la obsolescencia de los datos.
"""

import hashlib
import shutil
import time
from pathlib import Path
import requests

CACHE_DIR = Path.home() / ".my_cache"


class CacheError(Exception):
    """Excepción personalizada para cualquier error producido en el módulo cache."""
    pass


class Cache:
    """Gestiona el almacenamiento persistente de ficheros en un directorio local."""

    def __init__(self, app_name: str, obsolescence: int = 7) -> None:
        """Inicializa la caché creando la estructura de carpetas necesaria."""
        self._app_name = app_name
        self._obsolescence = obsolescence

        self._cache_path = CACHE_DIR / app_name
        self._cache_dir = str(self._cache_path)

        try:
            self._cache_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise CacheError(f"Error al crear el directorio de caché: {e}") from e

    @property
    def app_name(self) -> str:
        return self._app_name

    @property
    def cache_dir(self) -> str:
        return self._cache_dir

    @property
    def obsolescence(self) -> int:
        return self._obsolescence

    def _get_file_path(self, name: str) -> Path:
        """Método auxiliar privado para obtener la ruta completa de un fichero."""
        return self._cache_path / name

    def set(self, name: str, data: str) -> None:
        """Almacena en cache_dir los datos proporcionados con el nombre indicado."""
        file_path = self._get_file_path(name)
        try:
            file_path.write_text(data, encoding="utf-8")
        except Exception as e:
            raise CacheError(f"No se pudo guardar el fichero '{name}': {e}") from e

    def exists(self, name: str) -> bool:
        """Comprueba si ya existe un fichero con el nombre indicado en la caché."""
        return self._get_file_path(name).is_file()

    def load(self, name: str) -> str:
        """Recupera los datos de la caché. Lanza CacheError si no existe."""
        if not Cache.exists(self, name):
            raise CacheError(f"El fichero '{name}' no existe en la caché.")
        try:
            return self._get_file_path(name).read_text(encoding="utf-8")
        except Exception as e:
            raise CacheError(f"Error al leer el fichero '{name}': {e}") from e

    def how_old(self, name: str) -> float:
        """Devuelve la edad del fichero en milisegundos."""
        try:
            if not Cache.exists(self, name):
                raise CacheError(f"El fichero '{name}' no existe para calcular su edad.")
            mtime = self._get_file_path(name).stat().st_mtime
            edad_segundos = time.time() - mtime
            return edad_segundos * 1000.0
        except CacheError:
            raise
        except Exception as e:
            raise CacheError(f"Error al consultar la edad de '{name}': {e}") from e

    def delete(self, name: str) -> None:
        """Borra de la caché el fichero indicado. Si no existe, no lanza excepción."""
        try:
            self._get_file_path(name).unlink(missing_ok=True)
        except Exception as e:
            raise CacheError(f"Error al intentar borrar '{name}': {e}") from e

    def clear(self) -> None:
        """Borra todos los elementos almacenados en el directorio de la caché."""
        try:
            for item in self._cache_path.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
        except Exception as e:
            raise CacheError(f"Error al vaciar la caché: {e}") from e


class CacheURL(Cache):
    """Especialización de Cache para gestionar descargas de URLs mediante hashes."""

    def _generate_hash(self, url: str, **kwargs) -> str:
        """Genera un identificador MD5 único combinando la URL y los kwargs."""
        key = url
        if kwargs:
            kwargs_str = str(sorted(kwargs.items()))
            key += kwargs_str
        return hashlib.md5(key.encode("utf-8")).hexdigest()

    def _is_obsolete(self, url: str, **kwargs) -> bool:
        """Comprueba si el archivo en caché ha superado los días de obsolescencia."""
        edad_ms = self.how_old(url, **kwargs)
        limite_ms = self.obsolescence * 24 * 60 * 60 * 1000.0
        return edad_ms > limite_ms

    def get(self, url: str, **kwargs) -> str:
        """Recupera texto de una URL y permite fijar ``response_encoding``.

        ``response_encoding`` se usa para decodificar el cuerpo de la respuesta;
        no se envía como argumento HTTP. También forma parte de la clave de caché.
        """
        cache_kwargs = dict(kwargs)
        request_kwargs = dict(kwargs)
        response_encoding = request_kwargs.pop("response_encoding", None)

        if self.exists(url, **cache_kwargs) and not self._is_obsolete(url, **cache_kwargs):
            return self.load(url, **cache_kwargs)

        try:
            response = requests.get(url, **request_kwargs)
            response.raise_for_status()
            if response_encoding:
                response.encoding = response_encoding
            data = response.text

            hashed_name = self._generate_hash(url, **cache_kwargs)
            self.set(hashed_name, data)
            return data
        except requests.RequestException as e:
            raise CacheError(f"Error de red al descargar la URL '{url}': {e}") from e

    def exists(self, url: str, **kwargs) -> bool:
        """Comprueba si la URL (y sus parámetros) ya está registrada en la caché."""
        hashed_name = self._generate_hash(url, **kwargs)
        return super().exists(hashed_name)

    def load(self, url: str, **kwargs) -> str:
        """Carga los datos asociados a una URL directamente desde la caché."""
        hashed_name = self._generate_hash(url, **kwargs)
        return super().load(hashed_name)

    def how_old(self, name: str, **kwargs) -> float:
        """Calcula la edad en milisegundos del registro asociado a la URL."""
        hashed_name = self._generate_hash(name, **kwargs)
        return super().how_old(hashed_name)

    def delete(self, name: str, **kwargs) -> None:
        """Elimina de la caché el archivo asociado a la URL indicada."""
        hashed_name = self._generate_hash(name, **kwargs)
        super().delete(hashed_name)
