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
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail import send_mail
from django.core.mail.backends.base import BaseEmailBackend

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


def notificar(destinatarios, asunto, mensaje):
    """
    Envía un aviso por correo. Nunca interrumpe la operación del usuario: si falla, lo registra en el log.
    Devuelve cuántos correos salieron.
    """
    direcciones = sorted({d.strip().lower() for d in destinatarios if d and d.strip()})
    if not direcciones:
        return 0
    try:
        return send_mail(
            asunto, mensaje, settings.MAIL_FROM_EMAIL or None, direcciones, fail_silently=False
        )
    except Exception:  # noqa: BLE001 - un fallo de correo no debe romper la acción del usuario
        logger.exception("No se pudo enviar el correo «%s» a %s", asunto, direcciones)
        return 0
