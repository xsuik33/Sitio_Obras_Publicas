"""
Envío de correos.

Render (plan gratuito) bloquea los puertos SMTP (25, 465 y 587), así que se usa la API HTTP de Brevo.
Variables de entorno:
  BREVO_API_KEY     clave de API de Brevo (si falta, los correos se imprimen en la consola)
  MAIL_FROM_EMAIL   remitente verificado en Brevo (Senders)
  MAIL_FROM_NAME    nombre del remitente (opcional)
"""
import json
import logging
import re
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends.base import BaseEmailBackend
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


class BrevoEmailBackend(BaseEmailBackend):
    """Backend de Django que envía por la API HTTP de Brevo (sin SMTP)."""

    def send_messages(self, email_messages):
        enviados = 0
        for mensaje in email_messages:
            if not mensaje.to:
                continue
            cuerpo = {
                "sender": {"email": settings.MAIL_FROM_EMAIL, "name": settings.MAIL_FROM_NAME},
                "to": [{"email": destinatario} for destinatario in mensaje.to],
                "subject": mensaje.subject,
                "textContent": mensaje.body,
            }
            html = next((contenido for contenido, tipo in getattr(mensaje, "alternatives", []) if tipo == "text/html"), None)
            if html:
                cuerpo["htmlContent"] = html
            peticion = urllib.request.Request(
                settings.BREVO_API_URL,
                data=json.dumps(cuerpo).encode("utf-8"),
                headers={"api-key": settings.BREVO_API_KEY, "content-type": "application/json", "accept": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(peticion, timeout=10):
                    enviados += 1
            except (urllib.error.URLError, OSError):
                if not self.fail_silently:
                    raise
        return enviados


# ---------------------------------------------------------------------------
# Versión HTML de los avisos
# ---------------------------------------------------------------------------
# Los avisos se escriben como texto plano (ver reportes.py, mensuales.py y bitacora.py). De ese texto se arma
# una versión HTML con los colores del sistema: el asunto da el título; un párrafo «Etiqueta:» seguido de líneas
# va en un recuadro (o en una lista si sus líneas empiezan con «- »); «Estado: …» se destaca, y la URL final
# se vuelve un botón. El texto plano se envía también, para lectores que no muestran HTML.

ETIQUETAS = ("Comentarios", "Motivo", "Contenido de la nota", "Advertencias")
TONOS = (  # (palabra en el asunto, etiqueta, fondo, texto, borde, botón)
    ("rechaz", "Requiere corrección", "#fdeceb", "#b42318", "#b42318", "Corregir en el sistema"),
    ("aprob", "Aprobado", "#e6f4ea", "#127a3e", "#127a3e", "Ver en el sistema"),
    ("firmada", "Firmada", "#e6f4ea", "#127a3e", "#127a3e", "Ver la nota"),
    ("por revisar", "Por revisar", "#e7eef8", "#2f5f9e", "#2f5f9e", "Revisar ahora"),
    ("por firmar", "Por firmar", "#e7eef8", "#2f5f9e", "#2f5f9e", "Firmar ahora"),
)
TONO_GENERAL = ("Aviso", "#e2eff0", "#0e6170", "#0e6170", "Abrir en el sistema")


def _tono(asunto):
    minus = asunto.lower()
    datos = next((t[1:] for t in TONOS if t[0] in minus), TONO_GENERAL)
    return dict(zip(("etiqueta", "fondo", "texto", "borde", "boton"), datos))


def _bloques(mensaje):
    """Divide el texto en bloques para la plantilla. Devuelve (bloques, enlace)."""
    bloques, enlace = [], None
    for parte in re.split(r"\n\s*\n", mensaje.strip()):
        parte = parte.strip()
        if not parte:
            continue
        if re.fullmatch(r"https?://\S+", parte):
            enlace = parte
            continue
        lineas = parte.split("\n")
        cabeza = lineas[0].strip().rstrip(":")
        if cabeza in ETIQUETAS and lineas[0].strip().endswith(":") and len(lineas) > 1:
            cuerpo = [l.strip() for l in lineas[1:] if l.strip()]
            if all(l.startswith("- ") for l in cuerpo):
                bloques.append({"tipo": "lista", "etiqueta": cabeza, "items": [l[2:] for l in cuerpo]})
            else:
                bloques.append({"tipo": "cita", "etiqueta": cabeza, "texto": "\n".join(lineas[1:]).strip()})
            continue
        texto = []
        for linea in lineas:
            if linea.startswith("Estado: "):
                if texto:
                    bloques.append({"tipo": "texto", "texto": "\n".join(texto)})
                    texto = []
                bloques.append({"tipo": "estado", "texto": linea[len("Estado: "):].rstrip(".")})
            else:
                texto.append(linea)
        if texto:
            bloques.append({"tipo": "texto", "texto": "\n".join(texto)})
    return bloques, enlace


def html_aviso(asunto, mensaje):
    titulo, _, subtitulo = asunto.partition(" · ")
    bloques, enlace = _bloques(mensaje)
    tono = _tono(asunto)
    previa = next((b["texto"] for b in bloques if b["tipo"] == "texto"), "")[:140]
    return render_to_string("correo/aviso.html", {
        "asunto": asunto, "titulo": titulo, "subtitulo": subtitulo, "bloques": bloques, "enlace": enlace,
        "tono": tono, "boton": tono["boton"], "previa": previa,
    })


def notificar(destinatarios, asunto, mensaje):
    """
    Envía un aviso por correo. Nunca interrumpe la operación del usuario: si falla, lo registra en el log.
    Devuelve cuántos correos salieron.
    """
    direcciones = sorted({d.strip().lower() for d in destinatarios if d and d.strip()})
    if not direcciones:
        return 0
    try:
        correo = EmailMultiAlternatives(asunto, mensaje, settings.MAIL_FROM_EMAIL or None, direcciones)
        try:
            correo.attach_alternative(html_aviso(asunto, mensaje), "text/html")
        except Exception:  # noqa: BLE001 - si falla la versión bonita, sale al menos el texto
            logger.exception("No se pudo armar la versión HTML del correo «%s»", asunto)
        return correo.send(fail_silently=False)
    except Exception:  # noqa: BLE001 - un fallo de correo no debe romper la acción del usuario
        logger.exception("No se pudo enviar el correo «%s» a %s", asunto, direcciones)
        return 0
