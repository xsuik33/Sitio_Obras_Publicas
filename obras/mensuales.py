"""
Reglas del reporte mensual (CU-04).

Flujo (igual que el reporte diario, ver obras/reportes.py):
  Borrador → (enviar) → Pendiente → En revisión → Aprobado / Rechazado
  - Lo elabora el Supervisor. El Superintendente Contratista y el Residente validan por separado, una vez cada uno.
  - Rechazado: el autor lo corrige y vuelve a revisión. Un administrador puede corregir cualquier decisión.
Contenido:
  - Consolidado automático del periodo (1.º del mes → fecha de corte): reportes diarios, avance real vs
    programado, clima, incidencias, estimaciones y notas de bitácora; más el resumen del Supervisor.
  - Si faltan diarios o hay diarios sin aprobar, se advierte pero no se bloquea el envío.
  - Al aprobarse, el consolidado se congela en `reportes_mensuales.consolidado`; si deja de estar aprobado, se libera.
"""
import calendar
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from . import bitacora, reportes
from .correo import notificar
from .models import Contrato, Estimacion, NotaBitacora, ReporteDiario, ReporteMensual, ValidacionReporteMensual
from .permisos import CREAR_REPORTE, GESTIONAR_USUARIOS, obras_de, puede, roles_en
from .reportes import (
    APROBADO, BORRADOR, CENTAVOS, CERRADOS, EN_REVISION, PENDIENTE, RECHAZADO, ROLES_VALIDADORES, estatus,
)

MESES = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre",
         "Noviembre", "Diciembre")
ROL_DIRECTIVO = "Directivo"


def nombre_mes(reporte):
    return f"{MESES[reporte.mes - 1]} {reporte.anio}"


def ultimo_dia(anio, mes):
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


# ---------------------------------------------------------------------------
# Decisiones y estatus (mismas reglas que el reporte diario)
# ---------------------------------------------------------------------------

def decisiones(reporte):
    """{rol: 'Aprobado' | 'Rechazado' | None} con la última decisión de cada rol validador."""
    resultado = {rol: None for rol in ROLES_VALIDADORES}
    filas = (
        ValidacionReporteMensual.objects.filter(reporte_mensual=reporte, rol_validador__in=ROLES_VALIDADORES)
        .select_related("estatus")
        .order_by("fecha_validacion")
    )
    for v in filas:
        resultado[v.rol_validador] = v.estatus.nombre if v.estatus.nombre in CERRADOS else None
    return resultado


def estado_legible(reporte, decs=None):
    return reportes.estado_legible(reporte, decs if decs is not None else decisiones(reporte))


def roles_que_decide(usuario, obra, reporte, decs=None, es_admin=False):
    decs = decs if decs is not None else decisiones(reporte)
    return reportes.roles_que_decide(usuario, obra, reporte, decs, es_admin)


def _validar(reporte, usuario, rol, nuevo_estatus, comentarios=None):
    # Hora explícita (no now() de la transacción): ordena bien varias decisiones de la misma operación.
    return ValidacionReporteMensual.objects.create(
        reporte_mensual=reporte, usuario_validador=usuario, rol_validador=rol, estatus=nuevo_estatus,
        comentarios=comentarios or None, fecha_validacion=timezone.now(),
    )


def _fijar_estatus(reporte, nombre):
    """Cambia el estatus y congela (o libera) el consolidado según quede aprobado o no."""
    reporte.estatus = estatus(nombre)
    reporte.consolidado = consolidar(reporte) if nombre == APROBADO else None
    reporte.save(update_fields=["estatus", "consolidado"])


def registrar_decision(reporte, usuario, rol, nuevo_estatus, comentarios):
    """Guarda la decisión de un rol y recalcula el estatus. El reporte debe estar bloqueado (select_for_update)."""
    _validar(reporte, usuario, rol, nuevo_estatus, comentarios)
    anterior = reporte.estatus.nombre
    _fijar_estatus(reporte, reportes.estatus_calculado(decisiones(reporte)))
    if reporte.estatus.nombre in CERRADOS and reporte.estatus.nombre != anterior:
        _avisar_resultado(reporte, comentarios)
    return reporte


def enviar_a_revision(reporte, autor):
    """Pasa el reporte a Pendiente (reiniciando decisiones previas) y avisa al Superintendente y al Residente."""
    for rol, decision in decisiones(reporte).items():
        if decision:
            _validar(reporte, autor, rol, estatus(PENDIENTE), "Reenviado a revisión tras una corrección.")
    _fijar_estatus(reporte, PENDIENTE)
    datos = consolidar(reporte)
    texto = (
        f"{autor} envió a revisión el reporte mensual de {nombre_mes(reporte)} de la obra «{reporte.obra.nombre}».\n"
        f"Avance real al corte: {datos['avance']['corte'] or '—'} %."
    )
    if datos["advertencias"]:
        texto += "\n\nAdvertencias:\n- " + "\n- ".join(datos["advertencias"])
    notificar(
        reportes._correos_de(reporte.obra, ROLES_VALIDADORES),
        f"Reporte mensual por revisar · {reporte.obra.nombre} · {nombre_mes(reporte)}",
        texto + "\n\nEntra al sistema para aprobarlo o rechazarlo (Pendientes)." + _enlace(reporte),
    )


# ---------------------------------------------------------------------------
# Correos
# ---------------------------------------------------------------------------

def _enlace(reporte):
    if not settings.SITE_URL:
        return ""
    return "\n\n" + settings.SITE_URL + reverse("mensual_detalle", args=[reporte.obra_id, reporte.pk])


def _avisar_resultado(reporte, comentarios):
    resultado = reporte.estatus.nombre.lower()
    asunto = f"Reporte mensual {resultado} · {reporte.obra.nombre} · {nombre_mes(reporte)}"
    texto = (
        f"El reporte mensual de {nombre_mes(reporte)} de la obra «{reporte.obra.nombre}» fue {resultado}.\n"
        f"Estado: {estado_legible(reporte)}."
    )
    if comentarios:
        texto += f"\n\nComentarios:\n{comentarios}"
    autor = [reporte.supervisor.correo] if reporte.supervisor.activo else []
    if reporte.estatus.nombre == RECHAZADO:
        notificar(autor, asunto, texto + "\n\nCorrígelo en el sistema; al guardarlo volverá a revisión." + _enlace(reporte))
        return
    notificar(autor, asunto, texto + _enlace(reporte))
    datos = reporte.consolidado or {}
    avance = datos.get("avance", {})
    resumen = f"Avance real al corte: {avance.get('corte') or '—'} %"
    if avance.get("programado"):
        resumen += f" · programado: {avance['programado']} % · {avance['texto']}"
    notificar(
        reportes._correos_de(reporte.obra, (ROL_DIRECTIVO,)),
        asunto,
        f"Se aprobó el reporte mensual de {nombre_mes(reporte)} de la obra «{reporte.obra.nombre}».\n"
        f"{resumen}.{_enlace(reporte)}",
    )


# ---------------------------------------------------------------------------
# Consolidado del mes
# ---------------------------------------------------------------------------

def _pct(valor):
    return None if valor is None else str(Decimal(valor).quantize(CENTAVOS))


def _dinero(valor):
    return f"$ {Decimal(valor):,.2f}"


def periodo(reporte):
    inicio = date(reporte.anio, reporte.mes, 1)
    if reporte.obra.fecha_inicio_oficial and reporte.obra.fecha_inicio_oficial > inicio:
        inicio = min(reporte.obra.fecha_inicio_oficial, reporte.fecha_corte)
    return inicio, reporte.fecha_corte


def consolidar(reporte):
    """Cifras del periodo como dict serializable (se guarda tal cual en JSONB al aprobar)."""
    obra = reporte.obra
    inicio, corte = periodo(reporte)
    advertencias = []

    # --- Reportes diarios del periodo (los borradores no cuentan como reporte)
    diarios = list(
        ReporteDiario.objects.filter(obra=obra, fecha_reporte__range=(inicio, corte))
        .exclude(estatus__nombre=BORRADOR).select_related("estatus").order_by("fecha_reporte")
    )
    cuenta = Counter(d.estatus.nombre for d in diarios)
    con_reporte = {d.fecha_reporte for d in diarios}
    dias = (corte - inicio).days + 1
    sin_reporte = [inicio + timedelta(n) for n in range(dias) if inicio + timedelta(n) not in con_reporte]
    sin_aprobar = cuenta[PENDIENTE] + cuenta[EN_REVISION] + cuenta[RECHAZADO]
    if sin_reporte:
        advertencias.append(f"{len(sin_reporte)} de {dias} días del periodo sin reporte diario.")
    if sin_aprobar:
        advertencias.append(f"{sin_aprobar} reporte(s) diario(s) del periodo sin aprobar"
                            f"{f' ({cuenta[RECHAZADO]} rechazado(s))' if cuenta[RECHAZADO] else ''}.")

    # --- Avance físico: último diario aprobado al corte (si no hay, el último enviado, con advertencia)
    aprobados = ReporteDiario.objects.filter(obra=obra, estatus__nombre=APROBADO)
    al_corte = aprobados.filter(fecha_reporte__lte=corte).order_by("-fecha_reporte").first()
    fuente = "aprobado"
    if al_corte is None and diarios:
        al_corte, fuente = diarios[-1], "sin aprobar"
        advertencias.append("No hay reportes diarios aprobados al corte: el avance real sale de uno sin aprobar.")
    previo = aprobados.filter(fecha_reporte__lt=inicio).order_by("-fecha_reporte").first()
    real = al_corte.avance_fisico_real_porcentaje if al_corte else None
    real_inicio = previo.avance_fisico_real_porcentaje if previo else Decimal(0)
    programado = reportes.programado_en(obra, corte)
    avance = {
        "inicio": _pct(real_inicio), "corte": _pct(real), "fecha_corte_real": al_corte and f"{al_corte.fecha_reporte:%d/%m/%Y}",
        "mes": _pct(real - real_inicio) if real is not None else None, "fuente": fuente if al_corte else None,
        "programado": _pct(programado), "diferencia": None, "texto": "", "clase": "",
    }
    if programado is None:
        advertencias.append("La obra no tiene programa de obra: no se calcula la desviación.")
    elif real is not None:
        dif = Decimal(real).quantize(CENTAVOS) - programado
        avance["diferencia"] = _pct(dif)
        avance["texto"] = (f"Atraso de {abs(dif)} puntos porcentuales" if dif < 0
                           else f"Adelanto de {dif} puntos porcentuales" if dif > 0 else "En tiempo")
        avance["clase"] = "atraso" if dif < 0 else "adelanto" if dif > 0 else "tiempo"

    # --- Estimaciones con corte en el periodo y acumulado aprobado
    contrato = Contrato.objects.filter(obra=obra).first()
    estimaciones = None
    if contrato:
        del_mes = Estimacion.objects.filter(contrato=contrato, fecha_corte__range=(inicio, corte)) \
            .select_related("estatus").order_by("numero_estimacion")
        aprobadas_mes = sum((e.subtotal for e in del_mes if e.estatus.nombre == APROBADO), Decimal(0))
        acumulado = sum(
            (e.subtotal for e in Estimacion.objects.filter(
                contrato=contrato, estatus__nombre=APROBADO, fecha_corte__lte=corte)),
            Decimal(0),
        )
        monto = contrato.monto_total_contratado
        estimaciones = {
            "filas": [{"folio": e.folio, "numero": e.numero_estimacion, "corte": f"{e.fecha_corte:%d/%m/%Y}",
                       "subtotal": _dinero(e.subtotal), "estatus": e.estatus.nombre} for e in del_mes],
            "aprobado_mes": _dinero(aprobadas_mes), "acumulado": _dinero(acumulado), "monto": _dinero(monto),
            "avance_financiero": _pct(acumulado * 100 / monto) if monto else None,
        }

    # --- Bitácora del periodo
    notas = list(
        NotaBitacora.objects.filter(obra=obra, fecha_nota__date__range=(inicio, corte))
        .select_related("tipo").order_by("folio")
    )
    estados_notas = Counter(n.estado_firma for n in notas)

    return {
        "periodo": {"inicio": f"{inicio:%d/%m/%Y}", "corte": f"{corte:%d/%m/%Y}", "dias": dias},
        "diarios": {
            "total": len(diarios), "aprobados": cuenta[APROBADO], "sin_aprobar": sin_aprobar,
            "rechazados": cuenta[RECHAZADO], "dias_sin_reporte": len(sin_reporte),
            "fechas_sin_reporte": [f"{f:%d/%m}" for f in sin_reporte],
            "filas": [{"id": str(d.pk), "fecha": f"{d.fecha_reporte:%d/%m/%Y}", "estatus": d.estatus.nombre,
                       "avance": _pct(d.avance_fisico_real_porcentaje)} for d in diarios],
        },
        "avance": avance,
        "clima": Counter(d.clima for d in diarios if d.clima).most_common(),
        "incidencias": [{"fecha": f"{d.fecha_reporte:%d/%m/%Y}", "texto": d.incidencias} for d in diarios if d.incidencias],
        "seguridad": [{"fecha": f"{d.fecha_reporte:%d/%m/%Y}", "texto": d.incidentes_seguridad}
                      for d in diarios if d.incidentes_seguridad],
        "estimaciones": estimaciones,
        "bitacora": {
            "total": len(notas), "firmadas": estados_notas[bitacora.FIRMADA],
            "pendientes": estados_notas[bitacora.PENDIENTE], "rechazadas": estados_notas[bitacora.RECHAZADA],
            "filas": [{"id": str(n.pk), "folio": n.folio, "tipo": n.tipo.nombre, "estado": n.estado_firma,
                       "texto": n.descripcion[:90]} for n in notas],
        },
        "advertencias": advertencias,
        "generado": f"{timezone.localtime():%d/%m/%Y %H:%M}",
    }


def datos(reporte):
    """(consolidado, congelado): las cifras guardadas al aprobar o, si no, calculadas en este momento."""
    if reporte.consolidado:
        return reporte.consolidado, True
    return consolidar(reporte), False


# ---------------------------------------------------------------------------
# Permisos de edición y bandeja «Pendientes»
# ---------------------------------------------------------------------------

def puede_editar(usuario, obra, reporte):
    """Edita el autor (o un administrador de la obra) mientras el reporte no esté aprobado."""
    if reporte.estatus.nombre == APROBADO or not puede(usuario, obra, CREAR_REPORTE):
        return False
    return reporte.supervisor_id == usuario.pk or puede(usuario, obra, GESTIONAR_USUARIOS)


def pendientes_de(usuario):
    """Reportes mensuales que requieren acción del usuario: ('revisar', 'corregir')."""
    revisar, corregir = [], []
    for obra in obras_de(usuario).order_by("nombre"):
        mis_roles = [r for r in ROLES_VALIDADORES if r in roles_en(usuario, obra)]
        if mis_roles:
            candidatos = ReporteMensual.objects.filter(obra=obra, estatus__nombre__in=(PENDIENTE, EN_REVISION))
            for r in candidatos.select_related("supervisor", "estatus", "obra").order_by("anio", "mes"):
                decs = decisiones(r)
                if any(decs[rol] is None for rol in mis_roles):
                    revisar.append((r, estado_legible(r, decs)))
        if puede(usuario, obra, CREAR_REPORTE):
            propios = ReporteMensual.objects.filter(
                obra=obra, supervisor=usuario, estatus__nombre__in=(BORRADOR, RECHAZADO)
            )
            for r in propios.select_related("supervisor", "estatus", "obra").order_by("anio", "mes"):
                corregir.append((r, estado_legible(r)))
    return revisar, corregir
