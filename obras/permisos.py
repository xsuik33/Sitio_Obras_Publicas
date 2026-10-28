"""
Permisos por rol y por obra.

Un usuario tiene uno o más roles en cada obra (tabla usuarios_proyectos_roles).

Administradores:
  - El rol "Administrador" de una obra tiene todos los permisos en ESA obra (puede haber varios):
    asigna roles y corrige decisiones ya registradas.
  - Los correos de ADMIN_EMAILS son administradores de todas las obras y del panel /admin/ (donde se crean usuarios).

Para cambiar lo que puede hacer cada rol, edita PERMISOS_POR_ROL.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .models import Obra, UsuarioProyectoRol

VER = "ver"
GESTIONAR_USUARIOS = "gestionar_usuarios"
GESTIONAR_CONTRATO = "gestionar_contrato"
CREAR_ESTIMACION = "crear_estimacion"
VALIDAR_ESTIMACION = "validar_estimacion"
CREAR_REPORTE = "crear_reporte"
VALIDAR_REPORTE = "validar_reporte"
CREAR_NOTA = "crear_nota"
SUBIR_DOCUMENTO = "subir_documento"

TODOS = {VER, GESTIONAR_USUARIOS, GESTIONAR_CONTRATO, CREAR_ESTIMACION, VALIDAR_ESTIMACION,
         CREAR_REPORTE, VALIDAR_REPORTE, CREAR_NOTA, SUBIR_DOCUMENTO}

PERMISOS_POR_ROL = {
    "Administrador": set(TODOS),
    "Residente": {VER, VALIDAR_ESTIMACION, VALIDAR_REPORTE, CREAR_NOTA, SUBIR_DOCUMENTO},
    "Supervisor": {VER, CREAR_REPORTE, CREAR_NOTA, SUBIR_DOCUMENTO},
    "Superintendente Contratista": {VER, CREAR_ESTIMACION, VALIDAR_REPORTE, CREAR_NOTA, SUBIR_DOCUMENTO},
    "Directivo": {VER},
}


def roles_en(usuario, obra):
    """Nombres de los roles que tiene el usuario en la obra."""
    return set(
        UsuarioProyectoRol.objects.filter(usuario=usuario, obra=obra).values_list("rol__nombre", flat=True)
    )


def puede(usuario, obra, permiso):
    if not usuario.is_authenticated or not usuario.is_active:
        return False
    if usuario.es_admin_panel:
        return True
    return any(permiso in PERMISOS_POR_ROL.get(rol, ()) for rol in roles_en(usuario, obra))


def es_administrador(usuario, obra):
    """Administrador de la obra (rol Administrador) o del sistema (ADMIN_EMAILS)."""
    if not usuario.is_authenticated or not usuario.is_active:
        return False
    return usuario.es_admin_panel or "Administrador" in roles_en(usuario, obra)


def obras_de(usuario):
    """Obras a las que el usuario tiene acceso."""
    if usuario.es_admin_panel:
        return Obra.objects.all()
    return Obra.objects.filter(usuarioproyectorol__usuario=usuario).distinct()


def requiere_permiso(permiso):
    """Decorador: la vista recibe (request, obra) y exige sesión y el permiso en esa obra."""
    def decorador(vista):
        @wraps(vista)
        def envoltura(request, obra_id, *args, **kwargs):
            obra = get_object_or_404(Obra, pk=obra_id)
            if not puede(request.user, obra, permiso):
                raise PermissionDenied
            return vista(request, obra, *args, **kwargs)
        return login_required(envoltura)
    return decorador
