"""
Cliente de la e.firma del sistema (mini proyecto en servicio_efirma/).

Cada usuario crea su e.firma en «Mi e.firma» con un PIN: la autoridad certificadora del sistema le emite un
certificado (solo nombre y correo) y una llave privada cifrada con ese PIN, que se guardan en efirmas_usuarios.
Para firmar una nota solo escribe su PIN. No se usa la e.firma del SAT ni datos fiscales.

Si EFIRMA_URL está configurada, emitir y firmar se hacen en el servicio aparte (por HTTPS, con EFIRMA_TOKEN).
Si no, se usa el mismo núcleo en este proceso ("modo local"). La verificación siempre es local: solo necesita
el certificado guardado y la semilla de la autoridad certificadora (EFIRMA_SEMILLA).
"""
import json
import logging
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings

from servicio_efirma import efirma as nucleo
from servicio_efirma.efirma import PIN_MINIMO, ErrorEfirma  # noqa: F401 (se reexportan)

from .models import EfirmaUsuario

logger = logging.getLogger(__name__)


def _servicio(ruta, datos):
    peticion = Request(settings.EFIRMA_URL.rstrip("/") + ruta, data=json.dumps(datos).encode(), method="POST",
                       headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings.EFIRMA_TOKEN}"})
    try:
        with urlopen(peticion, timeout=30) as r:
            return json.load(r)
    except HTTPError as e:
        try:
            mensaje = json.load(e).get("error")
        except ValueError:
            mensaje = None
        if e.code == 422 and mensaje:
            raise ErrorEfirma(mensaje) from None
        logger.error("El servicio de e.firma respondió %s", e.code)
        raise ErrorEfirma("El servicio de e.firma no respondió bien. Intenta de nuevo en un momento.") from None
    except (OSError, ValueError):  # URLError, TimeoutError y errores de red son OSError
        logger.exception("No se pudo contactar al servicio de e.firma")
        raise ErrorEfirma("No se pudo contactar al servicio de e.firma. Intenta de nuevo en un momento.") from None


def emitir(usuario, pin):
    """Crea (o renueva) la e.firma del usuario con el PIN que eligió. Devuelve el EfirmaUsuario guardado."""
    nombre = f"{usuario.nombre} {usuario.apellidos}".strip()
    if settings.EFIRMA_URL:
        r = _servicio("/emitir", {"nombre": nombre, "correo": usuario.correo, "pin": pin})
    else:
        r = nucleo.emitir(nombre, usuario.correo, pin, semilla=settings.EFIRMA_SEMILLA)
    efirma, _ = EfirmaUsuario.objects.update_or_create(usuario=usuario, defaults={
        "certificado": r["certificado"], "llave_cifrada": r["llave_cifrada"], "numero": r["numero"],
        "valido_hasta": datetime.fromisoformat(r["valido_hasta"]),
    })
    return efirma


def de(usuario):
    return EfirmaUsuario.objects.filter(usuario=usuario).first()


def firmar(cadena, efirma, pin):
    """Sella la cadena con la e.firma del usuario. Lanza ErrorEfirma con un mensaje para el usuario si falla."""
    if settings.EFIRMA_URL:
        r = _servicio("/firmar", {"cadena": cadena, "certificado": efirma.certificado,
                                  "llave_cifrada": efirma.llave_cifrada, "pin": pin})
        modo = "servicio"
    else:
        r = nucleo.firmar(cadena, efirma.certificado, efirma.llave_cifrada, pin, semilla=settings.EFIRMA_SEMILLA)
        modo = "local"
    return {**r, "cadena": cadena, "modo": modo}


def verificar(firma):
    """True si el sello guardado corresponde a su cadena original y a un certificado de la AC del sistema."""
    return bool(firma.sello) and nucleo.verificar(firma.cadena_original or "", firma.sello, firma.certificado or "",
                                                  semilla=settings.EFIRMA_SEMILLA)


def datos(efirma):
    return nucleo.datos_certificado(nucleo.cargar_certificado(efirma.certificado))
