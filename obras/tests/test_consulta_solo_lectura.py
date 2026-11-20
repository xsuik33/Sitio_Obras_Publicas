"""Acceso de solo consulta (admin / admin sin crear cuenta): ve todas las obras y registros, no modifica nada."""
from django.test import override_settings
from django.urls import reverse

from obras.models import NotaBitacora, Obra, Usuario
from obras.tests.test_bitacora import BaseBitacora


@override_settings(CONSULTA_USUARIO="admin", CONSULTA_PASSWORD="admin", CONSULTA_USUARIOS=["admin"],
                   ADMIN_EMAILS=["admin@obras.test"])
class SoloConsultaTests(BaseBitacora):
    def entrar(self):
        self.assertTrue(self.client.login(username="admin", password="admin"))
        self.consulta = Usuario.objects.get(correo="admin")

    def test_entra_sin_que_exista_la_cuenta(self):
        self.assertFalse(Usuario.objects.filter(correo="admin").exists())
        r = self.client.post(reverse("login"), {"username": "admin", "password": "admin"})
        self.assertEqual(r.status_code, 302)
        cuenta = Usuario.objects.get(correo="admin")
        self.assertFalse(cuenta.has_usable_password())  # no se puede entrar por la vía normal
        self.assertTrue(cuenta.es_consulta)

    def test_contrasena_incorrecta_no_entra(self):
        self.assertFalse(self.client.login(username="admin", password="otra"))
        self.assertFalse(Usuario.objects.filter(correo="admin").exists())

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
        self.entrar()
        self.assertFalse(self.consulta.es_admin_panel)
        self.assertNotEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.assertEqual(self.client.get(reverse("usuarios_lista")).status_code, 403)

    def test_si_puede_cerrar_sesion(self):
        self.entrar()
        self.client.post(reverse("logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(CONSULTA_USUARIO="", CONSULTA_USUARIOS=[])
    def test_se_puede_desactivar(self):
        self.assertFalse(self.client.login(username="admin", password="admin"))
