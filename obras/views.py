from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import ReporteDiarioForm, ValidacionForm
from .models import (
    CatalogoEstatusValidacion, Contrato, Estimacion, NotaBitacora, ReporteDiario, Rol, Usuario,
    UsuarioProyectoRol, ValidacionReporteDiario,
)
from .permisos import (
    CREAR_REPORTE, GESTIONAR_USUARIOS, VALIDAR_REPORTE, VER, obras_de, puede, requiere_permiso, roles_en,
)


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


# ---------------------------------------------------------------------------
# Reportes diarios
# ---------------------------------------------------------------------------

def _puede_editar_reporte(usuario, obra, reporte):
    """Edita el autor (o un administrador de la obra) mientras el reporte no esté aprobado."""
    if reporte.estatus.nombre == "Aprobado" or not puede(usuario, obra, CREAR_REPORTE):
        return False
    return reporte.supervisor_id == usuario.pk or puede(usuario, obra, GESTIONAR_USUARIOS)


@requiere_permiso(VER)
def reportes_lista(request, obra):
    reportes = (
        ReporteDiario.objects.filter(obra=obra)
        .select_related("supervisor", "estatus")
        .order_by("-fecha_reporte")
    )
    contexto = {"obra": obra, "reportes": reportes, "puede_crear": puede(request.user, obra, CREAR_REPORTE)}
    return render(request, "reportes_lista.html", contexto)


@requiere_permiso(CREAR_REPORTE)
def reporte_nuevo(request, obra):
    form = ReporteDiarioForm(request.POST or None, obra=obra)
    if request.method == "POST" and form.is_valid():
        reporte = form.save(commit=False)
        reporte.obra = obra
        reporte.supervisor = request.user
        reporte.save()
        messages.success(request, "Reporte diario registrado.")
        return redirect("reporte_detalle", obra_id=obra.pk, reporte_id=reporte.pk)
    return render(request, "reporte_form.html", {"obra": obra, "form": form, "titulo": "Nuevo reporte diario"})


@requiere_permiso(VER)
def reporte_detalle(request, obra, reporte_id):
    reporte = get_object_or_404(
        ReporteDiario.objects.select_related("supervisor", "estatus"), pk=reporte_id, obra=obra
    )
    puede_validar = puede(request.user, obra, VALIDAR_REPORTE)
    contexto = {
        "obra": obra,
        "reporte": reporte,
        "validaciones": reporte.validacionreportediario_set.select_related("usuario_validador", "estatus")
        .order_by("-fecha_validacion"),
        "evidencias": reporte.evidencia_set.order_by("fecha_subida"),
        "puede_editar": _puede_editar_reporte(request.user, obra, reporte),
        "puede_validar": puede_validar,
        "form_validacion": ValidacionForm() if puede_validar else None,
    }
    return render(request, "reporte_detalle.html", contexto)


@requiere_permiso(CREAR_REPORTE)
def reporte_editar(request, obra, reporte_id):
    reporte = get_object_or_404(
        ReporteDiario.objects.select_related("supervisor", "estatus"), pk=reporte_id, obra=obra
    )
    if not _puede_editar_reporte(request.user, obra, reporte):
        raise PermissionDenied
    form = ReporteDiarioForm(request.POST or None, instance=reporte, obra=obra)
    if request.method == "POST" and form.is_valid():
        reporte = form.save()
        if reporte.estatus.nombre == "Rechazado":  # al corregirlo, vuelve a revisión
            reporte.estatus = CatalogoEstatusValidacion.objects.get(nombre="Pendiente")
            reporte.save(update_fields=["estatus"])
        messages.success(request, "Reporte actualizado.")
        return redirect("reporte_detalle", obra_id=obra.pk, reporte_id=reporte.pk)
    return render(request, "reporte_form.html", {"obra": obra, "form": form, "titulo": "Editar reporte diario"})


@requiere_permiso(VALIDAR_REPORTE)
def reporte_validar(request, obra, reporte_id):
    reporte = get_object_or_404(ReporteDiario, pk=reporte_id, obra=obra)
    if request.method == "POST":
        form = ValidacionForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                ValidacionReporteDiario.objects.create(
                    reporte_diario=reporte,
                    usuario_validador=request.user,
                    estatus=form.cleaned_data["estatus"],
                    comentarios=form.cleaned_data["comentarios"] or None,
                )
                reporte.estatus = form.cleaned_data["estatus"]
                reporte.save(update_fields=["estatus"])
            messages.success(request, f"Reporte marcado como «{reporte.estatus.nombre}».")
        else:
            messages.error(request, " ".join(e for errores in form.errors.values() for e in errores))
    return redirect("reporte_detalle", obra_id=obra.pk, reporte_id=reporte.pk)
