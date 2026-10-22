from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render

from .models import Contrato, Estimacion, NotaBitacora, ReporteDiario, Rol, Usuario, UsuarioProyectoRol
from .permisos import GESTIONAR_USUARIOS, VER, obras_de, puede, requiere_permiso, roles_en


def inicio(request):
    return redirect("mis_obras" if request.user.is_authenticated else "login")


def salud(request):
    """Comprobación de salud para Render: verifica la conexión a la base de datos."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"estado": "ok"})


@login_required
def mis_obras(request):
    filas = [
        {"obra": obra, "roles": sorted(roles_en(request.user, obra))}
        for obra in obras_de(request.user).order_by("nombre")
    ]
    return render(request, "mis_obras.html", {"filas": filas})


@requiere_permiso(VER)
def obra_detalle(request, obra):
    equipo = (
        UsuarioProyectoRol.objects.filter(obra=obra)
        .select_related("usuario", "rol")
        .order_by("usuario__nombre", "rol__nombre")
    )
    contexto = {
        "obra": obra,
        "contrato": Contrato.objects.filter(obra=obra).first(),
        "equipo": equipo,
        "mis_roles": sorted(roles_en(request.user, obra)),
        "n_estimaciones": Estimacion.objects.filter(contrato__obra=obra).count(),
        "n_reportes": ReporteDiario.objects.filter(obra=obra).count(),
        "n_notas": NotaBitacora.objects.filter(obra=obra).count(),
        "puede_gestionar": puede(request.user, obra, GESTIONAR_USUARIOS),
    }
    return render(request, "obra_detalle.html", contexto)


@requiere_permiso(GESTIONAR_USUARIOS)
def roles_obra(request, obra):
    if request.method == "POST":
        accion = request.POST.get("accion")
        usuario = Usuario.objects.filter(pk=request.POST.get("usuario_id") or None).first()
        rol = Rol.objects.filter(pk=request.POST.get("rol_id") or None).first()
        if not usuario or not rol:
            messages.error(request, "Selecciona un usuario y un rol válidos.")
        elif accion == "agregar":
            _, creado = UsuarioProyectoRol.objects.get_or_create(usuario=usuario, obra=obra, rol=rol)
            if creado:
                messages.success(request, f"Se asignó «{rol.nombre}» a {usuario}.")
            else:
                messages.info(request, f"{usuario} ya tenía el rol «{rol.nombre}».")
        elif accion == "quitar":
            se_quita_a_si_mismo = usuario.pk == request.user.pk and rol.nombre == "Administrador"
            if se_quita_a_si_mismo and not request.user.es_admin_panel:
                messages.error(request, "No puedes quitarte tu propio rol de Administrador.")
            else:
                UsuarioProyectoRol.objects.filter(usuario=usuario, obra=obra, rol=rol).delete()
                messages.success(request, f"Se quitó «{rol.nombre}» a {usuario}.")
        return redirect("roles_obra", obra_id=obra.pk)

    contexto = {
        "obra": obra,
        "equipo": UsuarioProyectoRol.objects.filter(obra=obra)
        .select_related("usuario", "rol")
        .order_by("usuario__nombre", "rol__nombre"),
        "usuarios": Usuario.objects.filter(activo=True).order_by("nombre", "apellidos"),
        "roles": Rol.objects.order_by("nombre"),
    }
    return render(request, "roles_obra.html", contexto)
