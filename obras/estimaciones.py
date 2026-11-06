"""
Reglas de las estimaciones (CU-02, CU-05, CU-08, CU-16, CU-17), con base en la LOPSRM y su Reglamento.

Flujo:  Borrador → (enviar) → Pendiente → [Supervisor] → En revisión → [Residente] → Aprobado
  - El Superintendente la crea con los conceptos del catálogo y la envía (CU-02).
  - El Supervisor la revisa primero (CU-05): la aprueba (pasa al Residente), la regresa «Con observaciones»
    (corrige sin que cuente como rechazo) o la rechaza.
  - El Residente da la validación final (CU-08): la aprueba o la rechaza.
  - Rechazada o con observaciones, el Superintendente la corrige y la reenvía; las decisiones previas se reinician.
  - Mientras está en validación (Pendiente o En revisión) sus volúmenes no se pueden modificar.
  - El administrador puede cambiar la decisión de cualquiera de los dos, siempre con motivo (CU-16, CU-17).
  - Solo puede haber una estimación abierta (no aprobada) por contrato; así los acumulados y las retenciones cuadran.

Cálculos (los totales finales los hace el trigger fn_calcular_totales_estimacion):
  - Importe de cada renglón = cantidad × precio unitario del catálogo; subtotal = suma de renglones.
  - Amortización del anticipo = subtotal × % de anticipo del contrato (Reglamento art. 143).
  - Retención por derechos = subtotal × % de retención del contrato (Reglamento art. 128).
  - Retención económica por atraso (Ley art. 46 Bis, Reglamento art. 88): si a la fecha de corte el avance real
    va detrás del programa, se retiene el % del contrato sobre el importe atrasado; si después se regulariza, se
    devuelve en la siguiente estimación. No se aplica si el Superintendente justifica un atraso no imputable.
  - Un renglón excedente (acumulado mayor al volumen contratado) solo se paga con orden escrita: debe citar una
    nota de bitácora firmada (Reglamento art. 118).
  - Para enviarla se exige el soporte de números generadores (Reglamento art. 132).
Plazos (Ley art. 54): periodo de máximo un mes; se presenta dentro de los 6 días naturales siguientes al corte y la
residencia tiene 15 días para revisarla. El sistema avisa, pero no la aprueba sola al vencer.
"""
import calendar
import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db.models import Max, Sum
from django.urls import reverse
from django.utils import timezone

from . import bitacora, reportes
from .correo import notificar
from .models import Contrato, Estimacion, EstimacionDetalle, ReporteDiario, UsuarioProyectoRol, ValidacionEstimacion
from .permisos import CREAR_ESTIMACION, VALIDAR_ESTIMACION, es_administrador, obras_de, puede, roles_en

logger = logging.getLogger(__name__)

BORRADOR, PENDIENTE, EN_REVISION, APROBADO, RECHAZADO = (
    reportes.BORRADOR, reportes.PENDIENTE, reportes.EN_REVISION, reportes.APROBADO, reportes.RECHAZADO)
OBSERVACIONES = "Con observaciones"
ROL_SUPERVISOR, ROL_RESIDENTE, ROL_SUPERINTENDENTE = "Supervisor", reportes.ROL_RESIDENTE, reportes.ROL_SUPERINTENDENTE
ROLES = (ROL_SUPERVISOR, ROL_RESIDENTE)
EDITABLES = (BORRADOR, RECHAZADO, OBSERVACIONES)
EN_VALIDACION = (PENDIENTE, EN_REVISION)
DECISIONES = (APROBADO, RECHAZADO, OBSERVACIONES)
OPCIONES = {ROL_SUPERVISOR: (APROBADO, OBSERVACIONES, RECHAZADO), ROL_RESIDENTE: (APROBADO, RECHAZADO)}
DIAS_PRESENTACION, DIAS_REVISION = 6, 15
CENTAVOS = Decimal("0.01")


def _q(valor):
    return Decimal(valor).quantize(CENTAVOS, ROUND_HALF_UP)


def folio(numero):
    return f"EST-{numero:03d}"


def contrato_de(obra):
    return Contrato.objects.filter(obra=obra).first()


def abierta(contrato, excepto=None):
    """La estimación no aprobada del contrato, si existe."""
    consulta = Estimacion.objects.filter(contrato=contrato).exclude(estatus__nombre=APROBADO)
    if excepto is not None:
        consulta = consulta.exclude(pk=excepto.pk)
    return consulta.order_by("numero_estimacion").first()


def siguiente_numero(contrato):
    return (Estimacion.objects.filter(contrato=contrato).aggregate(m=Max("numero_estimacion"))["m"] or 0) + 1


def periodo_valido(inicio, corte):
    """Ley art. 54: periodicidad no mayor de un mes."""
    if not inicio or not corte:
        return True
    dias_mes = calendar.monthrange(inicio.year, inicio.month)[1]
    return inicio <= corte < inicio + timedelta(days=dias_mes)


# ---------------------------------------------------------------------------
# Decisiones y estatus
# ---------------------------------------------------------------------------

def decisiones(est):
    """{rol: 'Aprobado' | 'Rechazado' | 'Con observaciones' | None} con la última decisión vigente de cada rol."""
    resultado = {rol: None for rol in ROLES}
    filas = ValidacionEstimacion.objects.filter(estimacion=est, rol_validador__in=ROLES).select_related("estatus") \
        .order_by("fecha_validacion", "id")
    for v in filas:
        resultado[v.rol_validador] = v.estatus.nombre if v.estatus.nombre in DECISIONES else None
    return resultado


def estatus_calculado(decs):
    sup, res = decs[ROL_SUPERVISOR], decs[ROL_RESIDENTE]
    if RECHAZADO in (sup, res):
        return RECHAZADO
    if sup == OBSERVACIONES:
        return OBSERVACIONES
    if sup == APROBADO:
        return APROBADO if res == APROBADO else EN_REVISION
    return PENDIENTE


def estado_legible(est, decs=None):
    nombre = est.estatus.nombre
    if nombre == BORRADOR:
        return "Borrador · todavía no se envía a revisión"
    decs = decs if decs is not None else decisiones(est)
    if nombre == PENDIENTE:
        return "Pendiente · espera la revisión del Supervisor"
    if nombre == EN_REVISION:
        return "Aprobada por el Supervisor · espera la validación final del Residente"
    if nombre == APROBADO:
        return "Aprobada por el Supervisor y el Residente"
    if nombre == OBSERVACIONES:
        return "Con observaciones del Supervisor · el Superintendente debe corregirla y reenviarla"
    quien = " y el ".join(r for r in ROLES if decs[r] == RECHAZADO) or "un validador"
    return f"Rechazada por el {quien} · el Superintendente debe corregirla y reenviarla"


def roles_que_decide(usuario, obra, est, es_admin=False):
    """Roles en nombre de los cuales el usuario puede decidir ahora."""
    if est.estatus.nombre == BORRADOR:
        return []
    if es_admin:
        return list(ROLES)
    propios = roles_en(usuario, obra)
    if est.estatus.nombre == PENDIENTE and ROL_SUPERVISOR in propios:
        return [ROL_SUPERVISOR]
    if est.estatus.nombre == EN_REVISION and ROL_RESIDENTE in propios:
        return [ROL_RESIDENTE]
    return []


def puede_ver(usuario, obra, est):
    if est.estatus.nombre != BORRADOR:
        return True
    return est.superintendente_id == usuario.pk or es_administrador(usuario, obra)


def puede_editar(usuario, obra, est):
    """La edita su autor (o un administrador de la obra) cuando es borrador, fue rechazada o tiene observaciones."""
    if est.estatus.nombre not in EDITABLES or not puede(usuario, obra, CREAR_ESTIMACION):
        return False
    return est.superintendente_id == usuario.pk or es_administrador(usuario, obra)


# ---------------------------------------------------------------------------
# Renglones, cálculos y avance
# ---------------------------------------------------------------------------

def acumulado_anterior(contrato, numero):
    """{concepto_id: cantidad estimada en estimaciones anteriores del contrato}."""
    filas = EstimacionDetalle.objects.filter(estimacion__contrato=contrato, estimacion__numero_estimacion__lt=numero) \
        .values("concepto_id").annotate(total=Sum("cantidad_ejecutada"))
    return {f["concepto_id"]: f["total"] for f in filas}


def notas_firmadas(obra):
    return obra.notabitacora_set.filter(estado_firma=bitacora.FIRMADA).select_related("tipo").order_by("-folio")


def avance_al_corte(obra, fecha):
    """(programado %, real %) a la fecha de corte; el real es el del último reporte diario aprobado."""
    prog = reportes.programado_en(obra, fecha)
    ultimo = ReporteDiario.objects.filter(obra=obra, estatus__nombre=APROBADO, fecha_reporte__lte=fecha) \
        .order_by("-fecha_reporte").values_list("avance_fisico_real_porcentaje", flat=True).first()
    return prog, (_q(ultimo) if ultimo is not None else None)


def recalcular(est):
    """Amortización y retención/devolución por atraso, a partir del subtotal ya calculado por la BD."""
    est.refresh_from_db(fields=["subtotal"])
    contrato = est.contrato
    obra = contrato.obra
    est.amortizacion_anticipo = _q(est.subtotal * contrato.porcentaje_anticipo / 100)
    prog, real = avance_al_corte(obra, est.fecha_corte)
    est.dias_atraso = reportes.dias_atraso(obra, est.fecha_corte, real) if real is not None else None
    requerido = Decimal(0)
    if prog is not None and real is not None and real < prog and not est.atraso_no_imputable:
        atrasado = contrato.monto_total_contratado * (prog - real) / 100
        requerido = _q(atrasado * contrato.porcentaje_retencion_atraso / 100)
    previas = Estimacion.objects.filter(contrato=contrato, numero_estimacion__lt=est.numero_estimacion) \
        .aggregate(r=Sum("retencion_atraso"), d=Sum("devolucion_retencion"))
    retenido = (previas["r"] or Decimal(0)) - (previas["d"] or Decimal(0))
    diferencia = requerido - retenido
    est.retencion_atraso = max(diferencia, Decimal(0))
    est.devolucion_retencion = min(max(-diferencia, Decimal(0)), max(retenido, Decimal(0)))
    est.save(update_fields=["amortizacion_anticipo", "dias_atraso", "retencion_atraso", "devolucion_retencion"])
    est.refresh_from_db()
    return {"programado": prog, "real": real, "requerido": requerido, "retenido_antes": retenido}


def avance(est):
    """Avance físico (programado vs real al corte) y financiero acumulado (lo aprobado + esta estimación)."""
    contrato = est.contrato
    prog, real = avance_al_corte(contrato.obra, est.fecha_corte)
    aprobado_antes = Estimacion.objects.filter(contrato=contrato, estatus__nombre=APROBADO,
                                               numero_estimacion__lt=est.numero_estimacion) \
        .aggregate(s=Sum("subtotal"))["s"] or Decimal(0)
    acumulado = aprobado_antes + est.subtotal
    monto = contrato.monto_total_contratado
    return {
        "programado": prog, "real": real,
        "acumulado": acumulado, "aprobado_antes": aprobado_antes,
        "pct_financiero": _q(acumulado * 100 / monto) if monto else None,
        "dias_atraso": est.dias_atraso, "critico": reportes.atraso_critico(est.dias_atraso),
    }


def faltantes_para_enviar(est):
    """Motivos por los que todavía no se puede enviar (lista vacía si se puede)."""
    motivos = []
    renglones = list(est.estimaciondetalle_set.select_related("concepto", "nota_bitacora"))
    if not any(r.cantidad_ejecutada > 0 for r in renglones):
        motivos.append("Captura al menos un concepto con cantidad ejecutada.")
    for r in renglones:
        if r.es_excedente and not (r.nota_bitacora and r.nota_bitacora.estado_firma == bitacora.FIRMADA):
            motivos.append(f"El concepto {r.concepto.clave} rebasa el volumen contratado: cita la nota de bitácora "
                           "firmada que autorizó el excedente.")
    if not est.documentos.filter(categoria="Números generadores").exists():
        motivos.append("Adjunta los números generadores (Reglamento, art. 132).")
    if est.atraso_no_imputable and not (est.justificacion_atraso or "").strip():
        motivos.append("Escribe la justificación del atraso no imputable.")
    if not periodo_valido(est.periodo_inicio, est.fecha_corte):
        motivos.append("El periodo de la estimación no puede ser mayor a un mes (Ley, art. 54).")
    return motivos


def plazos(est):
    """Avisos de plazos de la Ley art. 54 (solo informativos)."""
    hoy = timezone.localdate()
    limite_presentacion = est.fecha_corte + timedelta(days=DIAS_PRESENTACION)
    datos = {"limite_presentacion": limite_presentacion, "presentada": None, "dias_tarde": 0,
             "limite_revision": None, "dias_restantes": None, "vencida": False}
    if est.fecha_envio:
        enviada = timezone.localtime(est.fecha_envio).date()
        datos["presentada"] = enviada
        datos["dias_tarde"] = max(0, (enviada - limite_presentacion).days)
        datos["limite_revision"] = enviada + timedelta(days=DIAS_REVISION)
        if est.estatus.nombre in EN_VALIDACION:
            datos["dias_restantes"] = (datos["limite_revision"] - hoy).days
            datos["vencida"] = datos["dias_restantes"] < 0
    else:
        datos["dias_restantes"] = (limite_presentacion - hoy).days
    return datos


# ---------------------------------------------------------------------------
# Operaciones (llamar dentro de transaction.atomic, con la estimación bloqueada)
# ---------------------------------------------------------------------------

def _validar(est, usuario, rol, nuevo_estatus, comentarios=None):
    return ValidacionEstimacion.objects.create(
        estimacion=est, usuario_validador=usuario, rol_validador=rol, estatus=nuevo_estatus,
        comentarios=comentarios or None, fecha_validacion=timezone.now(),
    )


def enviar_a_revision(est, autor):
    """Pasa a Pendiente reiniciando decisiones previas y avisa al Supervisor."""
    for rol, decision in decisiones(est).items():
        if decision:
            _validar(est, autor, rol, reportes.estatus(PENDIENTE), "Reenviada a revisión tras una corrección.")
    est.estatus = reportes.estatus(PENDIENTE)
    est.fecha_envio = timezone.now()
    est.fecha_presentacion = timezone.localdate()
    est.save(update_fields=["estatus", "fecha_envio", "fecha_presentacion"])
    p = plazos(est)
    texto = (f"{autor} envió la estimación {est.folio} ({est.periodo_inicio:%d/%m/%Y} – {est.fecha_corte:%d/%m/%Y}) "
             f"de la obra «{est.contrato.obra.nombre}» por $ {est.subtotal:,.2f} antes de IVA.\n"
             f"Estado: {estado_legible(est)}.")
    if p["dias_tarde"]:
        texto += f"\n\nSe presentó {p['dias_tarde']} día(s) después del plazo de 6 días naturales tras el corte."
    notificar(_correos(est, (ROL_SUPERVISOR,)), f"Estimación por revisar · {est.contrato.obra.nombre} · {est.folio}",
              texto + f"\n\nTienes hasta el {p['limite_revision']:%d/%m/%Y} para revisarla (Pendientes).{_enlace(est)}")


def registrar_decision(est, usuario, rol, nuevo_estatus, comentarios, por_admin=False):
    _validar(est, usuario, rol, nuevo_estatus, comentarios)
    est.estatus = reportes.estatus(estatus_calculado(decisiones(est)))
    est.save(update_fields=["estatus"])
    if por_admin:
        _avisar_correccion(est, usuario, rol, nuevo_estatus, comentarios)
    else:
        _avisar_decision(est, usuario, rol, nuevo_estatus.nombre, comentarios)
    return est


def _enlace(est):
    if not settings.SITE_URL:
        return ""
    return "\n\n" + settings.SITE_URL + reverse("estimacion_detalle", args=[est.contrato.obra_id, est.pk])


def _correos(est, roles):
    return [a.usuario.correo for a in UsuarioProyectoRol.objects.filter(
        obra=est.contrato.obra, rol__nombre__in=roles, usuario__activo=True).select_related("usuario")]


def _autor(est):
    return [est.superintendente.correo] if est.superintendente.activo else []


def _avisar_decision(est, quien, rol, decision, comentarios):
    obra = est.contrato.obra.nombre
    palabra = {APROBADO: "aprobada", RECHAZADO: "rechazada", OBSERVACIONES: "regresada con observaciones"}[decision]
    texto = f"La estimación {est.folio} de la obra «{obra}» fue {palabra} por el {rol} ({quien}).\n" \
            f"Estado: {estado_legible(est)}."
    if comentarios:
        texto += f"\n\n{'Motivo' if decision == RECHAZADO else 'Comentarios'}:\n{comentarios}"
    if decision in (RECHAZADO, OBSERVACIONES):
        texto += "\n\nCorrígela en el sistema y reenvíala a revisión."
    destinatarios = set(_autor(est))
    if rol == ROL_SUPERVISOR and decision == APROBADO:
        destinatarios |= set(_correos(est, (ROL_RESIDENTE,)))
        texto += "\n\nPasa al Residente para su validación final."
    if rol == ROL_RESIDENTE:
        destinatarios |= set(_correos(est, (ROL_SUPERVISOR,)))
    asunto = {APROBADO: "aprobada", RECHAZADO: "rechazada", OBSERVACIONES: "con observaciones"}[decision]
    notificar(sorted(destinatarios), f"Estimación {asunto} · {obra} · {est.folio}", texto + _enlace(est))


def _avisar_correccion(est, admin, rol, nuevo_estatus, motivo):
    destinatarios = set(_autor(est)) | set(_correos(est, ROLES))
    destinatarios.discard(admin.correo)
    notificar(
        sorted(destinatarios),
        f"Cambio de estatus en estimación · {est.contrato.obra.nombre} · {est.folio}",
        f"{admin} (administrador) cambió la decisión del {rol} a «{nuevo_estatus.nombre}» en la estimación "
        f"{est.folio} de la obra «{est.contrato.obra.nombre}».\nEstado: {estado_legible(est)}.\n\n"
        f"Motivo:\n{motivo}{_enlace(est)}",
    )


# ---------------------------------------------------------------------------
# Bandeja «Pendientes»
# ---------------------------------------------------------------------------

def pendientes_de(usuario):
    """Estimaciones que requieren acción del usuario: (revisar, corregir)."""
    revisar, corregir = [], []
    for obra in obras_de(usuario).order_by("nombre"):
        propios = roles_en(usuario, obra)
        if puede(usuario, obra, VALIDAR_ESTIMACION):
            estados = [e for rol, e in ((ROL_SUPERVISOR, PENDIENTE), (ROL_RESIDENTE, EN_REVISION)) if rol in propios]
            for est in Estimacion.objects.filter(contrato__obra=obra, estatus__nombre__in=estados) \
                    .select_related("contrato__obra", "estatus", "superintendente").order_by("numero_estimacion"):
                revisar.append((est, estado_legible(est)))
        if ROL_SUPERINTENDENTE in propios:
            for est in Estimacion.objects.filter(contrato__obra=obra, superintendente=usuario,
                                                 estatus__nombre__in=EDITABLES) \
                    .select_related("contrato__obra", "estatus").order_by("numero_estimacion"):
                corregir.append((est, estado_legible(est)))
    return revisar, corregir
