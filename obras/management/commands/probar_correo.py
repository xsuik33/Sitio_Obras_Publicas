from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from obras.correo import notificar


class Command(BaseCommand):
    help = "Envía un correo de prueba para comprobar la configuración (python manage.py probar_correo tu@correo.com)."

    def add_arguments(self, parser):
        parser.add_argument("destino")

    def handle(self, *args, **opciones):
        self.stdout.write(f"Backend de correo: {settings.EMAIL_BACKEND}")
        self.stdout.write(f"Remitente: {settings.MAIL_FROM_EMAIL or '(sin definir)'}")
        enviados = notificar(
            [opciones["destino"]], "Prueba · Sistema de Obras",
            "Este es un correo de prueba del Sistema de Obras. Si lo recibes, el envío funciona.",
        )
        if not enviados:
            raise CommandError("No se envió el correo. Revisa BREVO_API_KEY y MAIL_FROM_EMAIL (el remitente debe estar verificado en Brevo).")
        self.stdout.write(self.style.SUCCESS("Correo enviado."))
