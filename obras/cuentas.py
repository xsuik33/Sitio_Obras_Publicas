"""Reglas y utilidades de administración de cuentas (CU-13)."""
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType

from .models import Obra, Usuario, UsuarioProyectoRol

ADMINISTRADOR = "Administrador"
# Roles que se pueden elegir al crear una cuenta (el Administrador se asigna desde la ficha de la cuenta).
ROLES_CUENTA = ("Superintendente Contratista", "Supervisor", "Residente", "Directivo")


def puede_administrar_cuentas(usuario):
    """Administrador del sistema (ADMIN_EMAILS) o con el rol Administrador en alguna obra."""
    if not usuario.is_authenticated or not usuario.is_active:
        return False
    if usuario.es_admin_panel:
        return True
    return UsuarioProyectoRol.objects.filter(usuario=usuario, rol__nombre=ADMINISTRADOR).exists()


def obras_administradas(usuario):
    """Obras en las que la persona puede asignar roles."""
    if usuario.es_admin_panel:
        return Obra.objects.all()
    return Obra.objects.filter(
        usuarioproyectorol__usuario=usuario, usuarioproyectorol__rol__nombre=ADMINISTRADOR
    ).distinct()


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
