"""
Servicio de firma con e.firma (mini proyecto vinculado al Sistema de Obras).

Endpoints:
  GET  /salud      → {"estado": "ok"}
  POST /firmar     → multipart: cadena, cer, key, contrasena. Devuelve el sello y los datos del certificado.
  POST /verificar  → JSON: {cadena, sello, certificado}. Devuelve {"valido": true|false}.

Todas las peticiones (menos /salud) llevan el encabezado  Authorization: Bearer <EFIRMA_TOKEN>.
La llave privada y la contraseña solo viven en memoria durante la petición; no se guardan ni se registran.
"""
import hmac
import logging
import os

from flask import Flask, jsonify, request

try:  # como paquete (pruebas) o como script (Render)
    from . import efirma
except ImportError:  # pragma: no cover
    import efirma

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("servicio_efirma")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # .cer y .key pesan unos cuantos KB


def _autorizado():
    token = os.environ.get("EFIRMA_TOKEN", "")
    recibido = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(token) and hmac.compare_digest(token, recibido)


@app.get("/salud")
def salud():
    return jsonify(estado="ok")


@app.post("/firmar")
def firmar():
    if not _autorizado():
        return jsonify(error="No autorizado."), 401
    cadena = request.form.get("cadena", "")
    cer, key = request.files.get("cer"), request.files.get("key")
    if not cadena or not cer or not key:
        return jsonify(error="Faltan la cadena, el .cer o el .key."), 400
    try:
        resultado = efirma.firmar(cadena, cer.read(), key.read(), request.form.get("contrasena", ""))
    except efirma.ErrorEfirma as e:
        return jsonify(error=str(e)), 422
    log.info("Cadena firmada con el certificado %s", resultado["numero"])
    return jsonify(resultado)


@app.post("/verificar")
def verificar():
    if not _autorizado():
        return jsonify(error="No autorizado."), 401
    datos = request.get_json(silent=True) or {}
    return jsonify(valido=efirma.verificar(datos.get("cadena", ""), datos.get("sello", ""), datos.get("certificado", "")))


@app.errorhandler(413)
def muy_grande(_):
    return jsonify(error="Los archivos son demasiado grandes."), 413
