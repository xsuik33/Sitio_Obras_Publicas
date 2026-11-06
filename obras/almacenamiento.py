"""
Almacenamiento de archivos en Supabase Storage (API HTTP, sin dependencias extra).

Variables de entorno (en Render; nunca en el repo):
  SUPABASE_URL          https://<proyecto>.supabase.co
  SUPABASE_SERVICE_KEY  Secret key «sb_secret_…» (Project Settings → API Keys) o la antigua service_role.
                        Solo vive en el servidor.
  SUPABASE_BUCKET       bucket PRIVADO donde se guardan los archivos (por defecto «evidencias»)

Sin SUPABASE_URL se usa el disco local (MEDIA_ROOT), útil en desarrollo y pruebas.
Los archivos nunca son públicos: la app revisa permisos y redirige a una URL firmada que caduca pronto.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


class ErrorAlmacenamiento(Exception):
    pass


@deconstructible
class SupabaseStorage(Storage):
    def __init__(self, url=None, clave=None, bucket=None, caducidad=None):
        self.url = (url or settings.SUPABASE_URL).rstrip("/")
        self.clave = clave or settings.SUPABASE_SERVICE_KEY
        self.bucket = bucket or settings.SUPABASE_BUCKET
        self.caducidad = caducidad or settings.SUPABASE_URL_CADUCIDAD

    # -- HTTP -------------------------------------------------------------------
    def _ruta(self, nombre):
        return urllib.parse.quote(nombre.lstrip("/"))

    def _peticion(self, metodo, ruta, datos=None, tipo="application/json", extra=None):
        cabeceras = {"apikey": self.clave, "Content-Type": tipo}
        if self.clave.startswith("eyJ"):  # clave antigua (JWT service_role); las nuevas sb_secret_ van solo en apikey
            cabeceras["Authorization"] = f"Bearer {self.clave}"
        cabeceras.update(extra or {})
        peticion = urllib.request.Request(f"{self.url}/storage/v1/{ruta}", data=datos, headers=cabeceras, method=metodo)
        try:
            with urllib.request.urlopen(peticion, timeout=30) as respuesta:
                return respuesta.read()
        except urllib.error.HTTPError as e:
            detalle = e.read()[:300].decode("utf-8", "replace")
            raise ErrorAlmacenamiento(f"Supabase Storage respondió {e.code}: {detalle}") from e
        except urllib.error.URLError as e:
            raise ErrorAlmacenamiento(f"No se pudo conectar con Supabase Storage: {e.reason}") from e

    def _json(self, metodo, ruta, cuerpo):
        return json.loads(self._peticion(metodo, ruta, json.dumps(cuerpo).encode()) or b"null")

    # -- API de Storage de Django -------------------------------------------------
    def _save(self, name, content):
        content.seek(0)
        tipo = getattr(content, "content_type", None) or "application/octet-stream"
        self._peticion("POST", f"object/{self.bucket}/{self._ruta(name)}", content.read(), tipo, {"x-upsert": "false"})
        return name

    def _open(self, name, mode="rb"):
        return ContentFile(self._peticion("GET", f"object/authenticated/{self.bucket}/{self._ruta(name)}"), name=name)

    def delete(self, name):
        self._json("DELETE", f"object/{self.bucket}", {"prefixes": [name]})

    def exists(self, name):
        return False  # los nombres llevan un UUID; si chocaran, Supabase rechaza la subida (x-upsert: false)

    def url_firmada(self, name, descargar=None):
        datos = self._json("POST", f"object/sign/{self.bucket}/{self._ruta(name)}", {"expiresIn": self.caducidad})
        firmada = f"{self.url}/storage/v1{datos['signedURL']}"
        if descargar:
            firmada += "&download=" + urllib.parse.quote(descargar)
        return firmada

    def url(self, name):
        return self.url_firmada(name)
