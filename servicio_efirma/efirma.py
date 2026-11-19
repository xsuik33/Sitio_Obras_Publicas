"""
Núcleo de la e.firma: lee el certificado (.cer) y la llave privada (.key) con su contraseña, comprueba que
correspondan y que el certificado esté vigente, y firma o verifica una cadena original con RSA-SHA256.

Los archivos de la e.firma del SAT vienen en DER: el .cer es un certificado X.509 y el .key una llave PKCS#8
cifrada con la contraseña de la e.firma. También se aceptan en PEM. La llave y la contraseña solo se usan en
memoria para firmar; nunca se guardan ni se escriben en los logs.
"""
import base64
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

# OID donde el SAT pone el RFC (x500UniqueIdentifier) y la CURP (serialNumber) del titular.
OID_RFC = x509.ObjectIdentifier("2.5.4.45")
OID_CURP = x509.ObjectIdentifier("2.5.4.5")


class ErrorEfirma(Exception):
    """Error que se le puede mostrar al usuario (sin datos sensibles)."""


@dataclass
class DatosCertificado:
    numero: str
    nombre: str
    rfc: str
    curp: str
    emisor: str
    valido_desde: str
    valido_hasta: str


def cargar_certificado(contenido: bytes) -> x509.Certificate:
    for cargar in (x509.load_der_x509_certificate, x509.load_pem_x509_certificate):
        try:
            return cargar(contenido)
        except ValueError:
            continue
    raise ErrorEfirma("El archivo .cer no es un certificado válido.")


def cargar_llave(contenido: bytes, contrasena: str):
    clave = (contrasena or "").encode()
    for cargar in (serialization.load_der_private_key, serialization.load_pem_private_key):
        try:
            llave = cargar(contenido, password=clave or None)
            break
        except (ValueError, TypeError):
            continue
    else:
        raise ErrorEfirma("No se pudo abrir el archivo .key: la contraseña es incorrecta o el archivo no es una llave privada.")
    if not isinstance(llave, rsa.RSAPrivateKey):
        raise ErrorEfirma("La llave privada no es RSA, como la de la e.firma.")
    return llave


def _atributo(nombre, oid):
    valores = nombre.get_attributes_for_oid(oid)
    return valores[0].value.strip() if valores else ""


def numero_certificado(cert: x509.Certificate) -> str:
    """El SAT codifica el número de certificado (20 dígitos) como texto ASCII dentro del número de serie."""
    hexa = format(cert.serial_number, "x")
    if len(hexa) % 2:
        hexa = "0" + hexa
    try:
        texto = bytes.fromhex(hexa).decode("ascii")
        if texto.isdigit():
            return texto
    except UnicodeDecodeError:
        pass
    return hexa.upper()


def datos_certificado(cert: x509.Certificate) -> DatosCertificado:
    rfc = _atributo(cert.subject, OID_RFC).split("/")[0].strip()
    return DatosCertificado(
        numero=numero_certificado(cert),
        nombre=_atributo(cert.subject, NameOID.COMMON_NAME) or _atributo(cert.subject, NameOID.ORGANIZATION_NAME),
        rfc=rfc,
        curp=_atributo(cert.subject, OID_CURP).split("/")[0].strip(),
        emisor=_atributo(cert.issuer, NameOID.COMMON_NAME) or _atributo(cert.issuer, NameOID.ORGANIZATION_NAME),
        valido_desde=cert.not_valid_before_utc.isoformat(),
        valido_hasta=cert.not_valid_after_utc.isoformat(),
    )


def validar_par(cert: x509.Certificate, llave, ahora=None):
    """La llave debe corresponder al certificado y el certificado debe estar vigente."""
    if llave.public_key().public_numbers() != cert.public_key().public_numbers():
        raise ErrorEfirma("La llave privada (.key) no corresponde al certificado (.cer).")
    ahora = ahora or datetime.now(timezone.utc)
    if ahora < cert.not_valid_before_utc:
        raise ErrorEfirma("El certificado todavía no es válido.")
    if ahora > cert.not_valid_after_utc:
        raise ErrorEfirma("El certificado de la e.firma está vencido.")


def firmar(cadena: str, cer: bytes, key: bytes, contrasena: str) -> dict:
    """Firma la cadena original con la e.firma. Devuelve el sello en base64, su SHA-256 y los datos del certificado."""
    cert = cargar_certificado(cer)
    llave = cargar_llave(key, contrasena)
    validar_par(cert, llave)
    datos = cadena.encode("utf-8")
    sello = llave.sign(datos, padding.PKCS1v15(), hashes.SHA256())
    return {
        "sello": base64.b64encode(sello).decode(),
        "sha256": hashlib.sha256(datos).hexdigest(),
        "certificado": base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode(),
        **asdict(datos_certificado(cert)),
    }


def verificar(cadena: str, sello_b64: str, certificado_b64: str) -> bool:
    """True si el sello corresponde a la cadena y al certificado."""
    try:
        cert = cargar_certificado(base64.b64decode(certificado_b64))
        cert.public_key().verify(base64.b64decode(sello_b64), cadena.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, ErrorEfirma, ValueError, TypeError):
        return False
