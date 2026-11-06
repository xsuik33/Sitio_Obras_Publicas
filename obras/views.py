from collections import defaultdict
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError, Q, Sum
from django.forms import formset_factory
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from . import pdf, reportes
from .cuentas import (
    ADMINISTRADOR, historial_de, obras_administradas, puede_administrar_cuentas, registrar_accion,
)
from .forms import (
    AsignacionForm, BaseConceptoFormSet, ConceptoForm, CuentaEdicionForm, CuentaNuevaForm, CuentaPasswordForm,
    BaseProgramaFormSet, ProgramaForm, ReporteDiarioForm, ValidacionForm,
)
from .models import (
    CatalogoConcepto, CatalogoEstatusValidacion, Contrato, Estimacion, EstimacionDetalle, NotaBitacora,
    ProgramaObra, ReporteDiario, Rol, Usuario, UsuarioProyectoRol,
)
from .permisos import (
    CREAR_REPORTE, GESTIONAR_CATALOGO, GESTIONAR_PROGRAMA, GESTIONAR_USUARIOS, VALIDAR_REPORTE, VER, es_administrador, obras_de,
    puede, requiere_permiso, roles_en,
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
        "n_conceptos": CatalogoConcepto.objects.filter(obra=obra).count(),
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
# Reportes diarios (CU-03)
# ---------------------------------------------------------------------------

def _puede_editar_reporte(usuario, obra, reporte):
    """Edita el autor (o un administrador de la obra) mientras el reporte no esté aprobado."""
    if reporte.estatus.nombre == reportes.APROBADO or not puede(usuario, obra, CREAR_REPORTE):
        return False
    return reporte.supervisor_id == usuario.pk or puede(usuario, obra, GESTIONAR_USUARIOS)


def _puede_ver_reporte(usuario, obra, reporte):
    """Un borrador solo lo ven su autor y los administradores."""
    if reporte.estatus.nombre != reportes.BORRADOR:
        return True
    return reporte.supervisor_id == usuario.pk or es_administrador(usuario, obra)


def _reporte_visible(usuario, obra, reporte_id):
    reporte = get_object_or_404(
        ReporteDiario.objects.select_related("supervisor", "estatus", "obra"), pk=reporte_id, obra=obra
    )
    if not _puede_ver_reporte(usuario, obra, reporte):
        raise PermissionDenied
    return reporte


@requiere_permiso(VER)
def reportes_lista(request, obra):
    consulta = ReporteDiario.objects.filter(obra=obra).select_related("supervisor", "estatus", "obra")
    if not es_administrador(request.user, obra):
        consulta = consulta.exclude(Q(estatus__nombre=reportes.BORRADOR) & ~Q(supervisor=request.user))
    filas = [(r, reportes.estado_legible(r)) for r in consulta.order_by("-fecha_reporte")]
    contexto = {"obra": obra, "filas": filas, "puede_crear": puede(request.user, obra, CREAR_REPORTE)}
    return render(request, "reportes_lista.html", contexto)


@requiere_permiso(CREAR_REPORTE)
def reporte_nuevo(request, obra):
    form = ReporteDiarioForm(request.POST or None, obra=obra)
    if request.method == "POST" and form.is_valid():
        enviar = request.POST.get("accion") == "enviar"
        with transaction.atomic():
            reporte = form.save(commit=False)
            reporte.obra = obra
            reporte.supervisor = request.user
            reporte.estatus = reportes.estatus(reportes.PENDIENTE if enviar else reportes.BORRADOR)
            reporte.save()
        if enviar:
            reportes.enviar_a_revision(reporte, request.user)
            messages.success(request, "Reporte enviado a revisión del Superintendente y del Residente.")
        else:
            messages.success(request, "Borrador guardado. Envíalo a revisión cuando esté listo.")
        return redirect("reporte_detalle", obra_id=obra.pk, reporte_id=reporte.pk)
    return render(request, "reporte_form.html", {"obra": obra, "form": form, "titulo": "Nuevo reporte diario", "nuevo": True})


@requiere_permiso(VER)
def reporte_detalle(request, obra, reporte_id):
    reporte = _reporte_visible(request.user, obra, reporte_id)
    es_admin = es_administrador(request.user, obra)
    decs = reportes.decisiones(reporte)
    roles = reportes.roles_que_decide(request.user, obra, reporte, decs, es_admin)
    puede_validar = puede(request.user, obra, VALIDAR_REPORTE) and bool(roles)
    contexto = {
        "obra": obra,
        "reporte": reporte,
        "estado": reportes.estado_legible(reporte, decs),
        "decisiones": [(reportes.ETIQUETA[r], d) for r, d in decs.items()],
        "validaciones": reporte.validacionreportediario_set.select_related("usuario_validador", "estatus")
        .order_by("-fecha_validacion"),
        "evidencias": reporte.evidencia_set.order_by("fecha_subida"),
        "puede_editar": _puede_editar_reporte(request.user, obra, reporte),
        "es_borrador": reporte.estatus.nombre == reportes.BORRADOR,
        "es_admin": es_admin,
        "puede_validar": puede_validar,
        "revision_bloqueada": puede(request.user, obra, VALIDAR_REPORTE) and not puede_validar
        and reporte.estatus.nombre != reportes.BORRADOR,
        "form_validacion": ValidacionForm(es_admin=es_admin, roles=roles) if puede_validar else None,
        "desviacion": reportes.desviacion(reporte),
        "financiero": reportes.resumen_financiero(reporte),
    }
    return render(request, "reporte_detalle.html", contexto)


@requiere_permiso(CREAR_REPORTE)
def reporte_editar(request, obra, reporte_id):
    reporte = _reporte_visible(request.user, obra, reporte_id)
    if not _puede_editar_reporte(request.user, obra, reporte):
        raise PermissionDenied
    era_borrador = reporte.estatus.nombre == reportes.BORRADOR
    form = ReporteDiarioForm(request.POST or None, instance=reporte, obra=obra)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            reporte = form.save()
        previo = reporte.estatus.nombre
        if era_borrador and request.POST.get("accion") != "enviar":
            messages.success(request, "Borrador actualizado.")
        elif era_borrador:
            reportes.enviar_a_revision(reporte, request.user)
            messages.success(request, "Reporte enviado a revisión del Superintendente y del Residente.")
        elif previo in (reportes.RECHAZADO, reportes.EN_REVISION):  # cambió después de decisiones: se revisa de nuevo
            reportes.enviar_a_revision(reporte, request.user)
            messages.success(request, "Reporte corregido y enviado de nuevo a revisión.")
        else:
            messages.success(request, "Reporte actualizado.")
        return redirect("reporte_detalle", obra_id=obra.pk, reporte_id=reporte.pk)
    return render(
        request, "reporte_form.html",
        {"obra": obra, "form": form, "titulo": "Editar reporte diario", "nuevo": False, "es_borrador": era_borrador},
    )


@requiere_permiso(VER)
def reporte_pdf(request, obra, reporte_id):
    reporte = _reporte_visible(request.user, obra, reporte_id)
    decs = reportes.decisiones(reporte)
    contenido = pdf.reporte_diario_pdf(
        reporte, reportes.estado_legible(reporte, decs), reportes.desviacion(reporte),
        reportes.resumen_financiero(reporte),
        list(reporte.validacionreportediario_set.select_related("usuario_validador", "estatus")
             .order_by("fecha_validacion")),
    )
    respuesta = HttpResponse(contenido, content_type="application/pdf")
    respuesta["Content-Disposition"] = f'inline; filename="reporte-diario-{reporte.fecha_reporte:%Y-%m-%d}.pdf"'
    return respuesta


@requiere_permiso(VALIDAR_REPORTE)
def reporte_validar(request, obra, reporte_id):
    _reporte_visible(request.user, obra, reporte_id)
    es_admin = es_administrador(request.user, obra)
    if request.method == "POST":
        with transaction.atomic():
            # Bloquea la fila para que dos personas no decidan a la vez.
            reporte = (
                ReporteDiario.objects.select_for_update(of=("self",))
                .select_related("estatus", "supervisor", "obra")
                .get(pk=reporte_id, obra=obra)
            )
            roles = reportes.roles_que_decide(request.user, obra, reporte, es_admin=es_admin)
            form = ValidacionForm(request.POST, es_admin=es_admin, roles=roles)
            if not roles:
                messages.error(
                    request,
                    "Ya no puedes decidir sobre este reporte. Si hay un error, solo el administrador puede corregirlo.",
                )
            elif form.is_valid():
                reportes.registrar_decision(
                    reporte, request.user, form.cleaned_data["rol"], form.cleaned_data["estatus"],
                    form.cleaned_data["comentarios"],
                )
                messages.success(request, f"Decisión registrada. Estado: {reportes.estado_legible(reporte)}.")
            else:
                messages.error(request, " ".join(e for errores in form.errors.values() for e in errores))
    return redirect("reporte_detalle", obra_id=obra.pk, reporte_id=reporte_id)


@login_required
def pendientes(request):
    revisar, corregir = reportes.pendientes_de(request.user)
    return render(request, "pendientes.html", {"revisar": revisar, "corregir": corregir})


# ---------------------------------------------------------------------------
# Programa de obra (CU-03)
# ---------------------------------------------------------------------------

@requiere_permiso(VER)
def programa_ver(request, obra):
    puntos = list(ProgramaObra.objects.filter(obra=obra).order_by("fecha"))
    contexto = {"obra": obra, "puntos": puntos, "puede_editar": puede(request.user, obra, GESTIONAR_PROGRAMA)}
    return render(request, "programa.html", contexto)


@requiere_permiso(GESTIONAR_PROGRAMA)
def programa_editar(request, obra):
    existentes = list(ProgramaObra.objects.filter(obra=obra).order_by("fecha"))
    inicial = [{"fecha": p.fecha, "avance": p.avance_programado_porcentaje} for p in existentes]
    ProgramaFormSet = formset_factory(
        ProgramaForm, formset=BaseProgramaFormSet, extra=3 if existentes else 6, can_delete=True
    )
    if request.method == "POST":
        formset = ProgramaFormSet(request.POST, initial=inicial)
        if formset.is_valid():
            filas = [f.cleaned_data for f in formset.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
            with transaction.atomic():
                ProgramaObra.objects.filter(obra=obra).delete()
                ProgramaObra.objects.bulk_create(
                    [ProgramaObra(obra=obra, fecha=d["fecha"], avance_programado_porcentaje=d["avance"]) for d in filas]
                )
            messages.success(request, f"Programa guardado: {len(filas)} fecha(s).")
            return redirect("programa_ver", obra_id=obra.pk)
    else:
        formset = ProgramaFormSet(initial=inicial)
    return render(request, "programa_editar.html", {"obra": obra, "formset": formset})


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


# ---------------------------------------------------------------------------
# Catálogo de conceptos (CU-01)
# ---------------------------------------------------------------------------

def catalogo_en_uso(obra):
    """El catálogo está en uso cuando alguna estimación ya usa sus conceptos."""
    return EstimacionDetalle.objects.filter(concepto__obra=obra).exists()


def _conceptos(obra):
    return list(CatalogoConcepto.objects.filter(obra=obra).order_by("clave"))


@requiere_permiso(VER)
def catalogo_ver(request, obra):
    contrato = Contrato.objects.filter(obra=obra).first()
    conceptos = _conceptos(obra)
    total = sum((c.importe_contratado for c in conceptos), Decimal("0"))
    en_uso = catalogo_en_uso(obra)
    contexto = {
        "obra": obra,
        "contrato": contrato,
        "conceptos": conceptos,
        "total": total,
        "diferencia": (contrato.monto_total_contratado - total) if contrato else None,
        "en_uso": en_uso,
        "puede_editar": bool(contrato) and not en_uso and puede(request.user, obra, GESTIONAR_CATALOGO),
    }
    return render(request, "catalogo.html", contexto)


@requiere_permiso(GESTIONAR_CATALOGO)
def catalogo_editar(request, obra):
    contrato = Contrato.objects.filter(obra=obra).first()
    if not contrato:
        messages.error(request, "La obra todavía no tiene contrato registrado; no se puede capturar el catálogo.")
        return redirect("catalogo_ver", obra_id=obra.pk)
    if catalogo_en_uso(obra):
        messages.error(
            request,
            "El catálogo ya está en uso en estimaciones. Los cambios requieren autorización "
            "(supervisor y residente) y aún no se pueden hacer desde aquí.",
        )
        return redirect("catalogo_ver", obra_id=obra.pk)

    existentes = _conceptos(obra)
    inicial = [
        {"id": c.pk, "capitulo": c.capitulo or "", "clave": c.clave, "descripcion": c.descripcion,
         "unidad_medida": c.unidad_medida, "volumen_total_contratado": c.volumen_total_contratado,
         "precio_unitario": c.precio_unitario}
        for c in existentes
    ]
    CatalogoFormSet = formset_factory(
        ConceptoForm, formset=BaseConceptoFormSet, extra=3 if existentes else 5, can_delete=True
    )
    if request.method == "POST":
        formset = CatalogoFormSet(request.POST, initial=inicial)
        if formset.is_valid():
            finales = [f.cleaned_data for f in formset.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
            propios = {c.pk: c for c in existentes}
            if any(d.get("id") and d["id"] not in propios for d in finales):
                messages.error(request, "El catálogo cambió mientras lo editabas. Vuelve a abrirlo.")
                return redirect("catalogo_editar", obra_id=obra.pk)
            try:
                with transaction.atomic():
                    if catalogo_en_uso(obra):
                        raise ProtectedError("en uso", [])
                    ids_finales = {d["id"] for d in finales if d.get("id")}
                    CatalogoConcepto.objects.filter(obra=obra).exclude(pk__in=ids_finales).delete()
                    cambios = [(propios[d["id"]], d) for d in finales if d.get("id")]
                    # Primero claves provisionales: permite intercambiar o reasignar claves sin chocar.
                    for concepto, d in cambios:
                        if concepto.clave != d["clave"]:
                            CatalogoConcepto.objects.filter(pk=concepto.pk).update(clave=f"~{concepto.pk}")
                    for concepto, d in cambios:
                        concepto.capitulo = d["capitulo"] or None
                        concepto.clave, concepto.descripcion = d["clave"], d["descripcion"]
                        concepto.unidad_medida = d["unidad_medida"]
                        concepto.volumen_total_contratado = d["volumen_total_contratado"]
                        concepto.precio_unitario = d["precio_unitario"]
                        concepto.save(update_fields=[
                            "capitulo", "clave", "descripcion", "unidad_medida",
                            "volumen_total_contratado", "precio_unitario",
                        ])
                    for d in (d for d in finales if not d.get("id")):
                        CatalogoConcepto.objects.create(
                            obra=obra, capitulo=d["capitulo"] or None, clave=d["clave"], descripcion=d["descripcion"],
                            unidad_medida=d["unidad_medida"], volumen_total_contratado=d["volumen_total_contratado"],
                            precio_unitario=d["precio_unitario"],
                        )
            except (ProtectedError, IntegrityError):
                messages.error(request, "No se pudo guardar: el catálogo ya está en uso o una clave se repite.")
                return redirect("catalogo_ver", obra_id=obra.pk)
            conceptos = _conceptos(obra)
            total = CatalogoConcepto.objects.filter(obra=obra).aggregate(t=Sum("importe_contratado"))["t"] or Decimal("0")
            messages.success(request, f"Catálogo guardado: {len(conceptos)} concepto(s), importe total $ {total:,.2f}.")
            if total != contrato.monto_total_contratado:
                messages.warning(
                    request,
                    f"El importe del catálogo ($ {total:,.2f}) no coincide con el monto del contrato "
                    f"($ {contrato.monto_total_contratado:,.2f}). Diferencia: $ {contrato.monto_total_contratado - total:,.2f}.",
                )
            return redirect("catalogo_ver", obra_id=obra.pk)
    else:
        formset = CatalogoFormSet(initial=inicial)
    return render(request, "catalogo_editar.html", {"obra": obra, "contrato": contrato, "formset": formset})
