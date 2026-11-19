"""
Núcleo de la e.firma del Sistema de Obras (no es la del SAT y no usa RFC, CURP ni datos fiscales).

El servicio funciona como la autoridad certificadora (AC) del sistema:
  * emitir():   genera para un usuario un par de llaves Ed25519 y un certificado X.509 firmado por la AC,
                que solo lleva su nombre y su correo. La llave privada se devuelve cifrada con el PIN que eligió
                el usuario (PKCS#8 cifrado); el PIN no se guarda en ningún lado.
  * firmar():   descifra la llave con el PIN, comprueba que el certificado sea de la AC del sistema, que esté
                vigente y que la llave le corresponda, y sella la cadena original con Ed25519.
  * verificar(): comprueba que el sello corresponda a la cadena y a un certificado emitido por la AC.

La llave de la AC se deriva de EFIRMA_SEMILLA (un secreto compartido entre el sistema y el servicio), así que no
hay archivos de llaves que guardar: con la misma semilla, la AC es la misma en cualquier servidor.
"""
import base64
import hashlib
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

VIGENCIA_DIAS = 2 * 365
PIN_MINIMO = 6
NOMBRE_AC = "AC del Sistema de Obras"


class ErrorEfirma(Exception):
    """Error que se le puede mostrar al usuario (sin datos sensibles)."""


@dataclass
class DatosCertificado:
    numero: str
    nombre: str
    correo: str
    emisor: str
    valido_desde: str
    valido_hasta: str


# ---------------------------------------------------------------------------
# Autoridad certificadora
# ---------------------------------------------------------------------------

def _semilla(semilla=None):
    semilla = semilla if semilla is not None else os.environ.get("EFIRMA_SEMILLA", "")
    if not semilla:
        raise ErrorEfirma("La e.firma no está configurada (falta EFIRMA_SEMILLA).")
    return semilla


def llave_ac(semilla=None) -> ed25519.Ed25519PrivateKey:
    return ed25519.Ed25519PrivateKey.from_private_bytes(hashlib.sha256(("ac-obras|" + _semilla(semilla)).encode()).digest())


def nombre_ac():
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NOMBRE_AC)])


# ---------------------------------------------------------------------------
# Emisión
# ---------------------------------------------------------------------------

def emitir(nombre: str, correo: str, pin: str, semilla=None, ahora=None, dias=VIGENCIA_DIAS) -> dict:
    """Crea la e.firma de un usuario. Devuelve el certificado y la llave cifrada con el PIN (ambos en base64)."""
    if len(pin or "") < PIN_MINIMO:
        raise ErrorEfirma(f"El PIN de la e.firma debe tener al menos {PIN_MINIMO} caracteres.")
    ac = llave_ac(semilla)
    llave = ed25519.Ed25519PrivateKey.generate()
    ahora = ahora or datetime.now(timezone.utc)
    sujeto = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, nombre.strip()[:64] or correo),
        x509.NameAttribute(NameOID.EMAIL_ADDRESS, correo.strip().lower()),
    ])
    cert = (
        x509.CertificateBuilder().subject_name(sujeto).issuer_name(nombre_ac()).public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(minutes=5)).not_valid_after(ahora + timedelta(days=dias))
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .sign(ac, None)
    )
    cifrada = llave.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                  serialization.BestAvailableEncryption(pin.encode()))
    return {
        "certificado": base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode(),
        "llave_cifrada": base64.b64encode(cifrada).decode(),
        **asdict(datos_certificado(cert)),
    }


# ---------------------------------------------------------------------------
# Lectura y validación
# ---------------------------------------------------------------------------

def cargar_certificado(certificado_b64: str) -> x509.Certificate:
    try:
        return x509.load_der_x509_certificate(base64.b64decode(certificado_b64))
    except (ValueError, TypeError):
        raise ErrorEfirma("El certificado de la e.firma no es válido.") from None


def _atributo(nombre, oid):
    valores = nombre.get_attributes_for_oid(oid)
    return valores[0].value if valores else ""


def datos_certificado(cert: x509.Certificate) -> DatosCertificado:
    return DatosCertificado(
        numero=format(cert.serial_number, "X")[-16:].zfill(16),
        nombre=_atributo(cert.subject, NameOID.COMMON_NAME),
        correo=_atributo(cert.subject, NameOID.EMAIL_ADDRESS),
        emisor=_atributo(cert.issuer, NameOID.COMMON_NAME),
        valido_desde=cert.not_valid_before_utc.isoformat(),
        valido_hasta=cert.not_valid_after_utc.isoformat(),
    )


def emitido_por_ac(cert: x509.Certificate, semilla=None) -> bool:
    try:
        llave_ac(semilla).public_key().verify(cert.signature, cert.tbs_certificate_bytes)
        return cert.issuer == nombre_ac()
    except InvalidSignature:
        return False


def validar_certificado(cert: x509.Certificate, semilla=None, ahora=None):
    if not emitido_por_ac(cert, semilla):
        raise ErrorEfirma("El certificado no fue emitido por la e.firma del sistema.")
    ahora = ahora or datetime.now(timezone.utc)
    if ahora < cert.not_valid_before_utc:
        raise ErrorEfirma("El certificado todavía no es válido.")
    if ahora > cert.not_valid_after_utc:
        raise ErrorEfirma("Tu e.firma está vencida; renuévala en «Mi e.firma».")


def abrir_llave(llave_cifrada_b64: str, pin: str):
    try:
        llave = serialization.load_der_private_key(base64.b64decode(llave_cifrada_b64), password=(pin or "").encode() or None)
    except (ValueError, TypeError):
        raise ErrorEfirma("El PIN de la e.firma es incorrecto.") from None
    if not isinstance(llave, ed25519.Ed25519PrivateKey):
        raise ErrorEfirma("La llave de la e.firma no es válida.")
    return llave


# ---------------------------------------------------------------------------
# Firma y verificación
# ---------------------------------------------------------------------------

def firmar(cadena: str, certificado_b64: str, llave_cifrada_b64: str, pin: str, semilla=None) -> dict:
    """Sella la cadena original con la e.firma del usuario. Devuelve el sello en base64 y los datos del certificado."""
    cert = cargar_certificado(certificado_b64)
    validar_certificado(cert, semilla)
    llave = abrir_llave(llave_cifrada_b64, pin)
    publica = serialization.Encoding.Raw, serialization.PublicFormat.Raw
    if llave.public_key().public_bytes(*publica) != cert.public_key().public_bytes(*publica):
        raise ErrorEfirma("La llave no corresponde al certificado de la e.firma.")
    datos = cadena.encode("utf-8")
    return {
        "sello": base64.b64encode(llave.sign(datos)).decode(),
        "sha256": hashlib.sha256(datos).hexdigest(),
        "certificado": certificado_b64,
        **asdict(datos_certificado(cert)),
    }


def verificar(cadena: str, sello_b64: str, certificado_b64: str, semilla=None) -> bool:
    """True si el sello corresponde a la cadena y a un certificado emitido por la AC del sistema."""
    try:
        cert = cargar_certificado(certificado_b64)
        if not emitido_por_ac(cert, semilla):
            return False
        cert.public_key().verify(base64.b64decode(sello_b64), cadena.encode("utf-8"))
        return True
    except (InvalidSignature, ErrorEfirma, ValueError, TypeError):
        return False
