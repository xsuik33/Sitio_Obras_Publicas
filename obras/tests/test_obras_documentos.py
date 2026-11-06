"""Pruebas del repositorio de documentos (CU-11), del historial y de las pantallas de obra, contrato y anticipos."""
from datetime import date
from decimal import Decimal

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from obras import historial, reportes
from obras.models import Anticipo, CatalogoConcepto, Contrato, Documento, Estimacion, Obra, Rol, Usuario, UsuarioProyectoRol

MEMORIA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
PDF = b"%PDF-1.7\n" + b"x" * 64


@override_settings(ADMIN_EMAILS=["root@obras.test"], SITE_URL="", STORAGES=MEMORIA, DOCUMENTO_MAX_MB=1)
class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.obra = Obra.objects.create(nombre="Puente Norte")
        cls.supervisor = cls._usuario("supervisor", "Supervisor")
        cls.residente = cls._usuario("residente", reportes.ROL_RESIDENTE)
        cls.directivo = cls._usuario("directivo", "Directivo")
        cls.admin = cls._usuario("admin", "Administrador")
        cls.root = Usuario.objects.create_user("root@obras.test", "Clave-segura-123", nombre="Root", apellidos="Sistema")

    @classmethod
    def _usuario(cls, nombre, rol):
        u = Usuario.objects.create_user(f"{nombre}@obras.test", "Clave-segura-123", nombre=nombre.title(), apellidos="Prueba")
        UsuarioProyectoRol.objects.create(usuario=u, obra=cls.obra, rol=Rol.objects.get(nombre=rol))
        return u

    def subir(self, usuario, contenido=PDF, nombre="plano.pdf", categoria="Plano"):
        self.client.force_login(usuario)
        archivo = SimpleUploadedFile(nombre, contenido)
        return self.client.post(reverse("documento_subir", args=[self.obra.pk]),
                                {"archivo": archivo, "categoria": categoria, "descripcion": "Planta general"})


class RepositorioTests(Base):
    def test_subir_consultar_y_borrar(self):
        self.subir(self.supervisor)
        doc = Documento.objects.get()
        self.assertEqual((doc.categoria, doc.tipo_archivo, doc.usuario_subida), ("Plano", "PDF", self.supervisor))
        self.assertTrue(default_storage.exists(doc.ruta_almacenamiento))
        # el Directivo lo consulta pero no puede subir ni borrar
        self.client.force_login(self.directivo)
        r = self.client.get(reverse("documentos_lista", args=[self.obra.pk]))
        self.assertContains(r, "plano.pdf")
        self.assertIsNone(r.context["form"])
        self.assertEqual(self.subir(self.directivo).status_code, 403)
        self.client.force_login(self.residente)  # no lo subió y no es administrador
        self.client.post(reverse("documento_borrar", args=[self.obra.pk, doc.pk]))
        self.assertTrue(Documento.objects.exists())
        self.client.force_login(self.supervisor)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("documento_borrar", args=[self.obra.pk, doc.pk]))
        self.assertFalse(Documento.objects.exists())
        self.assertFalse(default_storage.exists(doc.ruta_almacenamiento))
        mensajes = [m.change_message for m in historial.de_obra(self.obra)]
        self.assertEqual(mensajes, ["Borró «plano.pdf» (Plano).", "Subió «plano.pdf» (Plano) al repositorio."])

    def test_rechaza_formato_y_tamano(self):
        r = self.subir(self.supervisor, b"MZ\x90\x00" + b"0" * 40, "programa.exe")
        self.assertIn("no es un formato permitido", [str(m) for m in r.wsgi_request._messages][0])
        self.subir(self.supervisor, PDF + b"0" * (1024 * 1024), "enorme.pdf")
        self.assertFalse(Documento.objects.exists())

    def test_filtros(self):
        self.subir(self.supervisor)
        self.subir(self.residente, b"PK\x03\x04" + b"0" * 40, "volumenes.xlsx", "Documento técnico")
        self.client.force_login(self.directivo)
        url = reverse("documentos_lista", args=[self.obra.pk])
        self.assertEqual(len(self.client.get(url, {"categoria": "Plano"}).context["filas"]), 1)
        self.assertEqual(len(self.client.get(url, {"q": "volumen"}).context["filas"]), 1)


class ObraContratoTests(Base):
    def test_registrar_obra_solo_admin_del_sistema(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("obra_nueva")).status_code, 403)
        self.client.force_login(self.root)
        r = self.client.post(reverse("obra_nueva"), {"nombre": "Escuela Sur", "ubicacion": "Guadalupe",
                                                     "fecha_inicio_oficial": "2026-03-01", "fecha_fin_oficial": "2026-01-01"})
        self.assertIn("fecha_fin_oficial", r.context["form"].errors)
        r = self.client.post(reverse("obra_nueva"), {"nombre": "Escuela Sur", "ubicacion": "Guadalupe",
                                                     "fecha_inicio_oficial": "2026-03-01", "fecha_fin_oficial": "2026-12-01"})
        nueva = Obra.objects.get(nombre="Escuela Sur")
        self.assertRedirects(r, reverse("obra_detalle", args=[nueva.pk]))
        self.assertEqual(historial.de_obra(nueva)[0].change_message, "Registró la obra «Escuela Sur».")

    def test_editar_obra_y_contrato(self):
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(reverse("contrato_editar", args=[self.obra.pk])).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse("obra_editar", args=[self.obra.pk]), {"nombre": "Puente Norte II", "ubicacion": "Monterrey"})
        self.obra.refresh_from_db()
        self.assertEqual(self.obra.nombre, "Puente Norte II")
        datos = {"numero_contrato": "LP-1", "contratista": "Alfa", "monto_total_contratado": "1000000",
                 "porcentaje_anticipo": "35", "porcentaje_iva": "16", "porcentaje_retencion": "0.5",
                 "porcentaje_retencion_atraso": "5"}
        r = self.client.post(reverse("contrato_editar", args=[self.obra.pk]), datos)
        self.assertIn("porcentaje_anticipo", r.context["form"].errors)  # máximo 30 % (LOPSRM, art. 50)
        self.client.post(reverse("contrato_editar", args=[self.obra.pk]), {**datos, "porcentaje_anticipo": "30",
                                                                             "monto_garantia_cumplimiento": "100000"})
        contrato = Contrato.objects.get(obra=self.obra)
        self.assertEqual((contrato.monto_anticipo, contrato.monto_garantia_cumplimiento), (Decimal("300000.00"), Decimal("100000.00")))
        self.assertIn("Registró el contrato LP-1", historial.de_obra(self.obra)[0].change_message)

    def test_montos_bloqueados_con_estimaciones(self):
        contrato = Contrato.objects.create(obra=self.obra, numero_contrato="LP-1", contratista="Alfa",
                                           monto_total_contratado=Decimal("1000000"), porcentaje_anticipo=30)
        superint = self._usuario("superint", reportes.ROL_SUPERINTENDENTE)
        Estimacion.objects.create(contrato=contrato, superintendente=superint, numero_estimacion=1, folio="EST-001",
                                  periodo_inicio=date(2026, 1, 1), fecha_corte=date(2026, 1, 31),
                                  estatus=reportes.estatus(reportes.BORRADOR))
        self.client.force_login(self.admin)
        self.client.post(reverse("contrato_editar", args=[self.obra.pk]), {
            "numero_contrato": "LP-1", "contratista": "Alfa SA", "monto_total_contratado": "5", "porcentaje_anticipo": "0",
            "porcentaje_iva": "0", "porcentaje_retencion": "0", "porcentaje_retencion_atraso": "0", "numero_fianza": "F-77"})
        contrato.refresh_from_db()
        self.assertEqual((contrato.monto_total_contratado, contrato.contratista, contrato.numero_fianza),
                         (Decimal("1000000.00"), "Alfa SA", "F-77"))

    def test_anticipos(self):
        contrato = Contrato.objects.create(obra=self.obra, numero_contrato="LP-1", contratista="Alfa",
                                           monto_total_contratado=Decimal("1000000"), porcentaje_anticipo=30)
        self.client.force_login(self.admin)
        url = reverse("anticipo_nuevo", args=[self.obra.pk])
        self.client.post(url, {"ant-tipo": "Inicio de obra", "ant-monto": "350000", "ant-estatus": "Programado"})
        self.assertFalse(Anticipo.objects.exists())  # rebasa el anticipo autorizado (300,000)
        self.client.post(url, {"ant-tipo": "Inicio de obra", "ant-monto": "200000", "ant-estatus": "Pagado"})
        self.assertFalse(Anticipo.objects.exists())  # pagado sin fecha de pago
        self.client.post(url, {"ant-tipo": "Inicio de obra", "ant-monto": "200000", "ant-estatus": "Pagado",
                               "ant-fecha_pago": "2026-01-05"})
        anticipo = Anticipo.objects.get()
        self.client.post(reverse("anticipo_borrar", args=[self.obra.pk, anticipo.pk]))
        self.assertTrue(Anticipo.objects.exists())  # uno pagado no se borra
        self.assertEqual(contrato.anticipo_set.count(), 1)
