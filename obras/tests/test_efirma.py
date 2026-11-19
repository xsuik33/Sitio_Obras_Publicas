"""CU-10 (cambio): firma de notas en su propia página con e.firma, sello verificable y PDF de la nota."""
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from obras import bitacora
from obras import efirma as cliente
from obras.models import FirmaNotaBitacora
from obras.tests import efirma_prueba
from obras.tests.test_bitacora import RES, BaseBitacora
from servicio_efirma import efirma as nucleo


class NucleoEfirmaTests(SimpleTestCase):
    def test_firma_y_verifica(self):
        cer, key = efirma_prueba.efirma()
        r = nucleo.firmar("||hola||", cer, key, efirma_prueba.CONTRASENA)
        self.assertEqual((r["numero"], r["rfc"], r["nombre"]), ("00001000000500000001", "REPR800101AB1", "RESIDENTE DE PRUEBA"))
        self.assertTrue(nucleo.verificar("||hola||", r["sello"], r["certificado"]))
        self.assertFalse(nucleo.verificar("||hola cambiada||", r["sello"], r["certificado"]))

    def test_contrasena_incorrecta(self):
        cer, key = efirma_prueba.efirma()
        with self.assertRaisesMessage(nucleo.ErrorEfirma, "contraseña es incorrecta"):
            nucleo.firmar("x", cer, key, "otra")

    def test_llave_de_otro_certificado(self):
        cer, _ = efirma_prueba.efirma()
        _, key_otro = efirma_prueba.efirma(nombre="OTRA PERSONA")
        with self.assertRaisesMessage(nucleo.ErrorEfirma, "no corresponde al certificado"):
            nucleo.firmar("x", cer, key_otro, efirma_prueba.CONTRASENA)

    def test_certificado_vencido(self):
        cer, key = efirma_prueba.efirma(nombre="VENCIDA", dias=-1, inicio=-30)
        with self.assertRaisesMessage(nucleo.ErrorEfirma, "vencido"):
            nucleo.firmar("x", cer, key, efirma_prueba.CONTRASENA)

    def test_archivos_que_no_son_efirma(self):
        with self.assertRaisesMessage(nucleo.ErrorEfirma, "no es un certificado"):
            nucleo.firmar("x", b"basura", b"basura", "x")


class FirmaConEfirmaTests(BaseBitacora):
    def efirma(self, **extra):
        cer, key = efirma_prueba.efirma()
        return {"cer": SimpleUploadedFile("f.cer", cer), "key": SimpleUploadedFile("f.key", key),
                "contrasena": efirma_prueba.CONTRASENA, **extra}

    def test_pagina_propia_de_firma(self):
        nota = self.crear(self.supervisor)
        self.client.force_login(self.residente)
        r = self.client.get(reverse("nota_detalle", args=[self.obra.pk, nota.pk]))
        self.assertContains(r, reverse("nota_firmar", args=[self.obra.pk, nota.pk]))
        r = self.client.get(reverse("nota_firmar", args=[self.obra.pk, nota.pk]))
        self.assertContains(r, "Cadena original")
        self.assertContains(r, 'enctype="multipart/form-data"')

    def test_firma_guarda_sello_valido(self):
        nota = self.crear(self.supervisor)
        nota = self.firmar(self.residente, nota)
        f = FirmaNotaBitacora.objects.get(nota_bitacora=nota, rol_firmante=RES)
        self.assertTrue(f.con_efirma)
        self.assertEqual(f.certificado_numero, "00001000000500000001")
        self.assertEqual(f.cadena_original, bitacora.cadena_original(nota, RES, self.residente))
        self.assertTrue(cliente.verificar(f))

    def test_sin_efirma_no_firma(self):
        nota = self.crear(self.supervisor)
        self.client.force_login(self.residente)
        r = self.client.post(reverse("nota_firmar", args=[self.obra.pk, nota.pk]), {"rol": RES, "decision": "Firmada"})
        self.assertContains(r, "Sube tu certificado")
        self.assertFalse(FirmaNotaBitacora.objects.filter(nota_bitacora=nota, rol_firmante=RES).exists())

    def test_contrasena_incorrecta_muestra_error(self):
        nota = self.crear(self.supervisor)
        self.client.force_login(self.residente)
        r = self.client.post(reverse("nota_firmar", args=[self.obra.pk, nota.pk]),
                             {"rol": RES, "decision": "Firmada", **self.efirma(contrasena="mala")})
        self.assertContains(r, "contraseña es incorrecta")
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
        filas = generar.call_args.args[2]
        self.assertEqual([v for f, v in filas if f.con_efirma], [True, True])

    @override_settings(EFIRMA_URL="https://efirma.ejemplo", EFIRMA_TOKEN="t")
    def test_usa_el_servicio_externo_si_esta_configurado(self):
        nota = self.crear(self.supervisor)
        with mock.patch("obras.efirma.urlopen", side_effect=OSError("caído")):
            self.client.force_login(self.residente)
            r = self.client.post(reverse("nota_firmar", args=[self.obra.pk, nota.pk]),
                                 {"rol": RES, "decision": "Firmada", **self.efirma()})
        self.assertContains(r, "No se pudo contactar al servicio de e.firma")
        self.assertFalse(FirmaNotaBitacora.objects.filter(nota_bitacora=nota, rol_firmante=RES).exists())
