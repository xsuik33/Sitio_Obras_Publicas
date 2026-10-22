from django.apps import AppConfig


class ObrasConfig(AppConfig):
    name = "obras"
    verbose_name = "Sistema de obras"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # La tabla `usuarios` no tiene columna last_login: se desactiva su actualización.
        from django.contrib.auth import user_logged_in
        from django.contrib.auth.models import update_last_login

        user_logged_in.disconnect(update_last_login, dispatch_uid="update_last_login")
