"""
Documentos de la obra (CU-11) y documentos de soporte de una estimación (Reglamento LOPSRM art. 132).

Reglas:
  - Formatos: PDF, Word (.docx), Excel (.xlsx), imágenes (JPG, PNG, WEBP) y planos DWG, de hasta DOCUMENTO_MAX_MB.
    Se reconocen por su contenido; la extensión solo distingue entre formatos que comparten firma (docx/xlsx).
  - Cada documento tiene una categoría (Documento.CATEGORIAS) y queda asociado a la obra; si es soporte de una
    estimación, también a ella.
  - Lo puede borrar quien lo subió o un administrador de la obra (y, si es de una estimación, solo mientras esta
    se pueda editar).
  - Los archivos se guardan en el almacenamiento por defecto (Supabase Storage en producción) en
    obras/<obra>/documentos/<uuid>.<ext>.
"""
import logging
import uuid
from pathlib import PurePath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction

from .models import Documento
from .permisos import es_administrador

logger = logging.getLogger(__name__)

TIPOS = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "jpg": "image/jpeg", "png": "image/png", "webp": "image/webp",
    "dwg": "image/vnd.dwg",
}
ACEPTA = ".pdf,.docx,.xlsx,.jpg,.jpeg,.png,.webp,.dwg"
FORMATOS_LEGIBLES = "PDF, Word (.docx), Excel (.xlsx), JPG, PNG, WEBP o DWG"


def max_mb():
    return getattr(settings, "DOCUMENTO_MAX_MB", 20)


def _formato(cabecera, nombre):
    extension = PurePath(nombre).suffix.lower().lstrip(".")
    if cabecera.startswith(b"%PDF-"):
        return "pdf"
    if cabecera.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP":
        return "webp"
    if cabecera.startswith(b"AC10"):
        return "dwg"
    if cabecera.startswith(b"PK\x03\x04") and extension in ("docx", "xlsx"):  # documentos de Office (zip)
        return extension
    return None


def validar(archivo):
    """Devuelve la extensión real del archivo o lanza ValidationError con un motivo claro (flujo 3a de CU-11)."""
    if archivo.size == 0:
        raise ValidationError(f"«{archivo.name}» está vacío.")
    if archivo.size > max_mb() * 1024 * 1024:
        raise ValidationError(f"«{archivo.name}» pesa más de {max_mb()} MB.")
    archivo.seek(0)
    formato = _formato(archivo.read(16), archivo.name)
    archivo.seek(0)
    if not formato:
        raise ValidationError(f"«{archivo.name}» no es un formato permitido ({FORMATOS_LEGIBLES}).")
    return formato


def guardar(obra, archivo, usuario, categoria, descripcion="", estimacion=None, nombre=None):
    """Valida, sube el archivo y registra el documento. Llamar dentro de transaction.atomic."""
    formato = validar(archivo)
    if categoria not in dict(Documento.CATEGORIAS):
        raise ValidationError("Elige una categoría válida.")
    archivo.content_type = TIPOS[formato]
    ruta = default_storage.save(f"obras/{obra.pk}/documentos/{uuid.uuid4().hex}.{formato}", archivo)
    try:
        return Documento.objects.create(
            obra=obra, usuario_subida=usuario, ruta_almacenamiento=ruta, tipo_archivo=formato.upper(),
            nombre_archivo=((nombre or "").strip() or PurePath(archivo.name).name)[:255],
            descripcion=(descripcion or "").strip() or None, categoria=categoria, estimacion=estimacion,
        )
    except Exception:
        _borrar_archivo(ruta)  # la transacción se revierte: no dejar el archivo sin registro
        raise


def puede_borrar(usuario, documento):
    return documento.usuario_subida_id == usuario.pk or es_administrador(usuario, documento.obra)


def _borrar_archivo(ruta):
    try:
        default_storage.delete(ruta)
    except Exception:
        logger.exception("No se pudo borrar el archivo %s", ruta)


def borrar(documento):
    ruta = documento.ruta_almacenamiento
    documento.delete()
    transaction.on_commit(lambda: _borrar_archivo(ruta))


def nombre_descarga(documento):
    nombre = PurePath(documento.nombre_archivo).stem or "documento"
    return f"{nombre}.{(documento.tipo_archivo or 'bin').lower()}"


def tipo_contenido(documento):
    return TIPOS.get((documento.tipo_archivo or "").lower(), "application/octet-stream")
