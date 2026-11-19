"""Pruebas del mini proyecto. Correr desde la raíz del repo:  python -m unittest servicio_efirma.test_servicio"""
import os
import unittest
from unittest import mock

from servicio_efirma import efirma
from servicio_efirma.app import app

AUT = {"Authorization": "Bearer secreto"}


@mock.patch.dict(os.environ, {"EFIRMA_TOKEN": "secreto", "EFIRMA_SEMILLA": "semilla-servicio"})
class ServicioTests(unittest.TestCase):
    def setUp(self):
        self.c = app.test_client()

    def emitir(self):
        return self.c.post("/emitir", headers=AUT, json={"nombre": "Ana Prueba", "correo": "ana@obras.test", "pin": "123456"}).json

    def test_salud(self):
        self.assertEqual(self.c.get("/salud").json, {"estado": "ok"})

    def test_emite_firma_y_verifica(self):
        e = self.emitir()
        self.assertEqual(e["correo"], "ana@obras.test")
        r = self.c.post("/firmar", headers=AUT, json={"cadena": "||nota 1||", "certificado": e["certificado"],
                                                      "llave_cifrada": e["llave_cifrada"], "pin": "123456"})
        self.assertEqual(r.status_code, 200)
        v = self.c.post("/verificar", headers=AUT, json={"cadena": "||nota 1||", "sello": r.json["sello"],
                                                          "certificado": e["certificado"]})
        self.assertTrue(v.json["valido"])
        self.assertTrue(efirma.verificar("||nota 1||", r.json["sello"], e["certificado"]))

    def test_sin_token(self):
        r = self.c.post("/emitir", headers={"Authorization": "Bearer otro"}, json={"correo": "a@b.c", "pin": "123456"})
        self.assertEqual(r.status_code, 401)

    def test_pin_incorrecto(self):
        e = self.emitir()
        r = self.c.post("/firmar", headers=AUT, json={"cadena": "x", "certificado": e["certificado"],
                                                      "llave_cifrada": e["llave_cifrada"], "pin": "000000"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("PIN", r.json["error"])

    def test_pin_corto(self):
        r = self.c.post("/emitir", headers=AUT, json={"correo": "a@b.c", "pin": "12"})
        self.assertEqual(r.status_code, 422)

    def test_no_devuelve_el_pin(self):
        self.assertNotIn("pin", self.emitir())


if __name__ == "__main__":
    unittest.main()
