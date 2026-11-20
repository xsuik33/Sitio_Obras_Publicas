"""Reglas y utilidades de administración de cuentas (CU-13)."""
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType

from .models import Obra, Usuario, UsuarioProyectoRol

ADMINISTRADOR = "Administrador"
# Roles que se asignan en una obra (uno por persona en cada obra). El administrador es el del sistema (ADMIN_EMAILS).
ROLES_CUENTA = ("Superintendente Contratista", "Supervisor", "Residente", "Directivo")


def puede_administrar_cuentas(usuario):
    """Solo el administrador del sistema (ADMIN_EMAILS); ya no existe un administrador por obra."""
    return bool(usuario.is_authenticated and usuario.is_active and usuario.es_admin_panel)


def obras_administradas(usuario):
    """Obras en las que la persona puede asignar roles."""
    return Obra.objects.all() if puede_administrar_cuentas(usuario) else Obra.objects.none()


def registrar_accion(actor, usuario, bandera, mensaje):
    """Deja constancia en el historial (tabla django_admin_log, visible también en /admin/)."""
    LogEntry.objects.log_actions(
        actor.pk, Usuario.objects.filter(pk=usuario.pk), bandera, change_message=mensaje, single_object=True
    )


def historial_de(usuario):
    tipo = ContentType.objects.get_for_model(Usuario)
    return (
        LogEntry.objects.filter(content_type=tipo, object_id=str(usuario.pk))
        .select_related("user")
        .order_by("-action_time")
    )


def rol_actual(usuario, obra):
    """Rol que la persona ya tiene en la obra (cada persona tiene a lo más uno por obra), o None."""
    a = UsuarioProyectoRol.objects.filter(usuario=usuario, obra=obra).select_related("rol").first()
    return a.rol if a else None


def ocupante(obra, rol, excepto=None):
    """Persona que ya tiene ese rol en la obra (cada rol lo tiene a lo más una persona por obra), o None."""
    q = UsuarioProyectoRol.objects.filter(obra=obra, rol=rol).select_related("usuario")
    if excepto is not None:
        q = q.exclude(usuario=excepto)
    a = q.first()
    return a.usuario if a else None
