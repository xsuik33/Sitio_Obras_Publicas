"""CU-10 (cambio): cada usuario tiene su e.firma del sistema (no la del SAT) y firma las notas con su PIN."""
from datetime import datetime, timedelta, timezone
from unittest import mock

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from obras import bitacora
from obras import efirma as cliente
from obras.models import EfirmaUsuario, FirmaNotaBitacora
from obras.tests import efirma_prueba
from obras.tests.test_bitacora import RES, BaseBitacora
from servicio_efirma import efirma as nucleo

SEMILLA = "semilla-de-prueba"


class NucleoEfirmaTests(SimpleTestCase):
    def emitir(self, **extra):
        return nucleo.emitir("Ana Prueba", "Ana@Obras.test", "123456", semilla=SEMILLA, **extra)

    def test_certificado_solo_con_nombre_y_correo(self):
        e = self.emitir()
        self.assertEqual((e["nombre"], e["correo"], e["emisor"]), ("Ana Prueba", "ana@obras.test", nucleo.NOMBRE_AC))
        cert = nucleo.cargar_certificado(e["certificado"])
        self.assertEqual(len(cert.subject), 2)  # sin RFC, CURP ni datos fiscales
        self.assertNotIn("123456", e["llave_cifrada"])

    def test_firma_y_verifica(self):
        e = self.emitir()
        r = nucleo.firmar("||hola||", e["certificado"], e["llave_cifrada"], "123456", semilla=SEMILLA)
        self.assertTrue(nucleo.verificar("||hola||", r["sello"], e["certificado"], semilla=SEMILLA))
        self.assertFalse(nucleo.verificar("||otra||", r["sello"], e["certificado"], semilla=SEMILLA))

    def test_pin_incorrecto_y_pin_corto(self):
        e = self.emitir()
        with self.assertRaisesMessage(nucleo.ErrorEfirma, "PIN de la e.firma es incorrecto"):
            nucleo.firmar("x", e["certificado"], e["llave_cifrada"], "000000", semilla=SEMILLA)
        with self.assertRaisesMessage(nucleo.ErrorEfirma, "al menos 6"):
            nucleo.emitir("Ana", "ana@obras.test", "123", semilla=SEMILLA)

    def test_certificado_de_otra_autoridad_no_vale(self):
        e = nucleo.emitir("Ana", "ana@obras.test", "123456", semilla="otra-semilla")
        with self.assertRaisesMessage(nucleo.ErrorEfirma, "no fue emitido por la e.firma del sistema"):
            nucleo.firmar("x", e["certificado"], e["llave_cifrada"], "123456", semilla=SEMILLA)
        r = nucleo.firmar("x", e["certificado"], e["llave_cifrada"], "123456", semilla="otra-semilla")
        self.assertFalse(nucleo.verificar("x", r["sello"], e["certificado"], semilla=SEMILLA))

    def test_vencida(self):
        e = self.emitir(ahora=datetime.now(timezone.utc) - timedelta(days=3 * 365))
        with self.assertRaisesMessage(nucleo.ErrorEfirma, "vencida"):
            nucleo.firmar("x", e["certificado"], e["llave_cifrada"], "123456", semilla=SEMILLA)


@override_settings(EFIRMA_SEMILLA=SEMILLA, EFIRMA_URL="")
class FirmaConEfirmaTests(BaseBitacora):
    def test_crear_mi_efirma(self):
        self.client.force_login(self.residente)
        r = self.client.post(reverse("mi_efirma"), {"pin1": "mipin123", "pin2": "mipin123", "password": "Clave-segura-123"})
        self.assertRedirects(r, reverse("mi_efirma"))
        e = EfirmaUsuario.objects.get(usuario=self.residente)
        self.assertNotIn("mipin123", e.llave_cifrada)
        self.assertContains(self.client.get(reverse("mi_efirma")), e.numero)

    def test_crear_exige_contrasena_y_pines_iguales(self):
        self.client.force_login(self.residente)
        r = self.client.post(reverse("mi_efirma"), {"pin1": "mipin123", "pin2": "otro1234", "password": "mala"})
        self.assertContains(r, "Los PIN no coinciden")
        self.assertContains(r, "no es correcta")
        self.assertFalse(EfirmaUsuario.objects.exists())

    def test_pagina_de_firma_pide_pin(self):
        nota = self.crear(self.supervisor)
        self.client.force_login(self.residente)
        r = self.client.get(reverse("nota_firmar", args=[self.obra.pk, nota.pk]))
        self.assertContains(r, "Créala aquí")
        efirma_prueba.dar_efirma(self.residente)
        r = self.client.get(reverse("nota_firmar", args=[self.obra.pk, nota.pk]))
        self.assertContains(r, 'name="pin"')
        self.assertContains(r, "Cadena original")

    def test_firma_guarda_sello_valido(self):
        nota = self.crear(self.supervisor)
        nota = self.firmar(self.residente, nota)
        f = FirmaNotaBitacora.objects.get(nota_bitacora=nota, rol_firmante=RES)
        self.assertTrue(f.con_efirma)
        self.assertEqual(f.certificado_numero, self.residente.efirma.numero)
        self.assertEqual(f.cadena_original, bitacora.cadena_original(nota, RES, self.residente))
        self.assertTrue(cliente.verificar(f))

    def test_sin_efirma_no_firma(self):
        nota = self.crear(self.supervisor)
        self.client.force_login(self.residente)
        r = self.client.post(reverse("nota_firmar", args=[self.obra.pk, nota.pk]),
                             {"rol": RES, "decision": "Firmada", "pin": "123456"})
        self.assertContains(r, "Primero crea tu e.firma")
        self.assertFalse(FirmaNotaBitacora.objects.filter(nota_bitacora=nota, rol_firmante=RES).exists())

    def test_pin_incorrecto_muestra_error(self):
        nota = self.crear(self.supervisor)
        efirma_prueba.dar_efirma(self.residente)
        self.client.force_login(self.residente)
        r = self.client.post(reverse("nota_firmar", args=[self.obra.pk, nota.pk]),
                             {"rol": RES, "decision": "Firmada", "pin": "equivocado"})
        self.assertContains(r, "PIN de la e.firma es incorrecto")
        self.assertFalse(FirmaNotaBitacora.objects.filter(nota_bitacora=nota, rol_firmante=RES).exists())

    def test_rechazo_no_pide_efirma(self):
        nota = self.crear(self.supervisor)
        nota = self.firmar(self.residente, nota, "Rechazada", comentarios="Falta el volumen")
        self.assertEqual(nota.estado_firma, bitacora.RECHAZADA)

    def test_sello_invalido_si_la_nota_cambia(self):
        nota = self.crear(self.supervisor)
        self.firmar(self.residente, nota)
        f = FirmaNotaBitacora.objects.get(nota_bitacora=nota, rol_firmante=RES)
        f.cadena_original = f.cadena_original.replace("Colar", "Demoler")
        self.assertFalse(cliente.verificar(f))

    def test_pdf_de_la_nota(self):
        nota = self.crear(self.supervisor)
        self.firmar(self.residente, nota)
        self.firmar(self.superint, nota)
        self.client.force_login(self.directivo)
        r = self.client.get(reverse("nota_pdf", args=[self.obra.pk, nota.pk]))
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        with mock.patch("obras.views.pdf.nota_bitacora_pdf", return_value=b"%PDF") as generar:
            self.client.get(reverse("nota_pdf", args=[self.obra.pk, nota.pk]))
        self.assertEqual([v for f, v in generar.call_args.args[2] if f.con_efirma], [True, True])

    def test_usa_el_servicio_externo_si_esta_configurado(self):
        nota = self.crear(self.supervisor)
        efirma_prueba.dar_efirma(self.residente)
        with override_settings(EFIRMA_URL="https://efirma.ejemplo", EFIRMA_TOKEN="t"), \
                mock.patch("obras.efirma.urlopen", side_effect=OSError("caído")):
            self.client.force_login(self.residente)
            r = self.client.post(reverse("nota_firmar", args=[self.obra.pk, nota.pk]),
                                 {"rol": RES, "decision": "Firmada", "pin": efirma_prueba.PIN})
        self.assertContains(r, "No se pudo contactar al servicio de e.firma")
        self.assertFalse(FirmaNotaBitacora.objects.filter(nota_bitacora=nota, rol_firmante=RES).exists())
