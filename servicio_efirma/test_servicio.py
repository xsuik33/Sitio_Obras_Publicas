"""Pruebas del mini proyecto. Correr desde la raíz del repo:  python -m unittest servicio_efirma.test_servicio"""
import io
import os
import unittest
from unittest import mock

from servicio_efirma.app import app
from servicio_efirma import efirma

# Reutiliza el generador de e.firma de prueba del sistema (mismo formato que el SAT).
from obras.tests.efirma_prueba import CONTRASENA, efirma as generar  # noqa: E402


@mock.patch.dict(os.environ, {"EFIRMA_TOKEN": "secreto"})
class ServicioTests(unittest.TestCase):
    def setUp(self):
        self.c = app.test_client()
        self.cer, self.key = generar()

    def firmar(self, token="secreto", contrasena=CONTRASENA):
        return self.c.post("/firmar", headers={"Authorization": f"Bearer {token}"}, content_type="multipart/form-data",
                           data={"cadena": "||nota 1||", "contrasena": contrasena,
                                 "cer": (io.BytesIO(self.cer), "a.cer"), "key": (io.BytesIO(self.key), "a.key")})

    def test_salud(self):
        self.assertEqual(self.c.get("/salud").json, {"estado": "ok"})

    def test_firma_y_verifica(self):
        r = self.firmar()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json["numero"], "00001000000500000001")
        v = self.c.post("/verificar", headers={"Authorization": "Bearer secreto"},
                        json={"cadena": "||nota 1||", "sello": r.json["sello"], "certificado": r.json["certificado"]})
        self.assertTrue(v.json["valido"])

    def test_sin_token(self):
        self.assertEqual(self.firmar(token="otro").status_code, 401)

    def test_contrasena_incorrecta(self):
        r = self.firmar(contrasena="mala")
        self.assertEqual(r.status_code, 422)
        self.assertIn("contraseña", r.json["error"])

    def test_no_devuelve_la_llave(self):
        self.assertNotIn("key", self.firmar().json)

    def test_sello_corresponde_al_nucleo(self):
        r = self.firmar()
        self.assertTrue(efirma.verificar("||nota 1||", r.json["sello"], r.json["certificado"]))


if __name__ == "__main__":
    unittest.main()
