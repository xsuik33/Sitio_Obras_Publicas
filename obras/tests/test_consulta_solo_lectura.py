"""Cuenta de solo consulta: ve todas las obras y registros, pero no puede modificar nada."""
from io import StringIO

from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from obras.models import NotaBitacora, Obra, Usuario
from obras.tests.test_bitacora import BaseBitacora


@override_settings(CONSULTA_USUARIOS=["admin"], ADMIN_EMAILS=["admin@obras.test"])
class SoloConsultaTests(BaseBitacora):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command("crear_consulta", "admin", "--password", "admin", stdout=StringIO())
        cls.consulta = Usuario.objects.get(correo="admin")

    def entrar(self):
        self.assertTrue(self.client.login(username="admin", password="admin"))

    def test_inicia_sesion_con_admin_y_ve_todas_las_obras(self):
        self.entrar()
        r = self.client.get(reverse("mis_obras"))
        self.assertContains(r, "solo consulta")
        for obra in Obra.objects.all():
            self.assertEqual(self.client.get(reverse("obra_detalle", args=[obra.pk])).status_code, 200)

    def test_ve_registros_pero_sin_botones_de_captura(self):
        nota = self.crear(self.supervisor)
        self.entrar()
        r = self.client.get(reverse("bitacora_lista", args=[self.obra.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context["puede_crear"])
        r = self.client.get(reverse("nota_detalle", args=[self.obra.pk, nota.pk]))
        self.assertFalse(r.context["puede_firmar"])
        self.assertEqual(self.client.get(reverse("nota_pdf", args=[self.obra.pk, nota.pk])).status_code, 200)

    def test_no_puede_modificar_nada(self):
        nota = self.crear(self.supervisor)
        self.entrar()
        for url, datos in [
            (reverse("nota_nueva", args=[self.obra.pk]), {"tipo": self.tipo.pk, "descripcion": "x"}),
            (reverse("nota_borrar", args=[self.obra.pk, nota.pk]), {}),
            (reverse("obra_nueva"), {"nombre": "Nueva"}),
            (reverse("mi_efirma"), {"pin1": "123456", "pin2": "123456", "password": "admin"}),
            (reverse("cambiar_password"), {}),
        ]:
            r = self.client.post(url, datos)
            self.assertEqual(r.status_code, 302, url)
        self.assertEqual(NotaBitacora.objects.count(), 1)
        self.assertFalse(Obra.objects.filter(nombre="Nueva").exists())

    def test_no_es_administrador_ni_entra_al_panel(self):
        self.assertFalse(self.consulta.es_admin_panel)
        self.entrar()
        self.assertNotEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.assertEqual(self.client.get(reverse("usuarios_lista")).status_code, 403)

    def test_si_puede_cerrar_sesion(self):
        self.entrar()
        self.client.post(reverse("logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(CONSULTA_USUARIOS=[])
    def test_sin_la_variable_no_ve_obras(self):
        self.entrar()
        self.assertEqual(self.client.get(reverse("obra_detalle", args=[self.obra.pk])).status_code, 403)
