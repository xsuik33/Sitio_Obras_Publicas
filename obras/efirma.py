"""
Cliente del servicio de e.firma (mini proyecto en servicio_efirma/).

Si EFIRMA_URL está configurada, la firma se hace en ese servicio aparte (por HTTPS, con EFIRMA_TOKEN).
Si no, se usa el mismo núcleo en este proceso ("modo local"), útil para pruebas y para la demo.
La verificación de un sello se hace aquí: solo necesita el certificado público guardado con la firma.
"""
import json
import logging
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings

from servicio_efirma import efirma as nucleo
from servicio_efirma.efirma import ErrorEfirma  # noqa: F401 (se reexporta para las vistas)

logger = logging.getLogger(__name__)


def _multipart(campos, archivos):
    limite = uuid.uuid4().hex
    partes = []
    for nombre, valor in campos.items():
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{nombre}"\r\n\r\n'.encode()
                      + valor.encode("utf-8") + b"\r\n")
    for nombre, (archivo, contenido) in archivos.items():
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{nombre}"; filename="{archivo}"\r\n'
                      f"Content-Type: application/octet-stream\r\n\r\n".encode() + contenido + b"\r\n")
    partes.append(f"--{limite}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={limite}"


def firmar(cadena, cer, key, contrasena):
    """Firma la cadena con la e.firma. Lanza ErrorEfirma con un mensaje para el usuario si algo falla."""
    url = getattr(settings, "EFIRMA_URL", "")
    if not url:
        return {**nucleo.firmar(cadena, cer, key, contrasena), "cadena": cadena, "modo": "local"}
    cuerpo, tipo = _multipart({"cadena": cadena, "contrasena": contrasena}, {"cer": ("efirma.cer", cer), "key": ("efirma.key", key)})
    peticion = Request(url.rstrip("/") + "/firmar", data=cuerpo, method="POST",
                       headers={"Content-Type": tipo, "Authorization": f"Bearer {settings.EFIRMA_TOKEN}"})
    try:
        with urlopen(peticion, timeout=30) as r:
            return {**json.load(r), "cadena": cadena, "modo": "servicio"}
    except HTTPError as e:
        try:
            mensaje = json.load(e).get("error")
        except ValueError:
            mensaje = None
        if e.code == 422 and mensaje:
            raise ErrorEfirma(mensaje) from None
        logger.error("El servicio de e.firma respondió %s", e.code)
        raise ErrorEfirma("El servicio de e.firma no pudo firmar. Intenta de nuevo en un momento.") from None
    except (OSError, ValueError):  # URLError, TimeoutError y errores de red son OSError
        logger.exception("No se pudo contactar al servicio de e.firma")
        raise ErrorEfirma("No se pudo contactar al servicio de e.firma. Intenta de nuevo en un momento.") from None


def verificar(firma):
    """True si el sello guardado corresponde a su cadena original y a su certificado."""
    return bool(firma.sello) and nucleo.verificar(firma.cadena_original or "", firma.sello, firma.certificado or "")
