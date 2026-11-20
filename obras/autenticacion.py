"""
Acceso de solo consulta sin cuenta: quien escribe CONSULTA_USUARIO / CONSULTA_PASSWORD (por omisión admin / admin)
en el inicio de sesión entra como la cuenta de consulta. La primera vez esa cuenta se crea sola por dentro (sin
contraseña propia), así que no hay que darla de alta. Ve todo y no modifica nada (ver obras/middleware.py).
"""
import hmac

from django.conf import settings
from django.contrib.auth.backends import ModelBackend

from .models import Usuario


class ConsultaBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        usuario = (username or kwargs.get("correo") or "").strip().lower()
        objetivo = settings.CONSULTA_USUARIO
        if not objetivo or usuario != objetivo or password is None:
            return None
        if not hmac.compare_digest(str(password), str(settings.CONSULTA_PASSWORD)):
            return None
        cuenta, creada = Usuario.objects.get_or_create(
            correo=objetivo, defaults={"nombre": "Consulta", "apellidos": "Solo lectura", "activo": True})
        if creada:
            cuenta.set_unusable_password()
            cuenta.save(update_fields=["password"])
        return cuenta if cuenta.activo else None
