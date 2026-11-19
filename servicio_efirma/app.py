"""
Servicio de e.firma del Sistema de Obras (mini proyecto vinculado a la página).

Es la autoridad certificadora del sistema: emite a cada usuario su propia e.firma (no la del SAT; el certificado
solo lleva nombre y correo) y sella con ella las notas de bitácora.

Endpoints (JSON):
  GET  /salud      → {"estado": "ok"}
  POST /emitir     → {nombre, correo, pin}                          → certificado y llave cifrada con el PIN
  POST /firmar     → {cadena, certificado, llave_cifrada, pin}      → sello y datos del certificado
  POST /verificar  → {cadena, sello, certificado}                   → {"valido": true|false}

Todas (menos /salud) llevan  Authorization: Bearer <EFIRMA_TOKEN>.  El PIN solo vive en memoria durante la
petición; no se guarda ni se registra. El servicio no guarda nada: el sistema conserva el certificado y la
llave cifrada de cada usuario.
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
app.config["MAX_CONTENT_LENGTH"] = 256 * 1024  # un certificado y una llave pesan menos de 2 KB


def _autorizado():
    token = os.environ.get("EFIRMA_TOKEN", "")
    recibido = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(token) and hmac.compare_digest(token, recibido)


@app.get("/salud")
def salud():
    return jsonify(estado="ok")


def _json():
    return request.get_json(silent=True) or {}


@app.post("/emitir")
def emitir():
    if not _autorizado():
        return jsonify(error="No autorizado."), 401
    d = _json()
    if not d.get("correo"):
        return jsonify(error="Falta el correo del usuario."), 400
    try:
        resultado = efirma.emitir(d.get("nombre", ""), d["correo"], d.get("pin", ""))
    except efirma.ErrorEfirma as e:
        return jsonify(error=str(e)), 422
    log.info("e.firma emitida, certificado %s", resultado["numero"])
    return jsonify(resultado)


@app.post("/firmar")
def firmar():
    if not _autorizado():
        return jsonify(error="No autorizado."), 401
    d = _json()
    if not d.get("cadena") or not d.get("certificado") or not d.get("llave_cifrada"):
        return jsonify(error="Faltan la cadena, el certificado o la llave."), 400
    try:
        resultado = efirma.firmar(d["cadena"], d["certificado"], d["llave_cifrada"], d.get("pin", ""))
    except efirma.ErrorEfirma as e:
        return jsonify(error=str(e)), 422
    log.info("Cadena sellada con el certificado %s", resultado["numero"])
    return jsonify(resultado)


@app.post("/verificar")
def verificar():
    if not _autorizado():
        return jsonify(error="No autorizado."), 401
    d = _json()
    return jsonify(valido=efirma.verificar(d.get("cadena", ""), d.get("sello", ""), d.get("certificado", "")))


@app.errorhandler(413)
def muy_grande(_):
    return jsonify(error="La petición es demasiado grande."), 413
