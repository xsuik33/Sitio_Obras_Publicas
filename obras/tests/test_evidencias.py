"""Pruebas de evidencias (fotos y PDF) en el almacenamiento. Corren contra PostgreSQL (ver obras/pruebas_bd.py)."""
import json
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from obras import bitacora, reportes
from obras.almacenamiento import SupabaseStorage
from obras.models import (
    CatalogoTipoNota, Evidencia, NotaBitacora, Obra, ReporteDiario, ReporteMensual, Rol, Usuario, UsuarioProyectoRol,
)

JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"x" * 64
MEMORIA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def archivo(nombre="foto.jpg", contenido=JPG, tipo="image/jpeg"):
    return SimpleUploadedFile(nombre, contenido, content_type=tipo)


@override_settings(ADMIN_EMAILS=[], SITE_URL="", STORAGES=MEMORIA, EVIDENCIA_MAX_MB=1, EVIDENCIA_MAX_ARCHIVOS=3)
class BaseEvidencias(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.obra = Obra.objects.create(nombre="Puente Norte")
        cls.otra = Obra.objects.create(nombre="Otra obra")
        cls.supervisor = cls._usuario("supervisor", "Supervisor")
        cls.residente = cls._usuario("residente", reportes.ROL_RESIDENTE)
        cls.superint = cls._usuario("superint", reportes.ROL_SUPERINTENDENTE)
        cls.directivo = cls._usuario("directivo", "Directivo")
        cls.tipo_nota = CatalogoTipoNota.objects.get(nombre="Instrucción")

    @classmethod
    def _usuario(cls, nombre, rol):
        u = Usuario.objects.create_user(f"{nombre}@obras.test", "Clave-segura-123", nombre=nombre.title(), apellidos="Prueba")
        UsuarioProyectoRol.objects.create(usuario=u, obra=cls.obra, rol=Rol.objects.get(nombre=rol))
        return u

    def diario(self, estatus=reportes.BORRADOR, obra=None):
        dia = ReporteDiario.objects.count() + 1
        return ReporteDiario.objects.create(
            obra=obra or self.obra, supervisor=self.supervisor, fecha_reporte=date(2026, 10, dia),
            actividades_realizadas="Trabajos", avance_fisico_real_porcentaje=Decimal("10"),
            estatus=reportes.estatus(estatus),
        )

    def subir(self, usuario, tipo, objeto, *archivos, descripcion=""):
        self.client.force_login(usuario)
        url = reverse("evidencia_subir", args=[self.obra.pk, tipo, objeto.pk])
        return self.client.post(url, {"archivos": list(archivos) or [archivo()], "descripcion": descripcion})

    def ver(self, usuario, evidencia, **params):
        self.client.force_login(usuario)
        return self.client.get(reverse("evidencia_ver", args=[self.obra.pk, evidencia.pk]), params)


class SubidaTests(BaseEvidencias):
    def test_autor_sube_fotos_y_pdf(self):
        reporte = self.diario()
        r = self.subir(self.supervisor, "diario", reporte, archivo(), archivo("plano.pdf", PDF, "application/pdf"),
                       descripcion="Colado de losa")
        self.assertRedirects(r, reverse("reporte_detalle", args=[self.obra.pk, reporte.pk]))
        evs = list(Evidencia.objects.filter(reporte_diario=reporte).order_by("tipo"))
        self.assertEqual([e.tipo for e in evs], ["application/pdf", "image/jpeg"])
        for e in evs:
            self.assertTrue(e.ruta.startswith(f"obras/{self.obra.pk}/diario/{reporte.pk}/"))
            self.assertTrue(default_storage.exists(e.ruta))
            self.assertEqual(e.descripcion, "Colado de losa")
            self.assertEqual(e.usuario_subida, self.supervisor)

    def test_sin_descripcion_usa_el_nombre_del_archivo(self):
        reporte = self.diario()
        self.subir(self.supervisor, "diario", reporte, archivo("losa norte.png", PNG, "image/png"))
        e = Evidencia.objects.get(reporte_diario=reporte)
        self.assertEqual((e.descripcion, e.tipo), ("losa norte.png", "image/png"))
        self.assertTrue(e.ruta.endswith(".png"))

    def test_se_reconoce_el_contenido_no_la_extension(self):
        reporte = self.diario()
        self.subir(self.supervisor, "diario", reporte, archivo("virus.jpg", b"MZ\x90\x00 no es imagen", "image/jpeg"))
        self.subir(self.supervisor, "diario", reporte, archivo("foto.pdf", JPG, "application/pdf"))
        self.assertEqual(list(Evidencia.objects.filter(reporte_diario=reporte).values_list("tipo", flat=True)),
                         ["image/jpeg"])

    def test_limites_de_tamano_y_cantidad(self):
        reporte = self.diario()
        grande = archivo("grande.jpg", JPG + b"\x00" * (1024 * 1024))
        r = self.subir(self.supervisor, "diario", reporte, archivo(), grande)  # uno inválido: no se sube ninguno
        self.assertIn("pesa más de 1 MB", [str(m) for m in r.wsgi_request._messages][0])
        self.subir(self.supervisor, "diario", reporte, *[archivo(f"f{i}.jpg") for i in range(4)])
        self.assertFalse(Evidencia.objects.filter(reporte_diario=reporte).exists())

    def test_solo_quien_puede_editar_sube(self):
        reporte = self.diario(reportes.PENDIENTE)
        self.assertEqual(self.subir(self.directivo, "diario", reporte).status_code, 302)
        self.subir(self.residente, "diario", reporte)
        self.assertFalse(Evidencia.objects.exists())
        aprobado = self.diario(reportes.APROBADO)
        self.subir(self.supervisor, "diario", aprobado)
        self.assertFalse(Evidencia.objects.exists())

    def test_borrador_ajeno_no_es_visible(self):
        reporte = self.diario()
        self.assertEqual(self.subir(self.residente, "diario", reporte).status_code, 403)

    def test_tipo_desconocido_y_registro_de_otra_obra(self):
        self.client.force_login(self.supervisor)
        reporte = self.diario()
        url = reverse("evidencia_subir", args=[self.obra.pk, "estimacion", reporte.pk])
        self.assertEqual(self.client.post(url, {"archivos": [archivo()]}).status_code, 404)
        ajeno = self.diario(obra=self.otra)
        self.assertEqual(self.subir(self.supervisor, "diario", ajeno).status_code, 404)

    def test_rechazado_se_reenvia_a_revision(self):
        reporte = self.diario(reportes.RECHAZADO)
        self.subir(self.supervisor, "diario", reporte)
        reporte.refresh_from_db()
        self.assertEqual(reporte.estatus.nombre, reportes.PENDIENTE)

    def test_pendiente_no_cambia_de_estatus(self):
        reporte = self.diario(reportes.PENDIENTE)
        self.subir(self.supervisor, "diario", reporte)
        reporte.refresh_from_db()
        self.assertEqual(reporte.estatus.nombre, reportes.PENDIENTE)
        self.assertEqual(Evidencia.objects.count(), 1)

    def test_error_del_almacenamiento_no_deja_registros(self):
        from obras.almacenamiento import ErrorAlmacenamiento
        reporte = self.diario()
        with mock.patch("django.core.files.storage.InMemoryStorage._save", side_effect=ErrorAlmacenamiento("caído")):
            r = self.subir(self.supervisor, "diario", reporte)
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Evidencia.objects.exists())


class ConsultaTests(BaseEvidencias):
    def setUp(self):
        self.reporte = self.diario()
        self.subir(self.supervisor, "diario", self.reporte, archivo())
        self.evidencia = Evidencia.objects.get()

    def test_autor_ve_y_descarga(self):
        r = self.ver(self.supervisor, self.evidencia)
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/jpeg"))
        self.assertEqual(b"".join(r.streaming_content), JPG)
        r = self.ver(self.supervisor, self.evidencia, descargar="")
        self.assertIn("attachment", r["Content-Disposition"])

    def test_borrador_solo_lo_ve_su_autor(self):
        self.assertEqual(self.ver(self.directivo, self.evidencia).status_code, 403)
        ReporteDiario.objects.filter(pk=self.reporte.pk).update(estatus=reportes.estatus(reportes.PENDIENTE))
        self.assertEqual(self.ver(self.directivo, self.evidencia).status_code, 200)

    def test_otra_obra_no_la_ve(self):
        ajeno = Usuario.objects.create_user("ajeno@obras.test", "Clave-segura-123", nombre="A", apellidos="B")
        UsuarioProyectoRol.objects.create(usuario=ajeno, obra=self.otra, rol=Rol.objects.get(nombre="Supervisor"))
        self.client.force_login(ajeno)
        self.assertEqual(self.client.get(reverse("evidencia_ver", args=[self.otra.pk, self.evidencia.pk])).status_code, 404)
        self.assertEqual(self.ver(ajeno, self.evidencia).status_code, 403)

    def test_detalle_muestra_la_galeria(self):
        self.client.force_login(self.supervisor)
        r = self.client.get(reverse("reporte_detalle", args=[self.obra.pk, self.reporte.pk]))
        self.assertContains(r, reverse("evidencia_ver", args=[self.obra.pk, self.evidencia.pk]))
        self.assertContains(r, "Subir evidencias")

    def test_quitar_borra_registro_y_archivo(self):
        ruta = self.evidencia.ruta
        self.client.force_login(self.directivo)
        self.client.post(reverse("evidencia_borrar", args=[self.obra.pk, self.evidencia.pk]))
        self.assertTrue(Evidencia.objects.exists())
        self.client.force_login(self.supervisor)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("evidencia_borrar", args=[self.obra.pk, self.evidencia.pk]))
        self.assertFalse(Evidencia.objects.exists())
        self.assertFalse(default_storage.exists(ruta))


class MensualYNotaTests(BaseEvidencias):
    def test_reporte_mensual(self):
        mensual = ReporteMensual.objects.create(obra=self.obra, supervisor=self.supervisor, anio=2026, mes=10,
                                                fecha_corte=date(2026, 10, 31), estatus=reportes.estatus(reportes.BORRADOR))
        r = self.subir(self.supervisor, "mensual", mensual, archivo("avance.pdf", PDF, "application/pdf"))
        self.assertRedirects(r, reverse("mensual_detalle", args=[self.obra.pk, mensual.pk]), fetch_redirect_response=False)
        self.assertEqual(Evidencia.objects.get().reporte_mensual, mensual)

    def nota(self):
        self.client.force_login(self.supervisor)
        self.client.post(reverse("nota_nueva", args=[self.obra.pk]), {"tipo": self.tipo_nota.pk, "descripcion": "Colar"})
        return NotaBitacora.objects.get(obra=self.obra)

    def test_nota_autor_sube_y_al_borrarla_se_borran_los_archivos(self):
        nota = self.nota()
        self.subir(self.supervisor, "nota", nota)
        ruta = Evidencia.objects.get(nota_bitacora=nota).ruta
        self.subir(self.residente, "nota", nota)  # no es el autor
        self.assertEqual(Evidencia.objects.count(), 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.force_login(self.supervisor)
            self.client.post(reverse("nota_borrar", args=[self.obra.pk, nota.pk]))
        self.assertFalse(Evidencia.objects.exists())
        self.assertFalse(default_storage.exists(ruta))

    def test_nota_firmada_por_otros_ya_no_cambia(self):
        nota = self.nota()
        self.client.force_login(self.residente)
        self.client.post(reverse("nota_firmar", args=[self.obra.pk, nota.pk]),
                         {"rol": bitacora.ROL_RESIDENTE, "decision": bitacora.FIRMADA, "comentarios": ""})
        self.subir(self.supervisor, "nota", nota)
        self.assertFalse(Evidencia.objects.exists())


@override_settings(SUPABASE_URL="https://proyecto.supabase.co", SUPABASE_SERVICE_KEY="clave-prueba",
                   SUPABASE_BUCKET="evidencias", SUPABASE_URL_CADUCIDAD=300)
class SupabaseStorageTests(SimpleTestCase):
    def respuesta(self, cuerpo=b"{}"):
        r = mock.MagicMock()
        r.__enter__.return_value.read.return_value = cuerpo
        return r

    def test_subir_firmar_y_borrar(self):
        storage = SupabaseStorage()
        with mock.patch("urllib.request.urlopen", return_value=self.respuesta(b'{"Key":"x"}')) as urlopen:
            nombre = storage.save("obras/1/diario/2/abc.jpg", archivo())
        peticion = urlopen.call_args.args[0]
        self.assertEqual(nombre, "obras/1/diario/2/abc.jpg")
        self.assertEqual(peticion.full_url,
                         "https://proyecto.supabase.co/storage/v1/object/evidencias/obras/1/diario/2/abc.jpg")
        self.assertEqual((peticion.get_method(), peticion.get_header("Content-type")), ("POST", "image/jpeg"))
        self.assertEqual(peticion.get_header("Apikey"), "clave-prueba")
        self.assertIsNone(peticion.get_header("Authorization"))  # clave nueva sb_secret_: solo en apikey
        self.assertEqual(peticion.data, JPG)

        firmada = json.dumps({"signedURL": "/object/sign/evidencias/obras/1/diario/2/abc.jpg?token=t"}).encode()
        with mock.patch("urllib.request.urlopen", return_value=self.respuesta(firmada)) as urlopen:
            url = storage.url_firmada(nombre, descargar="evidencia.jpg")
        self.assertEqual(json.loads(urlopen.call_args.args[0].data), {"expiresIn": 300})
        self.assertEqual(url, "https://proyecto.supabase.co/storage/v1/object/sign/evidencias/obras/1/diario/2/abc.jpg"
                              "?token=t&download=evidencia.jpg")

        with mock.patch("urllib.request.urlopen", return_value=self.respuesta(b"[]")) as urlopen:
            storage.delete(nombre)
        peticion = urlopen.call_args.args[0]
        self.assertEqual((peticion.get_method(), json.loads(peticion.data)), ("DELETE", {"prefixes": [nombre]}))

    def test_clave_antigua_jwt_va_tambien_como_bearer(self):
        with mock.patch("urllib.request.urlopen", return_value=self.respuesta()) as urlopen:
            SupabaseStorage(clave="eyJhbGciOi.prueba").save("a.jpg", archivo())
        self.assertEqual(urlopen.call_args.args[0].get_header("Authorization"), "Bearer eyJhbGciOi.prueba")

    def test_error_http_se_reporta(self):
        import io
        import urllib.error
        from obras.almacenamiento import ErrorAlmacenamiento
        error = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b'{"message":"no"}'))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesMessage(ErrorAlmacenamiento, "403"):
                SupabaseStorage().save("a.jpg", archivo())
