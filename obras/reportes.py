"""
Reglas del reporte diario (CU-03).

Flujo:  Borrador → (enviar) → Pendiente → En revisión → Aprobado / Rechazado
  - El Superintendente Contratista y el Residente validan por separado y cada uno decide UNA vez.
  - Aprobado cuando ambos aprueban; Rechazado en cuanto uno rechaza; En revisión mientras solo uno aprobó.
  - Un administrador puede corregir la decisión de cualquiera de los dos (incluso regresarla a Pendiente).
  - El estatus guardado en reportes_diarios.estatus_id siempre se deriva de las decisiones.
"""
import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .correo import notificar
from .models import (
    CatalogoEstatusValidacion, Contrato, Estimacion, ProgramaObra, ReporteDiario, UsuarioProyectoRol,
    ValidacionReporteDiario,
)
from .permisos import CREAR_REPORTE, GESTIONAR_USUARIOS, es_administrador, obras_de, puede, roles_en

logger = logging.getLogger(__name__)

ROL_SUPERINTENDENTE = "Superintendente Contratista"
ROL_RESIDENTE = "Residente"
ROLES_VALIDADORES = (ROL_SUPERINTENDENTE, ROL_RESIDENTE)
ETIQUETA = {ROL_SUPERINTENDENTE: "Superintendente", ROL_RESIDENTE: "Residente"}

BORRADOR, PENDIENTE, EN_REVISION, APROBADO, RECHAZADO = "Borrador", "Pendiente", "En revisión", "Aprobado", "Rechazado"
CERRADOS = (APROBADO, RECHAZADO)


def estatus(nombre):
    return CatalogoEstatusValidacion.objects.get(nombre=nombre)


# ---------------------------------------------------------------------------
# Decisiones y estatus
# ---------------------------------------------------------------------------

def decisiones(reporte):
    """{rol: 'Aprobado' | 'Rechazado' | None} con la última decisión de cada rol validador."""
    resultado = {rol: None for rol in ROLES_VALIDADORES}
    filas = (
        ValidacionReporteDiario.objects.filter(reporte_diario=reporte, rol_validador__in=ROLES_VALIDADORES)
        .select_related("estatus")
        .order_by("fecha_validacion", "id")
    )
    for v in filas:
        resultado[v.rol_validador] = v.estatus.nombre if v.estatus.nombre in CERRADOS else None
    return resultado


def estatus_calculado(decs):
    valores = list(decs.values())
    if RECHAZADO in valores:
        return RECHAZADO
    if all(v == APROBADO for v in valores):
        return APROBADO
    if APROBADO in valores:
        return EN_REVISION
    return PENDIENTE


def _con_articulo(roles):
    return " y ".join(f"el {ETIQUETA[r]}" for r in roles)


def estado_legible(reporte, decs=None):
    """Texto claro: quién aprobó, quién rechazó y quién falta."""
    nombre = reporte.estatus.nombre
    if nombre == BORRADOR:
        return "Borrador · sin enviar a revisión"
    decs = decs if decs is not None else decisiones(reporte)
    if not any(decs.values()) and nombre in CERRADOS:  # registro anterior a CU-03
        return nombre
    aprobaron = [r for r in ROLES_VALIDADORES if decs[r] == APROBADO]
    rechazaron = [r for r in ROLES_VALIDADORES if decs[r] == RECHAZADO]
    faltan = [r for r in ROLES_VALIDADORES if decs[r] is None]
    if rechazaron:
        texto = ("Rechazaron " if len(rechazaron) > 1 else "Rechazó ") + _con_articulo(rechazaron)
        if aprobaron:
            texto += " · aprobó " + _con_articulo(aprobaron)
        return texto
    if not faltan:
        return "Aprobado por " + _con_articulo(aprobaron)
    if aprobaron:
        return f"Aprobó {_con_articulo(aprobaron)} · falta {_con_articulo(faltan)}"
    return f"Pendiente · faltan {_con_articulo(faltan)}"


def roles_que_decide(usuario, obra, reporte, decs=None, es_admin=False):
    """Roles en nombre de los cuales el usuario puede registrar ahora una decisión."""
    if reporte.estatus.nombre == BORRADOR:
        return []
    decs = decs if decs is not None else decisiones(reporte)
    if es_admin:
        return list(ROLES_VALIDADORES)
    if reporte.estatus.nombre in CERRADOS:
        return []
    propios = roles_en(usuario, obra)
    return [r for r in ROLES_VALIDADORES if r in propios and decs[r] is None]


def registrar_decision(reporte, usuario, rol, nuevo_estatus, comentarios, por_admin=False):
    """
    Guarda la decisión de un rol, recalcula el estatus del reporte y avisa al supervisor si quedó cerrado.
    Si la registra el administrador en nombre de otro rol (por_admin), avisa del cambio a todos los involucrados.
    Debe llamarse dentro de una transacción con el reporte bloqueado (select_for_update).
    """
    ValidacionReporteDiario.objects.create(
        reporte_diario=reporte, usuario_validador=usuario, rol_validador=rol,
        estatus=nuevo_estatus, comentarios=comentarios or None, fecha_validacion=timezone.now(),
    )
    anterior = reporte.estatus.nombre
    reporte.estatus = estatus(estatus_calculado(decisiones(reporte)))
    reporte.save(update_fields=["estatus"])
    if por_admin:
        avisar_correccion(reporte, "diario", usuario, rol, nuevo_estatus, comentarios)
    elif reporte.estatus.nombre in CERRADOS and reporte.estatus.nombre != anterior:
        _avisar_resultado(reporte, usuario, rol, comentarios)
    return reporte


def avisar_correccion(reporte, tipo, admin, rol, nuevo_estatus, motivo):
    """CU-14 / CU-15: el administrador cambió una decisión; se avisa al supervisor y a los validadores."""
    if tipo == "diario":
        que, cuando = "reporte diario", reporte.fecha_reporte.strftime("%d/%m/%Y")
        enlace = _enlace(reporte)
        legible = estado_legible(reporte)
    else:
        from . import mensuales  # import local: mensuales importa este módulo
        que, cuando = "reporte mensual", mensuales.nombre_mes(reporte)
        enlace = mensuales._enlace(reporte)
        legible = mensuales.estado_legible(reporte)
    destinatarios = set(_correos_de(reporte.obra, ROLES_VALIDADORES))
    if reporte.supervisor.activo:
        destinatarios.add(reporte.supervisor.correo)
    destinatarios.discard(admin.correo)
    notificar(
        sorted(destinatarios),
        f"Cambio de estatus en {que} · {reporte.obra.nombre} · {cuando}",
        f"{admin} (administrador) cambió la decisión de {ETIQUETA.get(rol, rol)} a «{nuevo_estatus.nombre}» "
        f"en el {que} de {cuando} de la obra «{reporte.obra.nombre}».\n"
        f"Estado: {legible}.\n\nMotivo:\n{motivo}{enlace}",
    )


# ---------------------------------------------------------------------------
# Envío a revisión y correos
# ---------------------------------------------------------------------------

def _enlace(reporte):
    if not settings.SITE_URL:
        return ""
    return "\n\n" + settings.SITE_URL + reverse("reporte_detalle", args=[reporte.obra_id, reporte.pk])


def _correos_de(obra, roles):
    return [
        a.usuario.correo
        for a in UsuarioProyectoRol.objects.filter(obra=obra, rol__nombre__in=roles).select_related("usuario")
        if a.usuario.activo
    ]


def puede_ver(usuario, obra, reporte):
    """Un borrador (diario o mensual) solo lo ven su autor y los administradores."""
    if reporte.estatus.nombre != BORRADOR:
        return True
    return reporte.supervisor_id == usuario.pk or es_administrador(usuario, obra)


def puede_editar(usuario, obra, reporte):
    """Edita el autor (o un administrador de la obra) mientras el reporte no esté aprobado."""
    if reporte.estatus.nombre == APROBADO or not puede(usuario, obra, CREAR_REPORTE):
        return False
    return reporte.supervisor_id == usuario.pk or puede(usuario, obra, GESTIONAR_USUARIOS)


def enviar_a_revision(reporte, autor):
    """Pasa el reporte a Pendiente (reiniciando decisiones previas) y avisa al Superintendente y al Residente."""
    previas = decisiones(reporte)
    for rol, decision in previas.items():
        if decision:
            ValidacionReporteDiario.objects.create(
                reporte_diario=reporte, usuario_validador=autor, rol_validador=rol,
                estatus=estatus(PENDIENTE), comentarios="Reenviado a revisión tras una corrección.",
                fecha_validacion=timezone.now(),
            )
    reporte.estatus = estatus(PENDIENTE)
    reporte.save(update_fields=["estatus"])
    fecha = reporte.fecha_reporte.strftime("%d/%m/%Y")
    notificar(
        _correos_de(reporte.obra, ROLES_VALIDADORES),
        f"Reporte diario por revisar · {reporte.obra.nombre} · {fecha}",
        f"{autor} envió a revisión el reporte diario del {fecha} de la obra «{reporte.obra.nombre}».\n"
        f"Entra al sistema para aprobarlo o rechazarlo (Pendientes).{_enlace(reporte)}",
    )


def _avisar_resultado(reporte, quien, rol, comentarios):
    fecha = reporte.fecha_reporte.strftime("%d/%m/%Y")
    resultado = reporte.estatus.nombre.lower()
    texto = (
        f"El reporte diario del {fecha} de la obra «{reporte.obra.nombre}» fue {resultado}.\n"
        f"Estado: {estado_legible(reporte)}."
    )
    if comentarios:
        texto += f"\n\nComentarios:\n{comentarios}"
    if reporte.estatus.nombre == RECHAZADO:
        texto += "\n\nCorrígelo en el sistema; al guardarlo volverá a revisión."
    notificar(
        [reporte.supervisor.correo] if reporte.supervisor.activo else [],
        f"Reporte diario {resultado} · {reporte.obra.nombre} · {fecha}",
        texto + _enlace(reporte),
    )


# ---------------------------------------------------------------------------
# Bandeja «Pendientes»
# ---------------------------------------------------------------------------

def pendientes_de(usuario):
    """Reportes que requieren acción del usuario, agrupados en 'revisar' y 'corregir'."""
    revisar, corregir = [], []
    for obra in obras_de(usuario).order_by("nombre"):
        mis_roles = [r for r in ROLES_VALIDADORES if r in roles_en(usuario, obra)]
        if mis_roles:
            candidatos = ReporteDiario.objects.filter(obra=obra, estatus__nombre__in=(PENDIENTE, EN_REVISION))
            for r in candidatos.select_related("supervisor", "estatus", "obra").order_by("fecha_reporte"):
                decs = decisiones(r)
                if any(decs[rol] is None for rol in mis_roles):
                    revisar.append((r, estado_legible(r, decs)))
        if puede(usuario, obra, CREAR_REPORTE):
            propios = ReporteDiario.objects.filter(
                obra=obra, supervisor=usuario, estatus__nombre__in=(BORRADOR, RECHAZADO)
            )
            for r in propios.select_related("supervisor", "estatus", "obra").order_by("fecha_reporte"):
                corregir.append((r, estado_legible(r)))
    return revisar, corregir


# ---------------------------------------------------------------------------
# Programa de obra y desviación
# ---------------------------------------------------------------------------

CENTAVOS = Decimal("0.01")


def interpolar(puntos, fecha):
    """puntos: lista ordenada de (fecha, avance %). Interpolación lineal; fuera del rango, el extremo."""
    if not puntos:
        return None
    if fecha <= puntos[0][0]:
        return Decimal(puntos[0][1]).quantize(CENTAVOS)
    if fecha >= puntos[-1][0]:
        return Decimal(puntos[-1][1]).quantize(CENTAVOS)
    for (f0, v0), (f1, v1) in zip(puntos, puntos[1:]):
        if f0 <= fecha <= f1:
            tramo = Decimal((f1 - f0).days)
            fraccion = Decimal((fecha - f0).days) / tramo if tramo else Decimal(1)
            return (Decimal(v0) + (Decimal(v1) - Decimal(v0)) * fraccion).quantize(CENTAVOS, ROUND_HALF_UP)


ATRASO_CRITICO_DIAS = 1  # desde cuántos días detrás del programa el atraso es crítico (flujo 4a de CU-03)


def puntos_programa(obra):
    puntos = list(
        ProgramaObra.objects.filter(obra=obra).order_by("fecha").values_list("fecha", "avance_programado_porcentaje")
    )
    if puntos and obra.fecha_inicio_oficial and obra.fecha_inicio_oficial < puntos[0][0]:
        puntos.insert(0, (obra.fecha_inicio_oficial, Decimal(0)))  # antes del primer punto, el avance parte de 0
    return puntos


def programado_en(obra, fecha):
    return interpolar(puntos_programa(obra), fecha)


def fecha_programada_para(puntos, avance):
    """Fecha en que el programa alcanza el avance dado (inversa de interpolar). None si el programa nunca lo alcanza."""
    avance = Decimal(avance)
    if not puntos:
        return None
    if avance <= Decimal(puntos[0][1]):
        return puntos[0][0]
    for (f0, v0), (f1, v1) in zip(puntos, puntos[1:]):
        v0, v1 = Decimal(v0), Decimal(v1)
        if v0 <= avance <= v1:
            if v1 == v0:
                return f0
            dias = Decimal((f1 - f0).days) * (avance - v0) / (v1 - v0)
            return f0 + timedelta(days=int(dias.to_integral_value(ROUND_HALF_UP)))
    return None


def dias_atraso(obra, fecha, avance):
    """Días que el avance real va detrás del programa a esa fecha. 0 si va en tiempo o adelantado; None sin programa."""
    puntos = puntos_programa(obra)
    if not puntos or avance is None or fecha is None:
        return None
    if Decimal(avance) >= interpolar(puntos, fecha):  # va en tiempo o adelantado respecto a lo programado a esa fecha
        return 0
    alcanzado = fecha_programada_para(puntos, avance)
    if alcanzado is None:  # el avance real supera todo el programa
        return 0
    return max(0, (fecha - alcanzado).days)


def atraso_critico(dias):
    return dias is not None and dias >= ATRASO_CRITICO_DIAS


def desviacion(reporte):
    """Avance real vs programado a la fecha del reporte. None si la obra aún no tiene programa."""
    prog = programado_en(reporte.obra, reporte.fecha_reporte)
    if prog is None:
        return None
    real = Decimal(reporte.avance_fisico_real_porcentaje).quantize(CENTAVOS)
    dif = (real - prog).quantize(CENTAVOS)
    if dif < 0:
        texto = f"Atraso de {abs(dif)} puntos porcentuales"
    elif dif > 0:
        texto = f"Adelanto de {dif} puntos porcentuales"
    else:
        texto = "En tiempo"
    dias = dias_atraso(reporte.obra, reporte.fecha_reporte, real)
    return {"programado": prog, "real": real, "diferencia": dif, "texto": texto,
            "clase": "atraso" if dif < 0 else "adelanto" if dif > 0 else "tiempo",
            "dias_atraso": dias, "critico": atraso_critico(dias)}


def resumen_financiero(reporte):
    """Avance físico × monto del contrato frente a lo aprobado en estimaciones. None si no hay contrato."""
    contrato = Contrato.objects.filter(obra=reporte.obra).first()
    if not contrato:
        return None
    monto = contrato.monto_total_contratado
    fisico = (monto * Decimal(reporte.avance_fisico_real_porcentaje) / 100).quantize(CENTAVOS, ROUND_HALF_UP)
    estimado = sum(
        (e.subtotal for e in Estimacion.objects.filter(contrato=contrato, estatus__nombre=APROBADO)), Decimal(0)
    )
    return {"monto": monto, "fisico": fisico, "estimado": estimado, "por_estimar": fisico - estimado}
