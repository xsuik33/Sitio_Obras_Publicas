import logging
import uuid
from collections import defaultdict
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.models import ADDITION, CHANGE
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError, Q, Sum
from django.forms import formset_factory
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone

from . import bitacora, documentos, estimaciones, evidencias, mensuales, pdf, reportes
from . import consulta as expedientes
from .almacenamiento import ErrorAlmacenamiento
from .cuentas import (
    ADMINISTRADOR, historial_de, obras_administradas, puede_administrar_cuentas, registrar_accion,
)
from .forms import (
    AsignacionForm, BaseConceptoFormSet, ConceptoForm, CuentaEdicionForm, CuentaNuevaForm, CuentaPasswordForm,
    BaseProgramaFormSet, FirmaNotaForm, NotaBitacoraForm, ProgramaForm, ReporteDiarioForm, ReporteMensualForm,
    DecisionEstimacionForm, DocumentoForm, EstimacionForm, ValidacionForm,
)
from .models import (
    CatalogoConcepto, CatalogoEstatusValidacion, CatalogoTipoNota, Contrato, Documento, Estimacion, EstimacionDetalle,
    Evidencia,
    NotaBitacora,
    ProgramaObra, ReporteDiario, ReporteMensual, Rol, Usuario, UsuarioProyectoRol,
)
from .permisos import (
    CREAR_ESTIMACION, CREAR_NOTA, CREAR_REPORTE, FIRMAR_NOTA, GESTIONAR_CATALOGO, GESTIONAR_PROGRAMA, GESTIONAR_USUARIOS,
    VALIDAR_ESTIMACION, VALIDAR_REPORTE, VER, es_administrador, obras_de,
    puede, requiere_permiso, roles_en,
)


logger = logging.getLogger(__name__)

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
    """CU-12 pasos 1-3: buscar la obra por nombre, identificador o estado y elegirla."""
    q = (request.GET.get("q") or "").strip()[:100]
    estado = request.GET.get("estado", "")
    if estado not in expedientes.ESTADOS:
        estado = ""
    obras = list(expedientes.buscar(request.user, q, estado))
    filas = [
        {"obra": obra, "id": expedientes.identificador(obra), "roles": sorted(roles_en(request.user, obra)),
         **expedientes.resumen_tarjeta(obra)}
        for obra in obras
    ]
    contexto = {
        "filas": filas, "q": q, "estado": estado, "estados": expedientes.ESTADOS, "filtrando": bool(q or estado),
        "tiene_obras": bool(filas) or obras_de(request.user).exists(),
    }
    return render(request, "mis_obras.html", contexto)


@requiere_permiso(VER)
def obra_detalle(request, obra):
    """CU-12 paso 4: expediente de la obra (solo consulta), según lo que el rol puede ver."""
    equipo = (
        UsuarioProyectoRol.objects.filter(obra=obra)
        .select_related("usuario", "rol")
        .order_by("usuario__nombre", "rol__nombre")
    )
    exp = expedientes.expediente(request.user, obra)
    contexto = {
        "obra": obra,
        "id_obra": expedientes.identificador(obra),
        "estado_obra": expedientes.estado_obra(obra),
        "equipo": equipo,
        "mis_roles": sorted(roles_en(request.user, obra)),
        "x": exp,
        "grafica": exp["avance"]["datos"]["grafica"] if exp["avance"]["ok"] else None,
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

_puede_editar_reporte = reportes.puede_editar
_puede_ver_reporte = reportes.puede_ver


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
            form.save_m2m()
        if enviar:
            reportes.enviar_a_revision(reporte, request.user)
            messages.success(request, "Reporte enviado a revisión del Superintendente y del Residente.")
        else:
            messages.success(request, "Borrador guardado. Envíalo a revisión cuando esté listo.")
        return redirect("reporte_detalle", obra_id=obra.pk, reporte_id=reporte.pk)
    return render(request, "reporte_form.html", {"obra": obra, "form": form, "titulo": "Nuevo reporte diario", "nuevo": True,
                                                 "atraso_critico_dias": reportes.ATRASO_CRITICO_DIAS})


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
        "ev": _contexto_evidencias(request.user, obra, reporte),
        "puede_editar": _puede_editar_reporte(request.user, obra, reporte),
        "es_borrador": reporte.estatus.nombre == reportes.BORRADOR,
        "es_admin": es_admin,
        "puede_validar": puede_validar,
        "revision_bloqueada": puede(request.user, obra, VALIDAR_REPORTE) and not puede_validar
        and reporte.estatus.nombre != reportes.BORRADOR,
        "form_validacion": ValidacionForm(es_admin=es_admin, roles=roles) if puede_validar else None,
        "desviacion": reportes.desviacion(reporte),
        "financiero": reportes.resumen_financiero(reporte),
        "notas": reporte.notas.select_related("tipo", "usuario_creador").order_by("folio"),
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
        {"obra": obra, "form": form, "titulo": "Editar reporte diario", "nuevo": False, "es_borrador": era_borrador,
         "atraso_critico_dias": reportes.ATRASO_CRITICO_DIAS,
         "dias_atraso": reportes.dias_atraso(obra, reporte.fecha_reporte, reporte.avance_fisico_real_porcentaje)},
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
                    por_admin=es_admin and form.cleaned_data["rol"] not in roles_en(request.user, obra),
                )
                messages.success(request, f"Decisión registrada. Estado: {reportes.estado_legible(reporte)}.")
            else:
                messages.error(request, " ".join(e for errores in form.errors.values() for e in errores))
    return redirect("reporte_detalle", obra_id=obra.pk, reporte_id=reporte_id)


@login_required
def pendientes(request):
    revisar, corregir = reportes.pendientes_de(request.user)
    notas_firmar, notas_corregir = bitacora.pendientes_de(request.user)
    mens_revisar, mens_corregir = mensuales.pendientes_de(request.user)
    est_revisar, est_corregir = estimaciones.pendientes_de(request.user)
    contexto = {"revisar": revisar, "corregir": corregir, "notas_firmar": notas_firmar,
                "notas_corregir": notas_corregir, "mens_revisar": mens_revisar, "mens_corregir": mens_corregir,
                "est_revisar": est_revisar, "est_corregir": est_corregir}
    return render(request, "pendientes.html", contexto)


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
# Reporte mensual (CU-04)
# ---------------------------------------------------------------------------

def _mensual_visible(usuario, obra, reporte_id, bloquear=False):
    consulta = ReporteMensual.objects.select_related("supervisor", "estatus", "obra")
    if bloquear:
        consulta = consulta.select_for_update(of=("self",))
    reporte = get_object_or_404(consulta, pk=reporte_id, obra=obra)
    es_borrador = reporte.estatus.nombre == reportes.BORRADOR
    if es_borrador and reporte.supervisor_id != usuario.pk and not es_administrador(usuario, obra):
        raise PermissionDenied  # un borrador solo lo ven su autor y los administradores
    return reporte


@requiere_permiso(VER)
def mensuales_lista(request, obra):
    consulta = ReporteMensual.objects.filter(obra=obra).select_related("supervisor", "estatus", "obra")
    if not es_administrador(request.user, obra):
        consulta = consulta.exclude(Q(estatus__nombre=reportes.BORRADOR) & ~Q(supervisor=request.user))
    filas = [(r, mensuales.nombre_mes(r), mensuales.estado_legible(r)) for r in consulta.order_by("-anio", "-mes")]
    contexto = {"obra": obra, "filas": filas, "puede_crear": puede(request.user, obra, CREAR_REPORTE)}
    return render(request, "mensuales_lista.html", contexto)


def _vista_previa(obra, form):
    """Consolidado del mes elegido en el formulario, para ver advertencias antes de enviar."""
    datos = form.cleaned_data if form.is_bound and form.is_valid() else None
    instancia = form.instance
    anio = (datos or {}).get("anio") or instancia.anio or form.fields["anio"].initial
    mes = (datos or {}).get("mes") or instancia.mes or form.fields["mes"].initial
    if not anio or not mes:
        return None
    corte = (datos or {}).get("fecha_corte") or instancia.fecha_corte or mensuales.ultimo_dia(anio, mes)
    return mensuales.consolidar(ReporteMensual(obra=obra, anio=anio, mes=mes, fecha_corte=corte))


@requiere_permiso(CREAR_REPORTE)
def mensual_nuevo(request, obra):
    form = ReporteMensualForm(request.POST or None, obra=obra)
    if request.method == "POST" and form.is_valid():
        enviar = request.POST.get("accion") == "enviar"
        with transaction.atomic():
            reporte = form.save(commit=False)
            reporte.obra, reporte.supervisor = obra, request.user
            reporte.estatus = reportes.estatus(reportes.BORRADOR)
            reporte.save()
            if enviar:
                mensuales.enviar_a_revision(reporte, request.user)
        if enviar:
            messages.success(request, "Reporte mensual enviado a revisión del Superintendente y del Residente.")
        else:
            messages.success(request, "Borrador guardado. Envíalo a revisión cuando esté listo.")
        return redirect("mensual_detalle", obra_id=obra.pk, reporte_id=reporte.pk)
    contexto = {"obra": obra, "form": form, "titulo": "Nuevo reporte mensual", "nuevo": True,
                "previa": _vista_previa(obra, form)}
    return render(request, "mensual_form.html", contexto)


@requiere_permiso(VER)
def mensual_detalle(request, obra, reporte_id):
    reporte = _mensual_visible(request.user, obra, reporte_id)
    es_admin = es_administrador(request.user, obra)
    decs = mensuales.decisiones(reporte)
    roles = mensuales.roles_que_decide(request.user, obra, reporte, decs, es_admin)
    puede_validar = puede(request.user, obra, VALIDAR_REPORTE) and bool(roles)
    consolidado, congelado = mensuales.datos(reporte)
    contexto = {
        "obra": obra,
        "reporte": reporte,
        "mes": mensuales.nombre_mes(reporte),
        "estado": mensuales.estado_legible(reporte, decs),
        "decisiones": [(reportes.ETIQUETA[r], d) for r, d in decs.items()],
        "validaciones": reporte.validaciones.select_related("usuario_validador", "estatus").order_by("-fecha_validacion"),
        "c": consolidado,
        "ev": _contexto_evidencias(request.user, obra, reporte),
        "congelado": congelado,
        "puede_editar": mensuales.puede_editar(request.user, obra, reporte),
        "es_borrador": reporte.estatus.nombre == reportes.BORRADOR,
        "es_admin": es_admin,
        "puede_validar": puede_validar,
        "revision_bloqueada": puede(request.user, obra, VALIDAR_REPORTE) and not puede_validar
        and reporte.estatus.nombre != reportes.BORRADOR,
        "form_validacion": ValidacionForm(es_admin=es_admin, roles=roles) if puede_validar else None,
    }
    return render(request, "mensual_detalle.html", contexto)


@requiere_permiso(CREAR_REPORTE)
def mensual_editar(request, obra, reporte_id):
    reporte = _mensual_visible(request.user, obra, reporte_id)
    if not mensuales.puede_editar(request.user, obra, reporte):
        raise PermissionDenied
    era_borrador = reporte.estatus.nombre == reportes.BORRADOR
    form = ReporteMensualForm(request.POST or None, instance=reporte, obra=obra)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            bloqueado = _mensual_visible(request.user, obra, reporte_id, bloquear=True)
            if not mensuales.puede_editar(request.user, obra, bloqueado):
                messages.error(request, "El reporte ya fue aprobado; no se puede editar.")
                return redirect("mensual_detalle", obra_id=obra.pk, reporte_id=reporte_id)
            for campo in ("fecha_corte", "resumen", "observaciones"):
                setattr(bloqueado, campo, form.cleaned_data[campo])
            bloqueado.fecha_modificacion = timezone.now()
            bloqueado.save(update_fields=["fecha_corte", "resumen", "observaciones", "fecha_modificacion"])
            previo = bloqueado.estatus.nombre
            reenviar = (era_borrador and request.POST.get("accion") == "enviar") or (
                previo in (reportes.RECHAZADO, reportes.EN_REVISION)
            )
            if reenviar:
                mensuales.enviar_a_revision(bloqueado, request.user)
        if era_borrador and not reenviar:
            messages.success(request, "Borrador actualizado.")
        elif reenviar:
            messages.success(request, "Reporte mensual enviado a revisión.")
        else:
            messages.success(request, "Reporte mensual actualizado.")
        return redirect("mensual_detalle", obra_id=obra.pk, reporte_id=reporte_id)
    contexto = {"obra": obra, "form": form, "titulo": f"Editar reporte mensual · {mensuales.nombre_mes(reporte)}",
                "nuevo": False, "es_borrador": era_borrador, "reporte": reporte, "previa": _vista_previa(obra, form)}
    return render(request, "mensual_form.html", contexto)


@requiere_permiso(VALIDAR_REPORTE)
def mensual_validar(request, obra, reporte_id):
    if request.method == "POST":
        es_admin = es_administrador(request.user, obra)
        with transaction.atomic():
            reporte = _mensual_visible(request.user, obra, reporte_id, bloquear=True)  # sin decisiones simultáneas
            roles = mensuales.roles_que_decide(request.user, obra, reporte, es_admin=es_admin)
            form = ValidacionForm(request.POST, es_admin=es_admin, roles=roles)
            if not roles:
                messages.error(
                    request,
                    "Ya no puedes decidir sobre este reporte. Si hay un error, solo el administrador puede corregirlo.",
                )
            elif form.is_valid():
                mensuales.registrar_decision(
                    reporte, request.user, form.cleaned_data["rol"], form.cleaned_data["estatus"],
                    form.cleaned_data["comentarios"],
                    por_admin=es_admin and form.cleaned_data["rol"] not in roles_en(request.user, obra),
                )
                messages.success(request, f"Decisión registrada. Estado: {mensuales.estado_legible(reporte)}.")
            else:
                messages.error(request, " ".join(e for errores in form.errors.values() for e in errores))
    return redirect("mensual_detalle", obra_id=obra.pk, reporte_id=reporte_id)


@requiere_permiso(VER)
def mensual_pdf(request, obra, reporte_id):
    reporte = _mensual_visible(request.user, obra, reporte_id)
    consolidado, congelado = mensuales.datos(reporte)
    contenido = pdf.reporte_mensual_pdf(
        reporte, mensuales.nombre_mes(reporte), mensuales.estado_legible(reporte), consolidado, congelado,
        list(reporte.validaciones.select_related("usuario_validador", "estatus").order_by("fecha_validacion")),
    )
    respuesta = HttpResponse(contenido, content_type="application/pdf")
    respuesta["Content-Disposition"] = f'inline; filename="reporte-mensual-{reporte.anio}-{reporte.mes:02d}.pdf"'
    return respuesta


# ---------------------------------------------------------------------------
# Bitácora de obra (CU-09)
# ---------------------------------------------------------------------------

def _nota(obra, nota_id, bloquear=False):
    consulta = NotaBitacora.objects.select_related("obra", "tipo", "usuario_creador", "nota_relacionada")
    if bloquear:
        consulta = consulta.select_for_update(of=("self",))
    return get_object_or_404(consulta, pk=nota_id, obra=obra)


def _es_uuid(valor):
    try:
        uuid.UUID(str(valor))
        return True
    except ValueError:
        return False


@requiere_permiso(VER)
def bitacora_lista(request, obra):
    estado = request.GET.get("estado", "")
    tipo = request.GET.get("tipo", "")
    q = (request.GET.get("q") or "").strip()
    if estado not in ("", bitacora.PENDIENTE, bitacora.FIRMADA, bitacora.RECHAZADA):
        estado = ""
    if not tipo.isdigit():
        tipo = ""
    filas = [(n, bitacora.estado_legible(n)) for n in bitacora.buscar(obra, estado, tipo, q)]
    contexto = {
        "obra": obra, "filas": filas, "estado": estado, "tipo": tipo, "q": q,
        "tipos": CatalogoTipoNota.objects.order_by("id"),
        "estados": (bitacora.PENDIENTE, bitacora.FIRMADA, bitacora.RECHAZADA),
        "puede_crear": puede(request.user, obra, CREAR_NOTA),
    }
    return render(request, "bitacora_lista.html", contexto)


@requiere_permiso(CREAR_NOTA)
def nota_nueva(request, obra):
    inicial = {}
    responde = request.GET.get("responde")
    if responde and _es_uuid(responde):
        inicial["nota_relacionada"] = NotaBitacora.objects.filter(obra=obra, pk=responde).first()
    form = NotaBitacoraForm(request.POST or None, obra=obra, initial=inicial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            nota = form.save(commit=False)
            nota.obra = obra
            bitacora.crear_nota(nota, request.user)
        messages.success(request, f"Nota {nota.folio} registrada. Estado: {bitacora.estado_legible(nota)}.")
        return redirect("nota_detalle", obra_id=obra.pk, nota_id=nota.pk)
    return render(request, "nota_form.html", {"obra": obra, "form": form, "titulo": "Nueva nota de bitácora"})


@requiere_permiso(VER)
def nota_detalle(request, obra, nota_id):
    nota = _nota(obra, nota_id)
    es_admin = es_administrador(request.user, obra)
    vigentes = bitacora.firmas_vigentes(nota)
    requeridos = bitacora.roles_requeridos(obra, vigentes)
    roles = bitacora.roles_que_firma(request.user, obra, nota, vigentes, es_admin)
    puede_firmar = puede(request.user, obra, FIRMAR_NOTA) and bool(roles)
    mis_roles_firmantes = [r for r in bitacora.ROLES_FIRMANTES if r in roles_en(request.user, obra)]
    contexto = {
        "obra": obra,
        "nota": nota,
        "estado": bitacora.estado_legible(nota, vigentes, requeridos),
        "firmas": [(bitacora.ETIQUETA[r], vigentes[r]) for r in requeridos],
        "historial": nota.firmas.select_related("usuario_firmante").order_by("-fecha_firma"),
        "ev": _contexto_evidencias(request.user, obra, nota),
        "citada_por": nota.notas_que_la_citan.select_related("tipo").order_by("folio"),
        "puede_editar": bitacora.puede_editar(request.user, obra, nota, vigentes),
        "puede_borrar": bitacora.puede_borrar(request.user, obra, nota, vigentes),
        "puede_responder": puede(request.user, obra, CREAR_NOTA),
        "es_admin": es_admin,
        "puede_firmar": puede_firmar,
        "firma_bloqueada": bool(mis_roles_firmantes) and not puede_firmar and nota.estado_firma != bitacora.FIRMADA,
        "form_firma": FirmaNotaForm(es_admin=es_admin, roles=roles) if puede_firmar else None,
    }
    return render(request, "nota_detalle.html", contexto)


@requiere_permiso(CREAR_NOTA)
def nota_editar(request, obra, nota_id):
    nota = _nota(obra, nota_id)
    if not bitacora.puede_editar(request.user, obra, nota):
        raise PermissionDenied
    era_rechazada = nota.estado_firma == bitacora.RECHAZADA
    form = NotaBitacoraForm(request.POST or None, instance=nota, obra=obra)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            bloqueada = _nota(obra, nota_id, bloquear=True)
            if not bitacora.puede_editar(request.user, obra, bloqueada):
                messages.error(request, "La nota ya fue firmada por alguien más; ya no se puede editar.")
                return redirect("nota_detalle", obra_id=obra.pk, nota_id=nota_id)
            bloqueada.tipo = form.cleaned_data["tipo"]
            bloqueada.descripcion = form.cleaned_data["descripcion"]
            bloqueada.nota_relacionada = form.cleaned_data["nota_relacionada"]
            bitacora.guardar_correccion(bloqueada, request.user)
        if era_rechazada:
            messages.success(request, "Nota corregida y enviada de nuevo a firma.")
        else:
            messages.success(request, "Nota actualizada.")
        return redirect("nota_detalle", obra_id=obra.pk, nota_id=nota_id)
    contexto = {"obra": obra, "form": form, "nota": nota, "titulo": f"Editar nota {nota.folio}",
                "era_rechazada": era_rechazada}
    return render(request, "nota_form.html", contexto)


@requiere_permiso(CREAR_NOTA)
def nota_borrar(request, obra, nota_id):
    if request.method != "POST":
        return redirect("nota_detalle", obra_id=obra.pk, nota_id=nota_id)
    with transaction.atomic():
        nota = _nota(obra, nota_id, bloquear=True)
        if not bitacora.puede_borrar(request.user, obra, nota):
            messages.error(request, "La nota ya fue firmada o rechazada por alguien más; no se puede borrar.")
            return redirect("nota_detalle", obra_id=obra.pk, nota_id=nota_id)
        folio = nota.folio
        rutas = list(evidencias.de(nota).values_list("ruta", flat=True))
        nota.delete()
        evidencias.borrar_archivos_al_confirmar(rutas)
    messages.success(request, f"Nota {folio} borrada. Su folio no se volverá a usar.")
    return redirect("bitacora_lista", obra_id=obra.pk)


@requiere_permiso(FIRMAR_NOTA)
def nota_firmar(request, obra, nota_id):
    if request.method == "POST":
        es_admin = es_administrador(request.user, obra)
        with transaction.atomic():
            nota = _nota(obra, nota_id, bloquear=True)  # dos personas no deciden a la vez
            roles = bitacora.roles_que_firma(request.user, obra, nota, es_admin=es_admin)
            form = FirmaNotaForm(request.POST, es_admin=es_admin, roles=roles)
            if not roles:
                messages.error(
                    request, "Ya no puedes firmar esta nota. Si hay un error, solo el administrador puede corregirlo."
                )
            elif form.is_valid():
                bitacora.registrar_firma(
                    nota, request.user, form.cleaned_data["rol"], form.cleaned_data["decision"],
                    form.cleaned_data["comentarios"],
                )
                messages.success(request, f"Firma registrada. Estado: {bitacora.estado_legible(nota)}.")
            else:
                messages.error(request, " ".join(e for errores in form.errors.values() for e in errores))
    return redirect("nota_detalle", obra_id=obra.pk, nota_id=nota_id)


# ---------------------------------------------------------------------------
# Evidencias (fotos y PDF) en Supabase Storage
# ---------------------------------------------------------------------------

def _contexto_evidencias(usuario, obra, objeto):
    return {
        "lista": evidencias.de(objeto),
        "puede": evidencias.puede_modificar(usuario, obra, objeto),
        "tipo": evidencias.tipo_de(objeto),
        "registro": objeto,
        "acepta": evidencias.ACEPTA,
        "max_mb": settings.EVIDENCIA_MAX_MB,
        "max_archivos": settings.EVIDENCIA_MAX_ARCHIVOS,
    }


def _volver_a(objeto):
    destino = {"diario": "reporte_detalle", "mensual": "mensual_detalle", "nota": "nota_detalle"}
    clave = "nota_id" if isinstance(objeto, NotaBitacora) else "reporte_id"
    return redirect(destino[evidencias.tipo_de(objeto)], **{"obra_id": objeto.obra_id, clave: objeto.pk})


def _evidencia(obra, evidencia_id):
    return get_object_or_404(
        Evidencia.objects.select_related("reporte_diario__estatus", "reporte_mensual__estatus", "nota_bitacora"),
        Q(reporte_diario__obra=obra) | Q(reporte_mensual__obra=obra) | Q(nota_bitacora__obra=obra),
        pk=evidencia_id,
    )


@requiere_permiso(VER)
def evidencia_subir(request, obra, tipo, registro_id):
    if tipo not in evidencias.PADRES:
        raise Http404
    objeto = evidencias.registro(obra, tipo, registro_id)
    if request.method != "POST":
        return _volver_a(objeto)
    try:
        with transaction.atomic():
            objeto = evidencias.registro(obra, tipo, registro_id, bloquear=True)
            if not evidencias.puede_ver(request.user, obra, objeto):
                raise PermissionDenied
            if not evidencias.puede_modificar(request.user, obra, objeto):
                messages.error(request, "Este registro ya no se puede modificar; no se agregaron evidencias.")
                return _volver_a(objeto)
            nuevas, reenviado = evidencias.agregar(
                objeto, request.FILES.getlist("archivos"), request.POST.get("descripcion", ""), request.user
            )
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    except ErrorAlmacenamiento:
        logger.exception("Error al subir evidencias")
        messages.error(request, "No se pudo guardar el archivo en el almacenamiento. Intenta de nuevo en un momento.")
    else:
        texto = f"{len(nuevas)} evidencia(s) agregada(s)."
        if reenviado:
            texto += " Como ya tenía revisiones, se envió de nuevo a revisión."
        messages.success(request, texto)
    return _volver_a(objeto)


@requiere_permiso(VER)
def evidencia_ver(request, obra, evidencia_id):
    evidencia = _evidencia(obra, evidencia_id)
    objeto = evidencias.registro_de(evidencia)
    if not evidencias.puede_ver(request.user, obra, objeto):
        raise PermissionDenied
    descargar = "descargar" in request.GET
    nombre = evidencias.nombre_descarga(evidencia)
    try:
        if hasattr(default_storage, "url_firmada"):
            return redirect(default_storage.url_firmada(evidencia.ruta, descargar=nombre if descargar else None))
        return FileResponse(default_storage.open(evidencia.ruta), content_type=evidencia.tipo,
                            as_attachment=descargar, filename=nombre)
    except (ErrorAlmacenamiento, FileNotFoundError):
        logger.exception("Error al abrir la evidencia %s", evidencia.pk)
        messages.error(request, "No se pudo abrir la evidencia. Intenta de nuevo en un momento.")
        return _volver_a(objeto)


@requiere_permiso(VER)
def evidencia_borrar(request, obra, evidencia_id):
    evidencia = _evidencia(obra, evidencia_id)
    objeto = evidencias.registro_de(evidencia)
    if request.method != "POST":
        return _volver_a(objeto)
    with transaction.atomic():
        objeto = evidencias.registro(obra, evidencias.tipo_de(objeto), objeto.pk, bloquear=True)
        if not evidencias.puede_ver(request.user, obra, objeto):
            raise PermissionDenied
        if not evidencias.puede_modificar(request.user, obra, objeto):
            messages.error(request, "Este registro ya no se puede modificar; la evidencia se conserva.")
            return _volver_a(objeto)
        evidencia = get_object_or_404(Evidencia, pk=evidencia_id)
        reenviado = evidencias.quitar(evidencia, request.user)
    texto = "Evidencia eliminada."
    if reenviado:
        texto += " Como ya tenía revisiones, se envió de nuevo a revisión."
    messages.success(request, texto)
    return _volver_a(objeto)


# ---------------------------------------------------------------------------
# Estimaciones (CU-02, CU-05, CU-08)
# ---------------------------------------------------------------------------

def _estimacion(obra, estimacion_id, bloquear=False):
    consulta = Estimacion.objects.select_related("contrato__obra", "estatus", "superintendente")
    if bloquear:
        consulta = consulta.select_for_update(of=("self",))
    return get_object_or_404(consulta, pk=estimacion_id, contrato__obra=obra)


def _estimacion_visible(usuario, obra, estimacion_id, bloquear=False):
    est = _estimacion(obra, estimacion_id, bloquear)
    if not estimaciones.puede_ver(usuario, obra, est):
        raise PermissionDenied  # un borrador solo lo ven su autor y los administradores
    return est


def _filas_estimacion(contrato, est, post=None):
    """Una fila por concepto del catálogo con lo contratado, lo estimado antes y lo de esta estimación."""
    numero = est.numero_estimacion if est else estimaciones.siguiente_numero(contrato)
    anteriores = estimaciones.acumulado_anterior(contrato, numero)
    actuales = {d.concepto_id: d for d in est.estimaciondetalle_set.all()} if est else {}
    filas = []
    for c in CatalogoConcepto.objects.filter(obra=contrato.obra).order_by("capitulo", "clave"):
        actual = actuales.get(c.pk)
        if post is not None:
            texto = (post.get(f"cant_{c.pk}") or "").strip().replace(",", "")
            nota_id = post.get(f"nota_{c.pk}") or ""
        else:
            texto = f"{actual.cantidad_ejecutada.normalize():f}" if actual else ""
            nota_id = str(actual.nota_bitacora_id) if actual and actual.nota_bitacora_id else ""
        anterior = anteriores.get(c.pk, Decimal(0))
        filas.append({"c": c, "anterior": anterior, "texto": texto, "nota_id": nota_id, "error": None, "cantidad": None})
    return filas


def _validar_filas(filas, obra):
    notas_validas = {str(n.pk) for n in estimaciones.notas_firmadas(obra)}
    ok = True
    for f in filas:
        if not f["texto"]:
            f["cantidad"] = Decimal(0)
            continue
        try:
            cantidad = Decimal(f["texto"])
        except (ArithmeticError, ValueError):
            cantidad = None
        if cantidad is None or not cantidad.is_finite() or cantidad < 0 or cantidad.as_tuple().exponent < -4:
            f["error"], ok = "Cantidad no válida (número positivo, hasta 4 decimales).", False
            continue
        f["cantidad"] = cantidad
        excede = f["anterior"] + cantidad > f["c"].volumen_total_contratado
        if excede and cantidad > 0 and f["nota_id"] not in notas_validas:
            f["error"], ok = "Rebasa el volumen contratado: elige la nota de bitácora firmada que lo autorizó.", False
        if not excede:
            f["nota_id"] = ""
    return ok


def _guardar_filas(est, filas):
    actuales = {d.concepto_id: d for d in est.estimaciondetalle_set.all()}
    for f in filas:
        actual = actuales.get(f["c"].pk)
        if not f["cantidad"]:
            if actual:
                actual.delete()
            continue
        nota = f["nota_id"] or None
        if actual:
            actual.cantidad_ejecutada, actual.nota_bitacora_id = f["cantidad"], nota
            actual.save(update_fields=["cantidad_ejecutada", "nota_bitacora"])
        else:
            EstimacionDetalle.objects.create(estimacion=est, concepto=f["c"], cantidad_ejecutada=f["cantidad"],
                                             nota_bitacora_id=nota)
    estimaciones.recalcular(est)


def _contexto_form_estimacion(obra, contrato, form, filas, est=None):
    return {
        "obra": obra, "contrato": contrato, "form": form, "filas": filas, "est": est,
        "notas": estimaciones.notas_firmadas(obra),
        "titulo": f"Editar estimación {est.folio}" if est else
                  f"Nueva estimación {estimaciones.folio(estimaciones.siguiente_numero(contrato))}",
    }


@requiere_permiso(VER)
def estimaciones_lista(request, obra):
    contrato = estimaciones.contrato_de(obra)
    consulta = Estimacion.objects.filter(contrato__obra=obra).select_related("estatus", "superintendente", "contrato")
    if not es_administrador(request.user, obra):
        consulta = consulta.exclude(Q(estatus__nombre=estimaciones.BORRADOR) & ~Q(superintendente=request.user))
    filas = [(e, estimaciones.estado_legible(e)) for e in consulta.order_by("-numero_estimacion")]
    contexto = {
        "obra": obra, "contrato": contrato, "filas": filas,
        "puede_crear": bool(contrato) and puede(request.user, obra, CREAR_ESTIMACION),
        "abierta": estimaciones.abierta(contrato) if contrato else None,
        "catalogo_vacio": not CatalogoConcepto.objects.filter(obra=obra).exists(),
    }
    return render(request, "estimaciones_lista.html", contexto)


@requiere_permiso(CREAR_ESTIMACION)
def estimacion_nueva(request, obra):
    contrato = estimaciones.contrato_de(obra)
    if not contrato or not CatalogoConcepto.objects.filter(obra=obra).exists():
        messages.error(request, "Para crear estimaciones la obra necesita contrato y catálogo de conceptos.")
        return redirect("estimaciones_lista", obra_id=obra.pk)
    abierta = estimaciones.abierta(contrato)
    if abierta:
        messages.error(request, f"Primero debe quedar aprobada la {abierta.folio}; solo puede haber una estimación abierta.")
        return redirect("estimacion_detalle", obra_id=obra.pk, estimacion_id=abierta.pk)
    form = EstimacionForm(request.POST or None, contrato=contrato)
    filas = _filas_estimacion(contrato, None, request.POST if request.method == "POST" else None)
    if request.method == "POST":
        filas_ok = _validar_filas(filas, obra)
        if form.is_valid() and filas_ok:
            with transaction.atomic():
                Contrato.objects.select_for_update().get(pk=contrato.pk)  # numeración sin choques
                if estimaciones.abierta(contrato):
                    messages.error(request, "Alguien más acaba de crear una estimación abierta.")
                    return redirect("estimaciones_lista", obra_id=obra.pk)
                est = form.save(commit=False)
                est.contrato, est.superintendente = contrato, request.user
                est.numero_estimacion = estimaciones.siguiente_numero(contrato)
                est.folio = estimaciones.folio(est.numero_estimacion)
                est.estatus = reportes.estatus(estimaciones.BORRADOR)
                est.save()
                _guardar_filas(est, filas)
            messages.success(request, f"{est.folio} guardada como borrador. Adjunta los números generadores y envíala a revisión.")
            return redirect("estimacion_detalle", obra_id=obra.pk, estimacion_id=est.pk)
        messages.error(request, "Revisa los datos marcados.")
    return render(request, "estimacion_form.html", _contexto_form_estimacion(obra, contrato, form, filas))


@requiere_permiso(CREAR_ESTIMACION)
def estimacion_editar(request, obra, estimacion_id):
    est = _estimacion_visible(request.user, obra, estimacion_id)
    if not estimaciones.puede_editar(request.user, obra, est):
        raise PermissionDenied
    contrato = est.contrato
    form = EstimacionForm(request.POST or None, instance=est, contrato=contrato)
    filas = _filas_estimacion(contrato, est, request.POST if request.method == "POST" else None)
    if request.method == "POST":
        filas_ok = _validar_filas(filas, obra)
        if form.is_valid() and filas_ok:
            with transaction.atomic():
                bloqueada = _estimacion(obra, estimacion_id, bloquear=True)
                if not estimaciones.puede_editar(request.user, obra, bloqueada):
                    messages.error(request, "La estimación ya está en validación; no se puede modificar.")
                    return redirect("estimacion_detalle", obra_id=obra.pk, estimacion_id=est.pk)
                for campo in ("periodo_inicio", "fecha_corte", "observaciones", "atraso_no_imputable", "justificacion_atraso"):
                    setattr(bloqueada, campo, form.cleaned_data[campo])
                bloqueada.fecha_modificacion = timezone.now()
                bloqueada.save(update_fields=["periodo_inicio", "fecha_corte", "observaciones", "atraso_no_imputable",
                                              "justificacion_atraso", "fecha_modificacion"])
                _guardar_filas(bloqueada, filas)
            messages.success(request, "Estimación actualizada. Cuando esté lista, envíala a revisión.")
            return redirect("estimacion_detalle", obra_id=obra.pk, estimacion_id=est.pk)
        messages.error(request, "Revisa los datos marcados.")
    return render(request, "estimacion_form.html", _contexto_form_estimacion(obra, contrato, form, filas, est))


@requiere_permiso(VER)
def estimacion_detalle(request, obra, estimacion_id):
    est = _estimacion_visible(request.user, obra, estimacion_id)
    es_admin = es_administrador(request.user, obra)
    decs = estimaciones.decisiones(est)
    roles = estimaciones.roles_que_decide(request.user, obra, est, es_admin)
    puede_validar = puede(request.user, obra, VALIDAR_ESTIMACION) and bool(roles)
    puede_editar = estimaciones.puede_editar(request.user, obra, est)
    renglones = list(est.estimaciondetalle_set.select_related("concepto", "nota_bitacora").order_by("concepto__clave"))
    contexto = {
        "obra": obra, "est": est, "contrato": est.contrato,
        "estado": estimaciones.estado_legible(est, decs),
        "decisiones": list(decs.items()),
        "historial": est.validacionestimacion_set.select_related("usuario_validador", "estatus").order_by("-fecha_validacion"),
        "renglones": renglones,
        "avance": estimaciones.avance(est),
        "plazos": estimaciones.plazos(est),
        "documentos": est.documentos.select_related("usuario_subida").order_by("categoria", "fecha_subida"),
        "puede_editar": puede_editar,
        "faltantes": estimaciones.faltantes_para_enviar(est) if puede_editar else [],
        "form_documento": DocumentoForm(categorias=[(c, c) for c in (
            "Números generadores", "Croquis", "Prueba de laboratorio", "Fotografía", "Factura", "Otro")]) if puede_editar else None,
        "puede_validar": puede_validar,
        "es_admin": es_admin,
        "form_decision": DecisionEstimacionForm(es_admin=es_admin, roles=roles) if puede_validar else None,
        "en_validacion": est.estatus.nombre in estimaciones.EN_VALIDACION,
    }
    return render(request, "estimacion_detalle.html", contexto)


@requiere_permiso(CREAR_ESTIMACION)
def estimacion_enviar(request, obra, estimacion_id):
    if request.method == "POST":
        with transaction.atomic():
            est = _estimacion_visible(request.user, obra, estimacion_id, bloquear=True)
            if not estimaciones.puede_editar(request.user, obra, est):
                messages.error(request, "Esta estimación no se puede enviar ahora.")
            else:
                estimaciones.recalcular(est)
                faltan = estimaciones.faltantes_para_enviar(est)
                if faltan:
                    messages.error(request, "Todavía no se puede enviar: " + " ".join(faltan))
                else:
                    estimaciones.enviar_a_revision(est, request.user)
                    messages.success(request, f"{est.folio} enviada a revisión del Supervisor.")
    return redirect("estimacion_detalle", obra_id=obra.pk, estimacion_id=estimacion_id)


@requiere_permiso(VALIDAR_ESTIMACION)
def estimacion_validar(request, obra, estimacion_id):
    if request.method == "POST":
        es_admin = es_administrador(request.user, obra)
        with transaction.atomic():
            est = _estimacion_visible(request.user, obra, estimacion_id, bloquear=True)
            roles = estimaciones.roles_que_decide(request.user, obra, est, es_admin)
            form = DecisionEstimacionForm(request.POST, es_admin=es_admin, roles=roles)
            if not roles:
                messages.error(request, "Ya no te toca decidir sobre esta estimación.")
            elif form.is_valid():
                rol = form.cleaned_data["rol"]
                estimaciones.registrar_decision(
                    est, request.user, rol, form.cleaned_data["estatus"], form.cleaned_data["comentarios"],
                    por_admin=es_admin and rol not in roles_en(request.user, obra),
                )
                messages.success(request, f"Decisión registrada. Estado: {estimaciones.estado_legible(est)}.")
            else:
                messages.error(request, " ".join(e for errores in form.errors.values() for e in errores))
    return redirect("estimacion_detalle", obra_id=obra.pk, estimacion_id=estimacion_id)


@requiere_permiso(CREAR_ESTIMACION)
def estimacion_documento_subir(request, obra, estimacion_id):
    if request.method == "POST":
        est = _estimacion_visible(request.user, obra, estimacion_id)
        form = DocumentoForm(request.POST, request.FILES)
        if not estimaciones.puede_editar(request.user, obra, est):
            messages.error(request, "La estimación está en validación; no se pueden cambiar sus documentos.")
        elif form.is_valid():
            try:
                with transaction.atomic():
                    documentos.guardar(obra, form.cleaned_data["archivo"], request.user, form.cleaned_data["categoria"],
                                       form.cleaned_data["descripcion"], estimacion=est, nombre=form.cleaned_data["nombre"])
                messages.success(request, "Documento agregado a la estimación.")
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
            except ErrorAlmacenamiento:
                logger.exception("Error al subir un documento")
                messages.error(request, "No se pudo guardar el archivo. Intenta de nuevo en un momento.")
        else:
            messages.error(request, " ".join(e for errores in form.errors.values() for e in errores))
    return redirect("estimacion_detalle", obra_id=obra.pk, estimacion_id=estimacion_id)


def _documento(obra, documento_id):
    return get_object_or_404(Documento.objects.select_related("obra", "estimacion__estatus", "usuario_subida"),
                             pk=documento_id, obra=obra)


@requiere_permiso(VER)
def documento_ver(request, obra, documento_id):
    doc = _documento(obra, documento_id)
    if doc.estimacion and not estimaciones.puede_ver(request.user, obra, doc.estimacion):
        raise PermissionDenied
    nombre = documentos.nombre_descarga(doc)
    try:
        if hasattr(default_storage, "url_firmada"):
            return redirect(default_storage.url_firmada(doc.ruta_almacenamiento, descargar=nombre))
        return FileResponse(default_storage.open(doc.ruta_almacenamiento), content_type=documentos.tipo_contenido(doc),
                            as_attachment=True, filename=nombre)
    except (ErrorAlmacenamiento, FileNotFoundError):
        logger.exception("Error al abrir el documento %s", doc.pk)
        messages.error(request, "No se pudo abrir el documento. Intenta de nuevo en un momento.")
        if doc.estimacion_id:
            return redirect("estimacion_detalle", obra_id=obra.pk, estimacion_id=doc.estimacion_id)
        return redirect("obra_detalle", obra_id=obra.pk)


@requiere_permiso(VER)
def documento_borrar(request, obra, documento_id):
    doc = _documento(obra, documento_id)
    volver = (redirect("estimacion_detalle", obra_id=obra.pk, estimacion_id=doc.estimacion_id) if doc.estimacion_id
              else redirect("obra_detalle", obra_id=obra.pk))
    if request.method != "POST":
        return volver
    if not documentos.puede_borrar(request.user, doc) or (
            doc.estimacion and not estimaciones.puede_editar(request.user, obra, doc.estimacion)):
        messages.error(request, "No puedes borrar este documento.")
        return volver
    with transaction.atomic():
        documentos.borrar(doc)
    messages.success(request, "Documento eliminado.")
    return volver


# ---------------------------------------------------------------------------
# Módulo de validaciones del administrador (CU-14 y CU-15)
# ---------------------------------------------------------------------------

TIPOS_VALIDACION = {
    "diarios": {
        "modelo": ReporteDiario, "reglas": reportes, "nombre": "Reportes diarios", "orden": ("-fecha_reporte",),
        "obra": "obra", "autor": "supervisor", "historial": "validacionreportediario_set", "url": "reporte_detalle",
        "etiqueta": lambda r: r.fecha_reporte.strftime("%d/%m/%Y"),
    },
    "mensuales": {
        "modelo": ReporteMensual, "reglas": mensuales, "nombre": "Reportes mensuales", "orden": ("-anio", "-mes"),
        "obra": "obra", "autor": "supervisor", "historial": "validaciones", "url": "mensual_detalle",
        "etiqueta": lambda r: mensuales.nombre_mes(r),
    },
    "estimaciones": {
        "modelo": Estimacion, "reglas": estimaciones, "nombre": "Estimaciones", "orden": ("-fecha_corte",),
        "obra": "contrato__obra", "autor": "superintendente", "historial": "validacionestimacion_set",
        "url": "estimacion_detalle", "etiqueta": lambda e: e.folio,
    },
}
ESTATUS_FILTRO = (reportes.PENDIENTE, reportes.EN_REVISION, estimaciones.OBSERVACIONES, reportes.APROBADO,
                  reportes.RECHAZADO)


def _solo_administradores(vista):
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not obras_administradas(request.user).exists():
            raise PermissionDenied
        return vista(request, *args, **kwargs)
    return envoltura


def _obra_de(conf, registro):
    return registro.contrato.obra if conf["obra"] == "contrato__obra" else registro.obra


def _decisiones_legibles(tipo, conf, registro):
    decs = conf["reglas"].decisiones(registro)
    if tipo == "estimaciones":
        return list(decs.items())
    return [(reportes.ETIQUETA[rol], d) for rol, d in decs.items()]


def _form_validacion(tipo, data=None):
    if tipo == "estimaciones":
        return DecisionEstimacionForm(data, es_admin=True, roles=estimaciones.ROLES)
    return ValidacionForm(data, es_admin=True, roles=reportes.ROLES_VALIDADORES)


@_solo_administradores
def validaciones(request):
    tipo = request.GET.get("tipo") if request.GET.get("tipo") in TIPOS_VALIDACION else "diarios"
    conf = TIPOS_VALIDACION[tipo]
    obras = obras_administradas(request.user).order_by("nombre")
    obra_id = request.GET.get("obra", "")
    estatus_f = request.GET.get("estatus", "")
    consulta = conf["modelo"].objects.filter(**{f"{conf['obra']}__in": obras}).exclude(estatus__nombre=reportes.BORRADOR)
    if obra_id and _es_uuid(obra_id):
        consulta = consulta.filter(**{f"{conf['obra']}_id" if conf["obra"] == "obra" else "contrato__obra_id": obra_id})
    else:
        obra_id = ""
    if estatus_f in ESTATUS_FILTRO:
        consulta = consulta.filter(estatus__nombre=estatus_f)
    else:
        estatus_f = ""
    filas = []
    for r in consulta.select_related(conf["obra"], conf["autor"], "estatus").order_by(*conf["orden"])[:200]:
        filas.append({"r": r, "nombre": conf["etiqueta"](r), "obra": _obra_de(conf, r), "autor": getattr(r, conf["autor"]),
                      "decisiones": _decisiones_legibles(tipo, conf, r)})
    contexto = {
        "tipo": tipo, "tipos": [(k, c["nombre"]) for k, c in TIPOS_VALIDACION.items()], "filas": filas,
        "obras": obras, "obra_id": obra_id, "estatus": estatus_f, "estatus_opciones": ESTATUS_FILTRO,
    }
    return render(request, "validaciones.html", contexto)


@_solo_administradores
def validacion_cambiar(request, tipo, reporte_id):
    if tipo not in TIPOS_VALIDACION:
        raise Http404
    conf = TIPOS_VALIDACION[tipo]
    reporte = get_object_or_404(
        conf["modelo"].objects.select_related(conf["obra"], conf["autor"], "estatus"),
        **{"pk": reporte_id, f"{conf['obra']}__in": obras_administradas(request.user)},
    )
    if reporte.estatus.nombre == reportes.BORRADOR:
        messages.error(request, "Un borrador todavía no tiene validaciones.")
        return redirect(f"{reverse('validaciones')}?tipo={tipo}")
    form = _form_validacion(tipo, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            bloqueado = conf["modelo"].objects.select_for_update(of=("self",)).select_related(
                conf["obra"], conf["autor"], "estatus").get(pk=reporte.pk)
            rol = form.cleaned_data["rol"]
            conf["reglas"].registrar_decision(
                bloqueado, request.user, rol, form.cleaned_data["estatus"], form.cleaned_data["comentarios"],
                por_admin=rol not in roles_en(request.user, _obra_de(conf, bloqueado)),
            )
        messages.success(request, f"Estatus actualizado. {_obra_de(conf, bloqueado).nombre} · {conf['etiqueta'](bloqueado)}: "
                                  f"{conf['reglas'].estado_legible(bloqueado)}.")
        return redirect(f"{reverse('validaciones')}?tipo={tipo}")
    obra = _obra_de(conf, reporte)
    contexto = {
        "tipo": tipo, "tipo_nombre": conf["nombre"], "reporte": reporte, "nombre": conf["etiqueta"](reporte),
        "obra_r": obra, "autor": getattr(reporte, conf["autor"]),
        "estado": conf["reglas"].estado_legible(reporte),
        "decisiones": _decisiones_legibles(tipo, conf, reporte),
        "historial": getattr(reporte, conf["historial"]).select_related("usuario_validador", "estatus")
        .order_by("-fecha_validacion"),
        "form": form,
        "url_reporte": reverse(conf["url"], args=[obra.pk, reporte.pk]),
    }
    return render(request, "validacion_cambiar.html", contexto)


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
