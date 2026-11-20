"""
Crea (o actualiza) una cuenta de solo consulta.

    python manage.py crear_consulta admin --password admin

Además hay que poner el usuario en la variable de entorno CONSULTA_USUARIOS (p. ej. CONSULTA_USUARIOS=admin);
sin eso la cuenta existe pero no ve ninguna obra.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from obras.models import Usuario


class Command(BaseCommand):
    help = "Crea o actualiza una cuenta de solo consulta (ve todo, no modifica nada)."

    def add_arguments(self, parser):
        parser.add_argument("usuario", help="Usuario con el que inicia sesión (puede ser un correo o un nombre como «admin»).")
        parser.add_argument("--password", required=True)
        parser.add_argument("--nombre", default="Consulta")
        parser.add_argument("--apellidos", default="Solo lectura")

    def handle(self, usuario, password, nombre, apellidos, **_):
        usuario = usuario.strip().lower()
        cuenta = Usuario.objects.filter(correo=usuario).first()
        if cuenta is None:
            cuenta = Usuario(correo=usuario, nombre=nombre, apellidos=apellidos)
        cuenta.activo = True
        cuenta.set_password(password)  # sin validadores: la decide quien administra el sistema
        cuenta.save()
        self.stdout.write(self.style.SUCCESS(f"Cuenta «{usuario}» lista."))
        if usuario not in settings.CONSULTA_USUARIOS:
            self.stdout.write(self.style.WARNING(
                f"Falta agregar «{usuario}» a la variable CONSULTA_USUARIOS para que sea de solo consulta."))
        if usuario in settings.ADMIN_EMAILS:
            self.stdout.write(self.style.ERROR("Ese usuario también está en ADMIN_EMAILS; quítalo de ahí."))
