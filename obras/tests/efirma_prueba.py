"""Genera una e.firma de prueba con el mismo formato que la del SAT (.cer DER y .key PKCS#8 cifrada en DER)."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CONTRASENA = "Clave-efirma-123"


@lru_cache(maxsize=None)
def efirma(nombre="RESIDENTE DE PRUEBA", rfc="REPR800101AB1", numero="00001000000500000001", dias=365, inicio=-1):
    """Devuelve (cer_der, key_der_cifrada). El número de certificado va como texto ASCII en el serial, igual que el SAT."""
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeto = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, nombre),
        x509.NameAttribute(x509.ObjectIdentifier("2.5.4.45"), f"{rfc} / "),
        x509.NameAttribute(x509.ObjectIdentifier("2.5.4.5"), "REPR800101HDFXXX01 / "),
    ])
    emisor = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AC DE PRUEBA DEL SISTEMA DE OBRAS")])
    ahora = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(sujeto).issuer_name(emisor).public_key(llave.public_key())
        .serial_number(int.from_bytes(numero.encode(), "big"))
        .not_valid_before(ahora + timedelta(days=inicio)).not_valid_after(ahora + timedelta(days=dias))
        .sign(llave, hashes.SHA256())
    )
    cer = cert.public_bytes(serialization.Encoding.DER)
    key = llave.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                              serialization.BestAvailableEncryption(CONTRASENA.encode()))
    return cer, key
