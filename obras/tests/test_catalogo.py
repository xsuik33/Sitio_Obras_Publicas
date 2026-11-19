"""CU-01: catálogo de conceptos con unidades de una lista y botón de borrar."""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from obras.models import CatalogoConcepto, Contrato, Obra, Rol, UnidadMedida, Usuario, UsuarioProyectoRol


@override_settings(ADMIN_EMAILS=["admin@obras.test"], SITE_URL="")
class CatalogoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.obra = Obra.objects.create(nombre="Puente Norte")
        Contrato.objects.create(obra=cls.obra, numero_contrato="LP-1", contratista="Constructora",
                                monto_total_contratado=Decimal("1000.00"))
        cls.sint = Usuario.objects.create_user("sint@obras.test", "Clave-segura-123", nombre="Si", apellidos="Nt")
        cls.sup = Usuario.objects.create_user("sup@obras.test", "Clave-segura-123", nombre="Su", apellidos="P")
        UsuarioProyectoRol.objects.create(usuario=cls.sint, obra=cls.obra,
                                          rol=Rol.objects.get(nombre="Superintendente Contratista"))
        UsuarioProyectoRol.objects.create(usuario=cls.sup, obra=cls.obra, rol=Rol.objects.get(nombre="Supervisor"))

    def datos(self, filas):
        d = {"form-TOTAL_FORMS": str(len(filas)), "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "0",
             "form-MAX_NUM_FORMS": "1000"}
        for i, f in enumerate(filas):
            for k, v in f.items():
                d[f"form-{i}-{k}"] = v
        return d

    def fila(self, clave="A-1", unidad="m3", **extra):
        return {"clave": clave, "descripcion": "Concreto", "unidad_medida": unidad,
                "volumen_total_contratado": "10", "precio_unitario": "100", **extra}

    def test_unidades_vienen_del_catalogo(self):
        self.assertTrue(UnidadMedida.objects.filter(clave="m3").exists())
        self.client.force_login(self.sint)
        r = self.client.get(reverse("catalogo_editar", args=[self.obra.pk]))
        self.assertContains(r, '<select name="form-0-unidad_medida"')
        self.assertContains(r, "m3 · Metro cúbico")

    def test_guarda_con_unidad_de_la_lista(self):
        self.client.force_login(self.sint)
        self.client.post(reverse("catalogo_editar", args=[self.obra.pk]), self.datos([self.fila()]))
        self.assertEqual(CatalogoConcepto.objects.get(obra=self.obra).unidad_medida, "m3")

    def test_rechaza_unidad_fuera_de_la_lista(self):
        self.client.force_login(self.sint)
        r = self.client.post(reverse("catalogo_editar", args=[self.obra.pk]), self.datos([self.fila(unidad="pulgadas")]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(CatalogoConcepto.objects.exists())

    def test_boton_borrar_en_la_vista(self):
        c = CatalogoConcepto.objects.create(obra=self.obra, clave="A-1", descripcion="x", unidad_medida="m3",
                                            volumen_total_contratado=1, precio_unitario=1)
        self.client.force_login(self.sint)
        self.assertContains(self.client.get(reverse("catalogo_ver", args=[self.obra.pk])), "Borrar")
        self.client.post(reverse("catalogo_borrar", args=[self.obra.pk, c.pk]))
        self.assertFalse(CatalogoConcepto.objects.filter(pk=c.pk).exists())

    def test_solo_quien_gestiona_el_catalogo_borra(self):
        c = CatalogoConcepto.objects.create(obra=self.obra, clave="A-1", descripcion="x", unidad_medida="m3",
                                            volumen_total_contratado=1, precio_unitario=1)
        self.client.force_login(self.sup)
        self.assertNotContains(self.client.get(reverse("catalogo_ver", args=[self.obra.pk])), "Borrar concepto")
        self.assertEqual(self.client.post(reverse("catalogo_borrar", args=[self.obra.pk, c.pk])).status_code, 403)
        self.assertTrue(CatalogoConcepto.objects.filter(pk=c.pk).exists())

    def test_borrar_desde_la_edicion(self):
        c = CatalogoConcepto.objects.create(obra=self.obra, clave="A-1", descripcion="x", unidad_medida="m3",
                                            volumen_total_contratado=1, precio_unitario=1)
        self.client.force_login(self.sint)
        d = self.datos([self.fila(id=str(c.pk), DELETE="on"), self.fila(clave="B-1")])
        d["form-INITIAL_FORMS"] = "1"
        self.client.post(reverse("catalogo_editar", args=[self.obra.pk]), d)
        self.assertEqual(list(CatalogoConcepto.objects.values_list("clave", flat=True)), ["B-1"])
