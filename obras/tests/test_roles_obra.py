"""Cambio en CU-13: un solo rol por persona en cada obra y sin rol Administrador por obra."""
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from obras.cuentas import puede_administrar_cuentas
from obras.models import Obra, Rol, Usuario, UsuarioProyectoRol


@override_settings(ADMIN_EMAILS=["admin@obras.test"], SITE_URL="")
class RolesObraTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.obra = Obra.objects.create(nombre="Puente Norte")
        cls.otra = Obra.objects.create(nombre="Puente Sur")
        cls.admin = Usuario.objects.create_user("admin@obras.test", "Clave-segura-123", nombre="Ad", apellidos="Min")
        cls.ana = Usuario.objects.create_user("ana@obras.test", "Clave-segura-123", nombre="Ana", apellidos="Prueba")
        cls.sup = Rol.objects.get(nombre="Supervisor")
        cls.res = Rol.objects.get(nombre="Residente")
        cls.adm = Rol.objects.get(nombre="Administrador")

    def asignar(self, usuario, rol, obra=None):
        self.client.force_login(self.admin)
        return self.client.post(reverse("roles_obra", args=[(obra or self.obra).pk]),
                                {"accion": "agregar", "usuario_id": usuario.pk, "rol_id": rol.pk}, follow=True)

    def test_un_solo_rol_por_obra(self):
        self.asignar(self.ana, self.sup)
        r = self.asignar(self.ana, self.res)
        self.assertContains(r, "ya tiene el rol «Supervisor» en esta obra")
        self.assertEqual(list(UsuarioProyectoRol.objects.filter(usuario=self.ana).values_list("rol__nombre", flat=True)),
                         ["Supervisor"])

    def test_otro_rol_en_otra_obra_si_se_permite(self):
        self.asignar(self.ana, self.sup)
        self.asignar(self.ana, self.res, obra=self.otra)
        self.assertEqual(UsuarioProyectoRol.objects.filter(usuario=self.ana).count(), 2)

    def test_no_se_asigna_administrador_en_la_obra(self):
        r = self.asignar(self.ana, self.adm)
        self.assertContains(r, "no se asigna en las obras")
        self.assertFalse(UsuarioProyectoRol.objects.filter(usuario=self.ana).exists())
        self.assertNotIn(self.adm, r.context["roles"])

    def test_desde_la_ficha_tampoco_dos_roles(self):
        UsuarioProyectoRol.objects.create(usuario=self.ana, obra=self.obra, rol=self.sup)
        self.client.force_login(self.admin)
        r = self.client.post(reverse("usuario_detalle", args=[self.ana.pk]),
                             {"accion": "agregar_rol", "obra": self.obra.pk, "rol": self.res.pk})
        self.assertContains(r, "Cada persona tiene un solo rol por obra")
        self.assertEqual(UsuarioProyectoRol.objects.filter(usuario=self.ana).count(), 1)

    def test_rol_administrador_ya_no_da_permisos(self):
        UsuarioProyectoRol.objects.create(usuario=self.ana, obra=self.obra, rol=self.adm)
        self.assertFalse(puede_administrar_cuentas(self.ana))
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse("roles_obra", args=[self.obra.pk])).status_code, 403)

    def test_la_base_impide_dos_roles_en_la_misma_obra(self):
        UsuarioProyectoRol.objects.create(usuario=self.ana, obra=self.obra, rol=self.sup)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UsuarioProyectoRol.objects.create(usuario=self.ana, obra=self.obra, rol=self.res)
