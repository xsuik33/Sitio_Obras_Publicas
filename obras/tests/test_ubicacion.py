"""Ubicación de la obra en mapa (OpenStreetMap) y módulo de clima guardado para después."""
from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from obras import clima
from obras.models import Obra, Usuario


@override_settings(ADMIN_EMAILS=["admin@obras.test"], SITE_URL="")
class UbicacionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = Usuario.objects.create_user("admin@obras.test", "Clave-segura-123", nombre="Ad", apellidos="Min")

    def guardar(self, **extra):
        self.client.force_login(self.admin)
        datos = {"nombre": "ESCOM", "ubicacion": "Av. Juan de Dios Bátiz, CDMX", "fecha_inicio_oficial": "",
                 "fecha_fin_oficial": "", "latitud": "", "longitud": "", **extra}
        return self.client.post(reverse("obra_nueva"), datos)

    def test_guarda_coordenadas_y_muestra_mapa(self):
        self.guardar(latitud="19.504507", longitud="-99.146911")
        obra = Obra.objects.get(nombre="ESCOM")
        self.assertEqual((obra.latitud, obra.longitud), (Decimal("19.504507"), Decimal("-99.146911")))
        r = self.client.get(reverse("obra_detalle", args=[obra.pk]))
        self.assertContains(r, 'id="mapa-obra"')
        self.assertContains(r, "openstreetmap.org/?mlat=19.504507")

    def test_sin_coordenadas_se_puede_guardar(self):
        self.guardar()
        obra = Obra.objects.get(nombre="ESCOM")
        self.assertIsNone(obra.latitud)
        self.assertContains(self.client.get(reverse("obra_detalle", args=[obra.pk])), "no tiene su punto marcado")

    def test_coordenadas_incompletas_o_invalidas(self):
        r = self.guardar(latitud="19.5")
        self.assertContains(r, "faltan coordenadas")
        r = self.guardar(latitud="95", longitud="10")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Obra.objects.exists())

    def test_el_formulario_trae_el_mapa_editable(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("obra_nueva"))
        self.assertContains(r, "Buscar la ubicación escrita")
        self.assertContains(r, 'name="latitud"')


class ClimaTests(SimpleTestCase):
    def test_codigos(self):
        self.assertEqual(clima.nombre_clima(0), "Soleado")
        self.assertEqual(clima.nombre_clima(63), "Lluvioso")
        self.assertIsNone(clima.nombre_clima(1234))

    def test_sin_coordenadas_o_sin_servicio_devuelve_none(self):
        self.assertIsNone(clima.clima_del_dia(None, None))
        with mock.patch("obras.clima.urlopen", side_effect=OSError("sin red")):
            self.assertIsNone(clima.clima_del_dia(19.5, -99.1))
