"""
Clima del día en la ubicación de la obra (Open-Meteo, gratuito y sin clave de API).

GUARDADO PARA DESPUÉS: no está conectado a ninguna pantalla. Si se decide usarlo, se puede llamar desde el
formulario del reporte diario para sugerir el clima y la temperatura del día con las coordenadas de la obra:

    from obras.clima import clima_del_dia
    datos = clima_del_dia(obra.latitud, obra.longitud, reporte.fecha_reporte)
    # {'clima': 'Lluvioso', 'temp_max': 24.1, 'temp_min': 12.3, 'precipitacion_mm': 3.2}

Si la obra no tiene coordenadas o el servicio no responde, devuelve None (nunca truena).
"""
import json
import logging
from datetime import date
from urllib.parse import urlencode
from urllib.request import urlopen

logger = logging.getLogger(__name__)

URL = "https://api.open-meteo.com/v1/forecast"
URL_HISTORICO = "https://archive-api.open-meteo.com/v1/archive"

# Códigos WMO de Open-Meteo agrupados en los valores del campo «clima» del reporte diario.
_GRUPOS = (
    ((0,), "Soleado"),
    ((1, 2, 3), "Nublado"),
    ((45, 48), "Niebla"),
    ((51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82), "Lluvioso"),
    ((71, 73, 75, 77, 85, 86), "Nevado"),
    ((95, 96, 99), "Tormenta"),
)


def nombre_clima(codigo):
    for codigos, nombre in _GRUPOS:
        if codigo in codigos:
            return nombre
    return None


def clima_del_dia(latitud, longitud, dia=None, tiempo_espera=5):
    """Clima de un día (por omisión hoy) en esas coordenadas, o None si no se puede obtener."""
    if latitud is None or longitud is None:
        return None
    dia = dia or date.today()
    url = URL_HISTORICO if (date.today() - dia).days > 5 else URL
    parametros = {
        "latitude": float(latitud), "longitude": float(longitud), "timezone": "America/Mexico_City",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
        "start_date": dia.isoformat(), "end_date": dia.isoformat(),
    }
    try:
        with urlopen(f"{url}?{urlencode(parametros)}", timeout=tiempo_espera) as respuesta:
            diario = json.load(respuesta)["daily"]
        return {
            "clima": nombre_clima(diario["weather_code"][0]),
            "temp_max": diario["temperature_2m_max"][0],
            "temp_min": diario["temperature_2m_min"][0],
            "precipitacion_mm": diario["precipitation_sum"][0],
        }
    except Exception:  # noqa: BLE001 - el clima es una ayuda; si falla, el usuario lo captura a mano
        logger.warning("No se pudo obtener el clima de Open-Meteo", exc_info=True)
        return None
