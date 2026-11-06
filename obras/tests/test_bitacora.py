"""Pruebas de la bitácora de obra (CU-09). Corren contra PostgreSQL (ver obras/pruebas_bd.py)."""
from pathlib import Path

from django.conf import settings
from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from obras import bitacora
from obras.models import CatalogoTipoNota, FirmaNotaBitacora, NotaBitacora, Obra, Rol, Usuario, UsuarioProyectoRol

RES, SUP, SINT = bitacora.ROL_RESIDENTE, bitacora.ROL_SUPERVISOR, bitacora.ROL_SUPERINTENDENTE


@override_settings(ADMIN_EMAILS=[], SITE_URL="")
class BaseBitacora(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.obra = Obra.objects.create(nombre="Puente Norte")
        cls.otra_obra = Obra.objects.create(nombre="Camino Sur")
        cls.residente = cls._usuario("residente", RES)
        cls.supervisor = cls._usuario("supervisor", SUP)
        cls.superint = cls._usuario("superint", SINT)
        cls.admin = cls._usuario("admin", "Administrador")
        cls.directivo = cls._usuario("directivo", "Directivo")
        cls.tipo = CatalogoTipoNota.objects.get(nombre="Instrucción")

    @classmethod
    def _usuario(cls, nombre, rol, obra=None):
        u = Usuario.objects.create_user(f"{nombre}@obras.test", "Clave-segura-123", nombre=nombre.title(), apellidos="Prueba")
        UsuarioProyectoRol.objects.create(usuario=u, obra=obra or cls.obra, rol=Rol.objects.get(nombre=rol))
        return u

    def crear(self, autor, texto="Colar la losa del claro 2", obra=None, **extra):
        self.client.force_login(autor)
        obra = obra or self.obra
        datos = {"tipo": self.tipo.pk, "descripcion": texto, **extra}
        r = self.client.post(reverse("nota_nueva", args=[obra.pk]), datos)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        return NotaBitacora.objects.filter(obra=obra).order_by("-folio").first()

    def firmar(self, usuario, nota, decision="Firmada", rol=None, comentarios=""):
        self.client.force_login(usuario)
        rol = rol or next(r for r in bitacora.ROLES_FIRMANTES if r in bitacora.roles_en(usuario, nota.obra))
        self.client.post(
            reverse("nota_firmar", args=[nota.obra_id, nota.pk]),
            {"rol": rol, "decision": decision, "comentarios": comentarios},
        )
        nota.refresh_from_db()
        return nota

    def destinatarios(self):
        return sorted(d for m in mail.outbox for d in m.to)


class CrearNotaTests(BaseBitacora):
    def test_folio_consecutivo_por_obra_y_no_se_reutiliza(self):
        n1 = self.crear(self.supervisor)
        n2 = self.crear(self.supervisor)
        self.assertEqual((n1.folio, n2.folio), (1, 2))
        self.client.post(reverse("nota_borrar", args=[self.obra.pk, n2.pk]))
        self.assertFalse(NotaBitacora.objects.filter(pk=n2.pk).exists())
        self.assertEqual(self.crear(self.supervisor).folio, 3)
        self._usuario("sup_sur", SUP, obra=self.otra_obra)
        otra = self.crear(Usuario.objects.get(correo="sup_sur@obras.test"), obra=self.otra_obra)
        self.assertEqual(otra.folio, 1)

    def test_autor_firma_y_se_avisa_a_los_demas(self):
        nota = self.crear(self.supervisor)
        self.assertEqual(nota.estado_firma, bitacora.PENDIENTE)
        decs = bitacora.decisiones(bitacora.firmas_vigentes(nota))
        self.assertEqual(decs, {RES: None, SUP: "Firmada", SINT: None})
        self.assertEqual(self.destinatarios(), ["residente@obras.test", "superint@obras.test"])
        self.assertIn("folio 1", mail.outbox[0].subject)

    def test_administrador_no_firma_al_crear(self):
        nota = self.crear(self.admin)
        decs = bitacora.decisiones(bitacora.firmas_vigentes(nota))
        self.assertEqual(decs, {RES: None, SUP: None, SINT: None})
        self.assertEqual(len(self.destinatarios()), 3)

    def test_directivo_no_crea_ni_firma(self):
        self.client.force_login(self.directivo)
        self.assertEqual(self.client.get(reverse("nota_nueva", args=[self.obra.pk])).status_code, 403)
        nota = self.crear(self.supervisor)
        self.client.force_login(self.directivo)
        r = self.client.post(reverse("nota_firmar", args=[self.obra.pk, nota.pk]), {"rol": RES, "decision": "Firmada"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get(reverse("nota_detalle", args=[self.obra.pk, nota.pk])).status_code, 200)

    def test_nota_relacionada_debe_ser_de_la_misma_obra(self):
        self._usuario("sup_sur", SUP, obra=self.otra_obra)
        ajena = self.crear(Usuario.objects.get(correo="sup_sur@obras.test"), obra=self.otra_obra)
        self.client.force_login(self.supervisor)
        r = self.client.post(reverse("nota_nueva", args=[self.obra.pk]),
                             {"tipo": self.tipo.pk, "descripcion": "x", "nota_relacionada": ajena.pk})
        self.assertEqual(r.status_code, 200)
        self.assertIn("nota_relacionada", r.context["form"].errors)
        previa = self.crear(self.supervisor)
        respuesta = self.crear(self.residente, nota_relacionada=previa.pk)
        self.assertEqual(respuesta.nota_relacionada_id, previa.pk)
        detalle = self.client.get(reverse("nota_detalle", args=[self.obra.pk, previa.pk]))
        self.assertContains(detalle, f"Nota {respuesta.folio}")


class FirmaTests(BaseBitacora):
    def test_firmada_cuando_todos_firman_y_avisa_al_autor(self):
        nota = self.crear(self.supervisor)
        nota = self.firmar(self.residente, nota)
        self.assertEqual(nota.estado_firma, bitacora.PENDIENTE)
        mail.outbox.clear()
        nota = self.firmar(self.superint, nota)
        self.assertEqual(nota.estado_firma, bitacora.FIRMADA)
        self.assertEqual(self.destinatarios(), ["supervisor@obras.test"])
        self.assertIn("firmada", mail.outbox[0].subject)

    def test_cada_rol_decide_una_sola_vez(self):
        nota = self.crear(self.supervisor)
        self.firmar(self.residente, nota)
        self.firmar(self.residente, nota, "Rechazada", comentarios="cambié de opinión")
        self.assertEqual(nota.estado_firma, bitacora.PENDIENTE)
        self.assertEqual(FirmaNotaBitacora.objects.filter(nota_bitacora=nota, rol_firmante=RES).count(), 1)

    def test_rechazo_exige_motivo_y_avisa_al_autor(self):
        nota = self.crear(self.supervisor)
        nota = self.firmar(self.residente, nota, "Rechazada")
        self.assertEqual(nota.estado_firma, bitacora.PENDIENTE)  # sin motivo no se registra
        mail.outbox.clear()
        nota = self.firmar(self.residente, nota, "Rechazada", comentarios="Falta indicar el eje")
        self.assertEqual(nota.estado_firma, bitacora.RECHAZADA)
        self.assertEqual(self.destinatarios(), ["supervisor@obras.test"])
        self.assertIn("Falta indicar el eje", mail.outbox[0].body)
        # tras el rechazo, los demás ya no firman
        nota = self.firmar(self.superint, nota)
        self.assertEqual(nota.estado_firma, bitacora.RECHAZADA)

    def test_solo_firman_los_roles_presentes_en_la_obra(self):
        UsuarioProyectoRol.objects.filter(obra=self.obra, rol__nombre=SUP).delete()
        nota = self.crear(self.residente)
        nota = self.firmar(self.superint, nota)
        self.assertEqual(nota.estado_firma, bitacora.FIRMADA)

    def test_administrador_corrige_firmas(self):
        nota = self.crear(self.supervisor)
        nota = self.firmar(self.residente, nota, "Rechazada", comentarios="error")
        self.assertEqual(nota.estado_firma, bitacora.RECHAZADA)
        nota = self.firmar(self.admin, nota, "Pendiente", rol=RES)
        self.assertEqual(nota.estado_firma, bitacora.PENDIENTE)
        nota = self.firmar(self.admin, nota, "Firmada", rol=RES)
        nota = self.firmar(self.admin, nota, "Firmada", rol=SINT)
        self.assertEqual(nota.estado_firma, bitacora.FIRMADA)

    def test_pendientes_muestra_notas_por_firmar_y_por_corregir(self):
        nota = self.crear(self.supervisor)
        self.client.force_login(self.residente)
        self.assertContains(self.client.get(reverse("pendientes")), reverse("nota_detalle", args=[self.obra.pk, nota.pk]))
        self.firmar(self.residente, nota, "Rechazada", comentarios="no")
        self.client.force_login(self.supervisor)
        r = self.client.get(reverse("pendientes"))
        self.assertEqual(len(r.context["notas_corregir"]), 1)
        self.client.force_login(self.superint)
        self.assertEqual(len(self.client.get(reverse("pendientes")).context["notas_firmar"]), 0)


class EdicionTests(BaseBitacora):
    def editar(self, usuario, nota, texto):
        self.client.force_login(usuario)
        return self.client.post(reverse("nota_editar", args=[self.obra.pk, nota.pk]),
                                {"tipo": self.tipo.pk, "descripcion": texto})

    def test_autor_edita_y_borra_antes_de_otras_firmas(self):
        nota = self.crear(self.supervisor)
        self.assertEqual(self.editar(self.supervisor, nota, "Texto corregido").status_code, 302)
        nota.refresh_from_db()
        self.assertEqual(nota.descripcion, "Texto corregido")
        self.assertIsNotNone(nota.fecha_modificacion)
        self.assertEqual(self.editar(self.residente, nota, "ajeno").status_code, 403)  # solo el autor

    def test_no_se_edita_ni_borra_despues_de_otra_firma(self):
        nota = self.crear(self.supervisor)
        self.firmar(self.residente, nota)
        self.assertEqual(self.editar(self.supervisor, nota, "otro").status_code, 403)
        self.client.post(reverse("nota_borrar", args=[self.obra.pk, nota.pk]))
        self.assertTrue(NotaBitacora.objects.filter(pk=nota.pk).exists())

    def test_rechazada_se_corrige_y_reenvia(self):
        nota = self.crear(self.supervisor)
        self.firmar(self.superint, nota)
        self.firmar(self.residente, nota, "Rechazada", comentarios="Falta el eje")
        mail.outbox.clear()
        self.assertEqual(self.editar(self.supervisor, nota, "Colar la losa del eje B").status_code, 302)
        nota.refresh_from_db()
        self.assertEqual(nota.estado_firma, bitacora.PENDIENTE)
        decs = bitacora.decisiones(bitacora.firmas_vigentes(nota))
        self.assertEqual(decs, {RES: None, SUP: "Firmada", SINT: None})
        self.assertEqual(self.destinatarios(), ["residente@obras.test", "superint@obras.test"])
        self.assertEqual(self.firmar(self.residente, nota).estado_firma, bitacora.PENDIENTE)
        self.assertEqual(self.firmar(self.superint, nota).estado_firma, bitacora.FIRMADA)

    def test_firmada_no_se_edita(self):
        nota = self.crear(self.supervisor)
        self.firmar(self.residente, nota)
        self.firmar(self.superint, nota)
        self.assertEqual(self.editar(self.supervisor, nota, "otro").status_code, 403)


class ListaTests(BaseBitacora):
    def test_lista_y_filtros(self):
        self.crear(self.supervisor, "Colado de zapatas")
        n2 = self.crear(self.supervisor, "Retiro de cimbra")
        self.firmar(self.residente, n2, "Rechazada", comentarios="no")
        self.client.force_login(self.directivo)
        url = reverse("bitacora_lista", args=[self.obra.pk])
        self.assertEqual(len(self.client.get(url).context["filas"]), 2)
        self.assertEqual(len(self.client.get(url, {"estado": "Rechazada"}).context["filas"]), 1)
        self.assertEqual(len(self.client.get(url, {"q": "zapatas"}).context["filas"]), 1)
        self.assertEqual(len(self.client.get(url, {"q": "2"}).context["filas"]), 1)
        detalle = self.client.get(reverse("obra_detalle", args=[self.obra.pk]))
        self.assertContains(detalle, url)

    def test_sin_acceso_a_la_obra(self):
        ajeno = Usuario.objects.create_user("ajeno@obras.test", "Clave-segura-123", nombre="A", apellidos="B")
        self.client.force_login(ajeno)
        self.assertEqual(self.client.get(reverse("bitacora_lista", args=[self.obra.pk])).status_code, 403)


class ScriptIncrementalTests(TestCase):
    def test_cu09_es_idempotente(self):
        sql = (Path(settings.BASE_DIR) / "db" / "cu09_bitacora.sql").read_text(encoding="utf-8")
        with connection.cursor() as cursor:
            cursor.execute(sql)
            cursor.execute(sql)
            cursor.execute("SELECT count(*) FROM catalogo_tipos_nota")
            self.assertEqual(cursor.fetchone()[0], 7)
