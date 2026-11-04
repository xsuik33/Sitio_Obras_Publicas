from collections import defaultdict
from functools import wraps

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from .cuentas import (
    ADMINISTRADOR, historial_de, obras_administradas, puede_administrar_cuentas, registrar_accion,
)
from .forms import (
    AsignacionForm, CuentaEdicionForm, CuentaNuevaForm, CuentaPasswordForm, ReporteDiarioForm, ValidacionForm,
)
from .models import (
    CatalogoEstatusValidacion, Contrato, Estimacion, NotaBitacora, ReporteDiario, Rol, Usuario,
    UsuarioProyectoRol, ValidacionReporteDiario,
)
from .permisos import (
    CREAR_REPORTE, GESTIONAR_USUARIOS, VALIDAR_REPORTE, VER, es_administrador, obras_de, puede,
    requiere_permiso, roles_en,
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
    es_admin = es_administrador(request.user, obra)
    tiene_permiso = puede(request.user, obra, VALIDAR_REPORTE)
    # Quien no es administrador decide una sola vez: solo mientras el reporte siga Pendiente.
    puede_validar = tiene_permiso and (es_admin or reporte.estatus.nombre == "Pendiente")
    contexto = {
        "obra": obra,
        "reporte": reporte,
        "validaciones": reporte.validacionreportediario_set.select_related("usuario_validador", "estatus")
        .order_by("-fecha_validacion"),
        "evidencias": reporte.evidencia_set.order_by("fecha_subida"),
        "puede_editar": _puede_editar_reporte(request.user, obra, reporte),
        "es_admin": es_admin,
        "puede_validar": puede_validar,
        "revision_bloqueada": tiene_permiso and not puede_validar,
        "form_validacion": ValidacionForm(es_admin=es_admin) if puede_validar else None,
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
    get_object_or_404(ReporteDiario, pk=reporte_id, obra=obra)
    es_admin = es_administrador(request.user, obra)
    if request.method == "POST":
        form = ValidacionForm(request.POST, es_admin=es_admin)
        if form.is_valid():
            with transaction.atomic():
                # Bloquea la fila para que dos personas no decidan a la vez.
                reporte = (
                    ReporteDiario.objects.select_for_update(of=("self",))
                    .select_related("estatus")
                    .get(pk=reporte_id, obra=obra)
                )
                if not es_admin and reporte.estatus.nombre != "Pendiente":
                    messages.error(
                        request,
                        "Este reporte ya fue revisado. Solo el administrador puede cambiar su estatus.",
                    )
                else:
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
    return redirect("reporte_detalle", obra_id=obra.pk, reporte_id=reporte_id)


# ---------------------------------------------------------------------------
# Cuentas de usuario (CU-13)
# ---------------------------------------------------------------------------

def _gestiona_cuentas(vista):
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not puede_administrar_cuentas(request.user):
            raise PermissionDenied
        return vista(request, *args, **kwargs)
    return login_required(envoltura)


@_gestiona_cuentas
def usuarios_lista(request):
    q = (request.GET.get("q") or "").strip()
    estado = request.GET.get("estado", "")
    usuarios = Usuario.objects.all()
    if q:
        usuarios = usuarios.filter(Q(nombre__icontains=q) | Q(apellidos__icontains=q) | Q(correo__icontains=q))
    if estado == "activos":
        usuarios = usuarios.filter(activo=True)
    elif estado == "inactivos":
        usuarios = usuarios.filter(activo=False)
    usuarios = list(usuarios.order_by("nombre", "apellidos"))
    roles = defaultdict(list)
    for a in UsuarioProyectoRol.objects.filter(usuario__in=usuarios).select_related("obra", "rol"):
        roles[a.usuario_id].append(f"{a.rol.nombre} · {a.obra.nombre}")
    filas = [{"usuario": u, "roles": sorted(roles[u.pk])} for u in usuarios]
    return render(request, "usuarios_lista.html", {"filas": filas, "q": q, "estado": estado})


@_gestiona_cuentas
def usuario_nuevo(request):
    form = CuentaNuevaForm(request.POST or None, obras=obras_administradas(request.user))
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        with transaction.atomic():
            usuario = Usuario.objects.create_user(
                datos["correo"], datos["password1"],
                nombre=datos["nombre"].strip(), apellidos=datos["apellidos"].strip(),
            )
            for obra in datos["obras"]:
                UsuarioProyectoRol.objects.get_or_create(usuario=usuario, obra=obra, rol=datos["rol"])
            obras = ", ".join(o.nombre for o in datos["obras"]) or "ninguna"
            registrar_accion(
                request.user, usuario, ADDITION, f"Cuenta creada con rol «{datos['rol'].nombre}». Obras: {obras}."
            )
        messages.success(request, f"Cuenta creada para {usuario}.")
        return redirect("usuario_detalle", usuario_id=usuario.pk)
    contexto = {"titulo": "Nueva cuenta", "form": form, "volver_url": reverse_lazy("usuarios_lista"),
                "volver_texto": "Usuarios", "boton": "Crear cuenta"}
    return render(request, "form_generico.html", contexto)


@_gestiona_cuentas
def usuario_detalle(request, usuario_id):
    usuario = get_object_or_404(Usuario, pk=usuario_id)
    if usuario.es_admin_panel and not request.user.es_admin_panel:
        raise PermissionDenied  # solo un administrador del sistema modifica a otro administrador del sistema
    obras = obras_administradas(request.user)
    form_datos = CuentaEdicionForm(
        initial={"nombre": usuario.nombre, "apellidos": usuario.apellidos, "correo": usuario.correo,
                 "activo": usuario.activo},
        usuario=usuario,
    )
    form_password = CuentaPasswordForm(usuario=usuario)
    form_rol = AsignacionForm(obras=obras)

    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "datos":
            form_datos = CuentaEdicionForm(request.POST, usuario=usuario)
            if form_datos.is_valid():
                d = form_datos.cleaned_data
                if usuario.pk == request.user.pk and not d["activo"]:
                    form_datos.add_error("activo", "No puedes desactivar tu propia cuenta.")
                else:
                    cambios = [
                        etiqueta
                        for campo, etiqueta in (("nombre", "nombre"), ("apellidos", "apellidos"),
                                                ("correo", "correo"), ("activo", "estado"))
                        if getattr(usuario, campo) != (d[campo].strip() if isinstance(d[campo], str) else d[campo])
                    ]
                    activo_antes = usuario.activo
                    usuario.nombre, usuario.apellidos = d["nombre"].strip(), d["apellidos"].strip()
                    usuario.correo, usuario.activo = d["correo"], d["activo"]
                    usuario.save(update_fields=["nombre", "apellidos", "correo", "activo"])
                    if cambios:
                        mensaje = "Datos actualizados: " + ", ".join(cambios) + "."
                        if activo_antes != usuario.activo:
                            mensaje += " Cuenta " + ("reactivada." if usuario.activo else "desactivada.")
                        registrar_accion(request.user, usuario, CHANGE, mensaje)
                    messages.success(request, "Datos guardados.")
                    return redirect("usuario_detalle", usuario_id=usuario.pk)
        elif accion == "password":
            form_password = CuentaPasswordForm(request.POST, usuario=usuario)
            if form_password.is_valid():
                usuario.set_password(form_password.cleaned_data["password1"])
                usuario.save(update_fields=["password"])
                registrar_accion(request.user, usuario, CHANGE, "Contraseña restablecida.")
                messages.success(request, "Contraseña restablecida.")
                return redirect("usuario_detalle", usuario_id=usuario.pk)
        elif accion == "agregar_rol":
            form_rol = AsignacionForm(request.POST, obras=obras)
            if form_rol.is_valid():
                obra, rol = form_rol.cleaned_data["obra"], form_rol.cleaned_data["rol"]
                _, creado = UsuarioProyectoRol.objects.get_or_create(usuario=usuario, obra=obra, rol=rol)
                if creado:
                    registrar_accion(
                        request.user, usuario, CHANGE, f"Rol «{rol.nombre}» asignado en la obra «{obra.nombre}»."
                    )
                    messages.success(request, f"Se asignó «{rol.nombre}» en «{obra.nombre}».")
                else:
                    messages.info(request, "Esa asignación ya existía.")
                return redirect("usuario_detalle", usuario_id=usuario.pk)
        elif accion == "quitar_rol":
            asignacion = (
                UsuarioProyectoRol.objects.filter(
                    usuario=usuario, obra__in=obras,
                    obra_id=request.POST.get("obra_id") or None, rol_id=request.POST.get("rol_id") or None,
                )
                .select_related("obra", "rol")
                .first()
            )
            if not asignacion:
                messages.error(request, "Esa asignación no existe o no la administras.")
            elif (usuario.pk == request.user.pk and asignacion.rol.nombre == ADMINISTRADOR
                  and not request.user.es_admin_panel):
                messages.error(request, "No puedes quitarte tu propio rol de Administrador.")
            else:
                UsuarioProyectoRol.objects.filter(usuario=usuario, obra=asignacion.obra, rol=asignacion.rol).delete()
                registrar_accion(
                    request.user, usuario, CHANGE,
                    f"Rol «{asignacion.rol.nombre}» retirado de la obra «{asignacion.obra.nombre}».",
                )
                messages.success(request, f"Se retiró «{asignacion.rol.nombre}» de «{asignacion.obra.nombre}».")
            return redirect("usuario_detalle", usuario_id=usuario.pk)

    contexto = {
        "usuario": usuario,
        "form_datos": form_datos,
        "form_password": form_password,
        "form_rol": form_rol,
        "asignaciones": UsuarioProyectoRol.objects.filter(usuario=usuario, obra__in=obras)
        .select_related("obra", "rol").order_by("obra__nombre", "rol__nombre"),
        "historial": historial_de(usuario)[:30],
    }
    return render(request, "usuario_detalle.html", contexto)


class CambiarPasswordView(SuccessMessageMixin, auth_views.PasswordChangeView):
    template_name = "cuenta_password.html"
    success_url = reverse_lazy("mis_obras")
    success_message = "Contraseña actualizada."
