"""Ayuda para las pruebas: da a un usuario su e.firma del sistema con un PIN conocido."""
PIN = "pin-prueba-123"


def dar_efirma(usuario, pin=PIN):
    from obras import efirma
    return efirma.de(usuario) or efirma.emitir(usuario, pin)
