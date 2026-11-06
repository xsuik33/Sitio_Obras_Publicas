"""Pruebas de la versión HTML de los avisos por correo (no tocan la base de datos)."""
from django.core import mail
from django.test import SimpleTestCase

from obras.correo import _bloques, html_aviso, notificar

RECHAZO = (
    "El reporte diario del 07/11/2026 de la obra «Prueba_Obra» fue rechazado.\n"
    "Estado: Rechazado · lo rechazó el Superintendente.\n\n"
    "Comentarios:\nFaltan fotos <del> colado.\n\n"
    "Corrígelo en el sistema; al guardarlo volverá a revisión.\n\n"
    "https://sistema-obras1.onrender.com/obras/1/reportes/2/"
)


class AvisoHtmlTests(SimpleTestCase):
    def test_bloques(self):
        bloques, enlace = _bloques(RECHAZO)
        self.assertEqual(enlace, "https://sistema-obras1.onrender.com/obras/1/reportes/2/")
        self.assertEqual([b["tipo"] for b in bloques], ["texto", "estado", "cita", "texto"])
        self.assertEqual(bloques[1]["texto"], "Rechazado · lo rechazó el Superintendente")
        self.assertEqual(bloques[2]["etiqueta"], "Comentarios")

    def test_advertencias_como_lista(self):
        bloques, _ = _bloques("Se envió el mensual.\n\nAdvertencias:\n- Faltan 3 días\n- Hay diarios sin aprobar")
        self.assertEqual(bloques[1], {"tipo": "lista", "etiqueta": "Advertencias",
                                      "items": ["Faltan 3 días", "Hay diarios sin aprobar"]})

    def test_html_con_titulo_boton_y_texto_escapado(self):
        html = html_aviso("Reporte diario rechazado · Prueba_Obra · 07/11/2026", RECHAZO)
        self.assertIn("Reporte diario rechazado</h1>", html)
        self.assertIn("Prueba_Obra · 07/11/2026", html)
        self.assertIn("Requiere corrección", html)
        self.assertIn("Corregir en el sistema", html)
        self.assertIn('href="https://sistema-obras1.onrender.com/obras/1/reportes/2/"', html)
        self.assertIn("&lt;del&gt;", html)  # el texto de los usuarios se escapa
        self.assertNotIn("<del>", html)

    def test_notificar_envia_texto_y_html(self):
        notificar(["A@obras.test", "a@obras.test", ""], "Nota de bitácora por firmar · Obra · folio 3", "Firma la nota.")
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ["a@obras.test"])
        self.assertEqual(correo.body, "Firma la nota.")
        html, tipo = correo.alternatives[0]
        self.assertEqual(tipo, "text/html")
        self.assertIn("Por firmar", html)
        self.assertNotIn("Firmar ahora", html)  # sin enlace no hay botón
