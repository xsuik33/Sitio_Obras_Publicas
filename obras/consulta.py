"""
Consulta del expediente de una obra (CU-12).

Flujo:
  1. El usuario busca la obra por nombre, identificador (inicio del ID o número de contrato) o estado.
  2. Se muestran las obras que coinciden (solo las suyas; 2a: si no hay, se avisa).
  3-4. Al elegir una, se muestra su expediente: avance físico y financiero (programado contra real), contrato,
       catálogo, estimaciones, reportes, bitácora y documentos.
  4a. Lo que el rol no puede ver se oculta: los borradores ajenos de reportes no aparecen (ni en conteos).
  4b. Si una sección falla al cargar, se muestra un aviso en esa sección y se puede reintentar; el resto sigue.
Solo consulta: nada aquí modifica datos.

Estado de la obra (se deriva de sus fechas oficiales):
  Por iniciar   la fecha de inicio aún no llega
  En ejecución  ya inició y no ha llegado la fecha de fin (o no tiene fecha de fin)
  Concluida     ya pasó la fecha de fin
  Sin fechas    no tiene fecha de inicio
"""
import logging
import re
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import CharField, Count, Q, Sum
from django.db.models.functions import Cast
from django.utils import timezone

from . import bitacora, mensuales, reportes
from .models import (
    CatalogoConcepto, Contrato, Documento, Estimacion, NotaBitacora, ProgramaObra, ReporteDiario, ReporteMensual,
)
from .permisos import es_administrador, obras_de

logger = logging.getLogger(__name__)

POR_INICIAR, EN_EJECUCION, CONCLUIDA, SIN_FECHAS = "Por iniciar", "En ejecución", "Concluida", "Sin fechas"
ESTADOS = (EN_EJECUCION, POR_INICIAR, CONCLUIDA, SIN_FECHAS)
CENTAVOS = Decimal("0.01")
RECIENTES = 5


def hoy():
    return timezone.localdate()


def estado_obra(obra, fecha=None):
    fecha = fecha or hoy()
    if not obra.fecha_inicio_oficial:
        return SIN_FECHAS
    if fecha < obra.fecha_inicio_oficial:
        return POR_INICIAR
    if obra.fecha_fin_oficial and fecha > obra.fecha_fin_oficial:
        return CONCLUIDA
    return EN_EJECUCION


def _filtro_estado(estado, fecha):
    if estado == SIN_FECHAS:
        return Q(fecha_inicio_oficial__isnull=True)
    if estado == POR_INICIAR:
        return Q(fecha_inicio_oficial__gt=fecha)
    if estado == CONCLUIDA:
        return Q(fecha_inicio_oficial__lte=fecha, fecha_fin_oficial__lt=fecha)
    if estado == EN_EJECUCION:
        return Q(fecha_inicio_oficial__lte=fecha) & (Q(fecha_fin_oficial__isnull=True) | Q(fecha_fin_oficial__gte=fecha))
    return Q()


def buscar(usuario, texto="", estado=""):
    """Obras del usuario que coinciden con el texto (nombre, ubicación, contrato, contratista o ID) y el estado."""
    consulta = obras_de(usuario)
    texto = (texto or "").strip()
    if texto:
        filtro = (Q(nombre__icontains=texto) | Q(ubicacion__icontains=texto)
                  | Q(contrato__numero_contrato__icontains=texto) | Q(contrato__contratista__icontains=texto))
        if re.fullmatch(r"[0-9a-fA-F-]{4,36}", texto):
            consulta = consulta.annotate(id_texto=Cast("id", CharField()))
            filtro |= Q(id_texto__istartswith=texto.lower())
        consulta = consulta.filter(filtro)
    if estado in ESTADOS:
        consulta = consulta.filter(_filtro_estado(estado, hoy()))
    return consulta.distinct().order_by("nombre")


def identificador(obra):
    return str(obra.pk)[:8].upper()


# ---------------------------------------------------------------------------
# Avance físico y financiero
# ---------------------------------------------------------------------------

def _q(valor):
    return Decimal(valor).quantize(CENTAVOS, ROUND_HALF_UP)


def _pct(parte, total):
    return _q(Decimal(parte) * 100 / Decimal(total)) if total else None


def serie_programada(obra):
    puntos = list(ProgramaObra.objects.filter(obra=obra).order_by("fecha")
                  .values_list("fecha", "avance_programado_porcentaje"))
    if puntos and obra.fecha_inicio_oficial and obra.fecha_inicio_oficial < puntos[0][0]:
        puntos.insert(0, (obra.fecha_inicio_oficial, Decimal(0)))
    return [(f, _q(v)) for f, v in puntos]


def serie_real(obra):
    """Avance físico de los reportes diarios aprobados (el dato oficial)."""
    return [(f, _q(v)) for f, v in ReporteDiario.objects.filter(obra=obra, estatus__nombre=reportes.APROBADO)
            .order_by("fecha_reporte").values_list("fecha_reporte", "avance_fisico_real_porcentaje")]


def avance(obra, contrato=None, fecha=None):
    """Programado contra real a la fecha (físico en %, financiero en $). Las claves valen None si falta el dato."""
    fecha = fecha or hoy()
    programada, real = serie_programada(obra), serie_real(obra)
    prog = reportes.interpolar(programada, fecha) if programada else None
    ultimo = real[-1] if real else None
    fisico_real = ultimo[1] if ultimo else None
    desviacion = _q(fisico_real - prog) if fisico_real is not None and prog is not None else None
    datos = {
        "fecha": fecha, "programado": prog, "real": fisico_real, "fecha_real": ultimo[0] if ultimo else None,
        "desviacion": desviacion, "programada": programada, "serie_real": real, "financiero": None,
    }
    contrato = contrato if contrato is not None else Contrato.objects.filter(obra=obra).first()
    if contrato:
        monto = contrato.monto_total_contratado
        aprobadas = Estimacion.objects.filter(contrato=contrato, estatus__nombre=reportes.APROBADO)
        estimado = aprobadas.aggregate(s=Sum("subtotal"))["s"] or Decimal(0)
        datos["financiero"] = {
            "monto": monto,
            "programado": _q(monto * prog / 100) if prog is not None else None,
            "fisico": _q(monto * fisico_real / 100) if fisico_real is not None else None,
            "estimado": estimado,
            "pct_estimado": _pct(estimado, monto),
            "neto": aprobadas.aggregate(s=Sum("monto_neto"))["s"] or Decimal(0),
        }
    return datos


def clase_desviacion(dif):
    if dif is None:
        return ""
    return "atraso" if dif < 0 else "adelanto" if dif > 0 else "tiempo"


def texto_desviacion(dif):
    if dif is None:
        return "Sin datos suficientes"
    if dif < 0:
        return f"Atraso de {abs(dif)} pts"
    if dif > 0:
        return f"Adelanto de {dif} pts"
    return "En tiempo"


def grafica(datos):
    """Series para la gráfica de avance físico (fechas ISO y porcentajes como número)."""
    serie = lambda puntos: [{"f": f.isoformat(), "v": float(v)} for f, v in puntos]  # noqa: E731
    return {"programado": serie(datos["programada"]), "real": serie(datos["serie_real"]), "hoy": datos["fecha"].isoformat()}


# ---------------------------------------------------------------------------
# Expediente
# ---------------------------------------------------------------------------

def _visibles(usuario, obra, consulta):
    """Oculta los borradores ajenos de reportes (diarios o mensuales) a quien no es administrador."""
    if es_administrador(usuario, obra):
        return consulta
    return consulta.exclude(Q(estatus__nombre=reportes.BORRADOR) & ~Q(supervisor=usuario))


def _conteo(consulta, campo):
    return dict(consulta.values_list(campo).annotate(n=Count("pk")).order_by())


def _contrato(obra, **_):
    contrato = Contrato.objects.filter(obra=obra).first()
    if not contrato:
        return None
    return {"c": contrato, "anticipos": list(contrato.anticipo_set.order_by("fecha_programada", "fecha_registro"))}


def _catalogo(obra, **_):
    conceptos = CatalogoConcepto.objects.filter(obra=obra)
    total = conceptos.aggregate(n=Count("pk"), importe=Sum("importe_contratado"))
    capitulos = list(conceptos.values("capitulo").annotate(n=Count("pk"), importe=Sum("importe_contratado"))
                     .order_by("capitulo"))
    return {"n": total["n"], "importe": total["importe"] or Decimal(0), "capitulos": capitulos}


def _estimaciones(obra, usuario, **_):
    consulta = Estimacion.objects.filter(contrato__obra=obra)
    if not es_administrador(usuario, obra):
        consulta = consulta.exclude(Q(estatus__nombre=reportes.BORRADOR) & ~Q(superintendente=usuario))
    lista = list(consulta.select_related("estatus").order_by("-numero_estimacion"))
    return {"lista": lista, "conteo": _conteo(consulta, "estatus__nombre")}


def _diarios(obra, usuario, **_):
    consulta = _visibles(usuario, obra, ReporteDiario.objects.filter(obra=obra))
    recientes = consulta.select_related("supervisor", "estatus", "obra").order_by("-fecha_reporte")[:RECIENTES]
    return {"n": consulta.count(), "conteo": _conteo(consulta, "estatus__nombre"),
            "recientes": [(r, reportes.estado_legible(r)) for r in recientes]}


def _mensuales(obra, usuario, **_):
    consulta = _visibles(usuario, obra, ReporteMensual.objects.filter(obra=obra))
    recientes = consulta.select_related("supervisor", "estatus", "obra").order_by("-anio", "-mes")[:RECIENTES]
    return {"n": consulta.count(), "conteo": _conteo(consulta, "estatus__nombre"),
            "recientes": [(r, mensuales.nombre_mes(r)) for r in recientes]}


def _bitacora(obra, **_):
    consulta = NotaBitacora.objects.filter(obra=obra)
    recientes = consulta.select_related("tipo", "usuario_creador").order_by("-folio")[:RECIENTES]
    return {"n": consulta.count(), "conteo": _conteo(consulta, "estado_firma"), "recientes": list(recientes)}


def _documentos(obra, usuario, **_):
    consulta = Documento.objects.filter(obra=obra)
    if not es_administrador(usuario, obra):
        consulta = consulta.exclude(Q(estimacion__estatus__nombre=reportes.BORRADOR) & ~Q(estimacion__superintendente=usuario))
    return {"lista": list(consulta.select_related("usuario_subida").order_by("-fecha_subida"))}


SECCIONES = {
    "contrato": _contrato, "catalogo": _catalogo, "estimaciones": _estimaciones, "diarios": _diarios,
    "mensuales": _mensuales, "bitacora": _bitacora, "documentos": _documentos,
}


def _seccion(nombre, funcion, **kwargs):
    try:
        return {"ok": True, "datos": funcion(**kwargs)}
    except Exception:  # 4b: una sección que falla no tumba el expediente
        logger.exception("No se pudo cargar la sección %s del expediente", nombre)
        return {"ok": False, "datos": None}


def expediente(usuario, obra):
    secciones = {nombre: _seccion(nombre, f, obra=obra, usuario=usuario) for nombre, f in SECCIONES.items()}
    contrato = (secciones["contrato"]["datos"] or {}).get("c")
    av = _seccion("avance", lambda **k: avance(obra, contrato), obra=obra)
    if av["ok"]:
        d = av["datos"]
        d["clase"], d["texto"] = clase_desviacion(d["desviacion"]), texto_desviacion(d["desviacion"])
        d["grafica"] = grafica(d)
    secciones["avance"] = av
    return secciones


def resumen_tarjeta(obra):
    """Datos breves para la lista de obras: estado, avance programado/real y contrato."""
    try:
        contrato = Contrato.objects.filter(obra=obra).first()
        datos = avance(obra, contrato)
        return {"estado": estado_obra(obra), "contrato": contrato, "programado": datos["programado"],
                "real": datos["real"], "clase": clase_desviacion(datos["desviacion"]),
                "texto": texto_desviacion(datos["desviacion"]) if datos["desviacion"] is not None else ""}
    except Exception:
        logger.exception("No se pudo resumir la obra %s", obra.pk)
        return {"estado": estado_obra(obra), "error": True}


ESTADO_NOTAS = (bitacora.PENDIENTE, bitacora.FIRMADA, bitacora.RECHAZADA)
ESTADO_REPORTES = (reportes.BORRADOR, reportes.PENDIENTE, reportes.EN_REVISION, reportes.APROBADO, reportes.RECHAZADO)
