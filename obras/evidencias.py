"""
Evidencias (fotos y PDF) de reportes diarios, reportes mensuales y notas de bitácora.

Reglas:
  - Ver/descargar: quien puede ver el registro (VER en la obra; un borrador, solo su autor y los administradores).
  - Subir/quitar: quien puede editar el registro (mismas reglas que «Editar»). En un reporte aprobado o una nota
    firmada por otros ya no se pueden cambiar.
  - Si el registro ya tenía decisiones (reporte en revisión o rechazado, nota rechazada), cambiar sus evidencias
    cuenta como corrección: se reenvía a revisión/firma, igual que al editarlo.
  - Solo JPG, PNG, WEBP y PDF, reconocidos por su contenido (no por la extensión), de hasta EVIDENCIA_MAX_MB.
  - Los archivos se guardan en el almacenamiento por defecto (Supabase Storage en producción, ver almacenamiento.py)
    en obras/<obra>/<tipo>/<registro>/<uuid>.<ext>; la tabla «evidencias» guarda la ruta.
"""
import logging
import uuid
from pathlib import PurePath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.shortcuts import get_object_or_404

from . import bitacora, mensuales, reportes
from .models import Evidencia, NotaBitacora, ReporteDiario, ReporteMensual
from .permisos import VER, puede

logger = logging.getLogger(__name__)

# tipo de registro en la URL → (modelo, campo de la evidencia)
PADRES = {
    "diario": (ReporteDiario, "reporte_diario"),
    "mensual": (ReporteMensual, "reporte_mensual"),
    "nota": (NotaBitacora, "nota_bitacora"),
}
FORMATOS = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "pdf": "application/pdf"}
ACEPTA = ".jpg,.jpeg,.png,.webp,.pdf,image/jpeg,image/png,image/webp,application/pdf"


def tipo_de(objeto):
    return next(t for t, (modelo, _) in PADRES.items() if isinstance(objeto, modelo))


def registro(obra, tipo, registro_id, bloquear=False):
    if tipo not in PADRES:
        raise ValueError(tipo)
    modelo = PADRES[tipo][0]
    consulta = modelo.objects.select_related("obra")
    if bloquear:
        consulta = consulta.select_for_update(of=("self",))
    return get_object_or_404(consulta, pk=registro_id, obra=obra)


def registro_de(evidencia):
    return evidencia.reporte_diario or evidencia.reporte_mensual or evidencia.nota_bitacora


def de(objeto):
    return Evidencia.objects.filter(**{PADRES[tipo_de(objeto)][1]: objeto}).select_related("usuario_subida") \
        .order_by("fecha_subida")


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------

def puede_ver(usuario, obra, objeto):
    if not puede(usuario, obra, VER):
        return False
    if isinstance(objeto, NotaBitacora):
        return True
    return reportes.puede_ver(usuario, obra, objeto)


def puede_modificar(usuario, obra, objeto):
    if isinstance(objeto, ReporteDiario):
        return reportes.puede_editar(usuario, obra, objeto)
    if isinstance(objeto, ReporteMensual):
        return mensuales.puede_editar(usuario, obra, objeto)
    return bitacora.puede_editar(usuario, obra, objeto)


# ---------------------------------------------------------------------------
# Archivos
# ---------------------------------------------------------------------------

def _formato(cabecera):
    if cabecera.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP":
        return "webp"
    if cabecera.startswith(b"%PDF-"):
        return "pdf"
    return None


def validar(archivo):
    """Devuelve la extensión real del archivo o lanza ValidationError."""
    maximo = settings.EVIDENCIA_MAX_MB
    if archivo.size > maximo * 1024 * 1024:
        raise ValidationError(f"«{archivo.name}» pesa más de {maximo} MB.")
    if archivo.size == 0:
        raise ValidationError(f"«{archivo.name}» está vacío.")
    archivo.seek(0)
    formato = _formato(archivo.read(16))
    archivo.seek(0)
    if not formato:
        raise ValidationError(f"«{archivo.name}» no es una imagen JPG, PNG o WEBP ni un PDF.")
    return formato


def es_imagen(evidencia):
    return (evidencia.tipo or "").startswith("image/")


def nombre_descarga(evidencia):
    return f"evidencia-{str(evidencia.pk)[:8]}{PurePath(evidencia.ruta).suffix}"


def _borrar_archivos(rutas):
    for ruta in rutas:
        try:
            default_storage.delete(ruta)
        except Exception:  # el registro ya no existe; un archivo huérfano no debe romper la operación
            logger.exception("No se pudo borrar el archivo %s", ruta)


def borrar_archivos_al_confirmar(rutas):
    rutas = list(rutas)
    if rutas:
        transaction.on_commit(lambda: _borrar_archivos(rutas))


# ---------------------------------------------------------------------------
# Operaciones (llamar dentro de transaction.atomic, con el registro bloqueado)
# ---------------------------------------------------------------------------

def _reenviar_si_hay_decisiones(objeto, usuario):
    """Cambiar evidencias de un registro ya revisado es una corrección: vuelve a revisión/firma."""
    if isinstance(objeto, NotaBitacora):
        if objeto.estado_firma == bitacora.RECHAZADA:
            bitacora.guardar_correccion(objeto, usuario)
            return True
        return False
    if objeto.estatus.nombre in (reportes.RECHAZADO, reportes.EN_REVISION):
        (reportes if isinstance(objeto, ReporteDiario) else mensuales).enviar_a_revision(objeto, usuario)
        return True
    return False


def agregar(objeto, archivos, descripcion, usuario):
    """Valida todos, sube los archivos y registra las evidencias. Devuelve (evidencias, reenviado)."""
    if not archivos:
        raise ValidationError("Elige al menos un archivo.")
    if len(archivos) > settings.EVIDENCIA_MAX_ARCHIVOS:
        raise ValidationError(f"Sube como máximo {settings.EVIDENCIA_MAX_ARCHIVOS} archivos a la vez.")
    formatos = [validar(a) for a in archivos]
    tipo = tipo_de(objeto)
    subidas, nuevas = [], []
    try:
        for archivo, formato in zip(archivos, formatos):
            archivo.content_type = FORMATOS[formato]
            ruta = default_storage.save(f"obras/{objeto.obra_id}/{tipo}/{objeto.pk}/{uuid.uuid4().hex}.{formato}", archivo)
            subidas.append(ruta)
            nuevas.append(Evidencia.objects.create(
                ruta=ruta, tipo=FORMATOS[formato], usuario_subida=usuario, **{PADRES[tipo][1]: objeto},
                descripcion=(descripcion or "").strip() or PurePath(archivo.name).name[:255],
            ))
        reenviado = _reenviar_si_hay_decisiones(objeto, usuario)
    except Exception:
        _borrar_archivos(subidas)  # la transacción se revierte: no dejar archivos sin registro
        raise
    return nuevas, reenviado


def quitar(evidencia, usuario):
    objeto = registro_de(evidencia)
    ruta = evidencia.ruta
    evidencia.delete()
    borrar_archivos_al_confirmar([ruta])
    return _reenviar_si_hay_decisiones(objeto, usuario)
