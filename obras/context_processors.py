import logging

from .cuentas import puede_administrar_cuentas
from . import bitacora, estimaciones, mensuales
from .reportes import pendientes_de

logger = logging.getLogger(__name__)


def navegacion(request):
    usuario = request.user
    if not usuario.is_authenticated:
        return {"puede_cuentas": False, "n_pendientes": 0}
    try:
        revisar, corregir = pendientes_de(usuario)
        notas_firmar, notas_corregir = bitacora.pendientes_de(usuario)
        mens_revisar, mens_corregir = mensuales.pendientes_de(usuario)
        est_revisar, est_corregir = estimaciones.pendientes_de(usuario)
        n = sum(map(len, (revisar, corregir, notas_firmar, notas_corregir, mens_revisar, mens_corregir,
                          est_revisar, est_corregir)))
    except Exception:  # noqa: BLE001 - el menú nunca debe tumbar una página
        logger.exception("No se pudo contar los pendientes")
        n = 0
    return {"puede_cuentas": bool(puede_administrar_cuentas(usuario)), "n_pendientes": n}
