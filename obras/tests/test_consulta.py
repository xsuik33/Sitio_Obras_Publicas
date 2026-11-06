"""Pruebas de la consulta del expediente (CU-12). Corren contra PostgreSQL (ver obras/pruebas_bd.py)."""
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from obras import consulta, reportes
from obras.models import (
    CatalogoConcepto, Contrato, Estimacion, Obra, ProgramaObra, ReporteDiario, Rol, Usuario, UsuarioProyectoRol,
)

HOY = date(2026, 6, 15)


@override_settings(ADMIN_EMAILS=[], SITE_URL="")
class BaseConsulta(TestCase):
    def setUp(self):
        parche = mock.patch("obras.consulta.hoy", lambda: HOY)
        parche.start()
        self.addCleanup(parche.stop)

    @classmethod
    def setUpTestData(cls):
        cls.puente = Obra.objects.create(nombre="Puente Norte", ubicacion="Monterrey",
                                         fecha_inicio_oficial=date(2026, 1, 1), fecha_fin_oficial=date(2026, 12, 31))
        cls.escuela = Obra.objects.create(nombre="Escuela Sur", fecha_inicio_oficial=date(2026, 9, 1))
        cls.clinica = Obra.objects.create(nombre="Clínica Centro", fecha_inicio_oficial=date(2025, 1, 1),
                                          fecha_fin_oficial=date(2025, 12, 31))
        cls.ajena = Obra.objects.create(nombre="Puente Ajeno")
        cls.directivo = cls._usuario("directivo", "Directivo", cls.puente, cls.escuela, cls.clinica)
        cls.supervisor = cls._usuario("supervisor", "Supervisor", cls.puente)
        cls.otro_sup = cls._usuario("otro", "Supervisor", cls.puente)
        cls.superint = cls._usuario("superint", reportes.ROL_SUPERINTENDENTE, cls.puente)
        cls.contrato = Contrato.objects.create(obra=cls.puente, numero_contrato="LP-2026-014", contratista="Constructora Alfa",
                                               monto_total_contratado=Decimal("1000000"))

    @classmethod
    def _usuario(cls, nombre, rol, *obras):
        u = Usuario.objects.create_user(f"{nombre}@obras.test", "Clave-segura-123", nombre=nombre.title(), apellidos="Prueba")
        for obra in obras:
            UsuarioProyectoRol.objects.create(usuario=u, obra=obra, rol=Rol.objects.get(nombre=rol))
        return u

    def diario(self, fecha, avance, estatus=reportes.APROBADO, supervisor=None):
        return ReporteDiario.objects.create(
            obra=self.puente, supervisor=supervisor or self.supervisor, fecha_reporte=fecha, actividades_realizadas="x",
            avance_fisico_real_porcentaje=Decimal(avance), estatus=reportes.estatus(estatus),
        )

    def buscar(self, usuario, **params):
        self.client.force_login(usuario)
        return self.client.get(reverse("mis_obras"), params)


class BusquedaTests(BaseConsulta):
    def nombres(self, r):
        return [f["obra"].nombre for f in r.context["filas"]]

    def test_solo_sus_obras_por_nombre_contrato_e_id(self):
        self.assertEqual(self.nombres(self.buscar(self.directivo)), ["Clínica Centro", "Escuela Sur", "Puente Norte"])
        self.assertEqual(self.nombres(self.buscar(self.directivo, q="puente")), ["Puente Norte"])  # no la ajena
        self.assertEqual(self.nombres(self.buscar(self.directivo, q="LP-2026")), ["Puente Norte"])
        self.assertEqual(self.nombres(self.buscar(self.directivo, q="alfa")), ["Puente Norte"])
        self.assertEqual(self.nombres(self.buscar(self.directivo, q=str(self.escuela.pk)[:8].upper())), ["Escuela Sur"])

    def test_por_estado(self):
        self.assertEqual(self.nombres(self.buscar(self.directivo, estado=consulta.EN_EJECUCION)), ["Puente Norte"])
        self.assertEqual(self.nombres(self.buscar(self.directivo, estado=consulta.POR_INICIAR)), ["Escuela Sur"])
        self.assertEqual(self.nombres(self.buscar(self.directivo, estado=consulta.CONCLUIDA)), ["Clínica Centro"])
        self.assertEqual(len(self.buscar(self.directivo, estado="inventado").context["filas"]), 3)  # se ignora

    def test_sin_resultados_avisa(self):
        r = self.buscar(self.directivo, q="no existe")
        self.assertEqual(r.context["filas"], [])
        self.assertContains(r, "No hay obras que coincidan")

    def test_tarjeta_muestra_avance(self):
        ProgramaObra.objects.create(obra=self.puente, fecha=date(2026, 12, 31), avance_programado_porcentaje=100)
        self.diario(date(2026, 6, 1), 30)
        fila = next(f for f in self.buscar(self.directivo).context["filas"] if f["obra"] == self.puente)
        self.assertEqual(fila["estado"], consulta.EN_EJECUCION)
        self.assertEqual(fila["real"], Decimal("30.00"))
        self.assertEqual(fila["programado"], Decimal("45.33"))  # 165 de 364 días desde el inicio oficial
        self.assertEqual(fila["clase"], "atraso")


class ExpedienteTests(BaseConsulta):
    def expediente(self, usuario, obra=None):
        self.client.force_login(usuario)
        return self.client.get(reverse("obra_detalle", args=[(obra or self.puente).pk]))

    def test_avance_fisico_y_financiero(self):
        ProgramaObra.objects.create(obra=self.puente, fecha=date(2026, 6, 15), avance_programado_porcentaje=50)
        self.diario(date(2026, 6, 10), 40)
        self.diario(date(2026, 6, 14), 60, reportes.PENDIENTE)  # no aprobado: no cuenta
        concepto = CatalogoConcepto.objects.create(obra=self.puente, clave="A1", descripcion="Concreto", unidad_medida="m3",
                                                   volumen_total_contratado=100, precio_unitario=1000, capitulo="Estructura")
        est = Estimacion.objects.create(contrato=self.contrato, superintendente=self.superint, numero_estimacion=1,
                                        folio="E-1", periodo_inicio=date(2026, 5, 1), fecha_corte=date(2026, 5, 31),
                                        estatus=reportes.estatus(reportes.APROBADO))
        est.estimaciondetalle_set.create(concepto=concepto, cantidad_ejecutada=Decimal("25"))
        r = self.expediente(self.directivo)
        av = r.context["x"]["avance"]["datos"]
        self.assertEqual((av["programado"], av["real"], av["desviacion"]), (Decimal("50.00"), Decimal("40.00"), Decimal("-10.00")))
        self.assertEqual(av["financiero"]["programado"], Decimal("500000.00"))
        self.assertEqual(av["financiero"]["fisico"], Decimal("400000.00"))
        self.assertEqual(av["financiero"]["estimado"], Decimal("25000.00"))
        self.assertEqual(av["financiero"]["pct_estimado"], Decimal("2.50"))
        self.assertEqual(r.context["grafica"]["real"], [{"f": "2026-06-10", "v": 40.0}])
        self.assertContains(r, "LP-2026-014")
        self.assertContains(r, "Estructura")
        self.assertContains(r, 'id="datos-avance"')

    def test_borradores_ajenos_ocultos(self):
        self.diario(date(2026, 6, 1), 10, reportes.BORRADOR, supervisor=self.otro_sup)
        self.diario(date(2026, 6, 2), 12, reportes.BORRADOR)
        self.diario(date(2026, 6, 3), 14, reportes.PENDIENTE, supervisor=self.otro_sup)
        self.assertEqual(self.expediente(self.directivo).context["x"]["diarios"]["datos"]["n"], 1)
        self.assertEqual(self.expediente(self.supervisor).context["x"]["diarios"]["datos"]["n"], 2)

    def test_seccion_que_falla_no_tumba_el_expediente(self):
        with mock.patch.dict(consulta.SECCIONES, {"estimaciones": mock.Mock(side_effect=RuntimeError("caída"))}):
            r = self.expediente(self.directivo)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "No se pudieron cargar las estimaciones")
        self.assertContains(r, "Reintentar")

    def test_sin_datos_y_sin_acceso(self):
        r = self.expediente(self.directivo, self.escuela)
        self.assertContains(r, "Sin contrato registrado")
        self.assertContains(r, "Para ver la gráfica")
        self.assertEqual(self.expediente(self.directivo, self.ajena).status_code, 403)

    def test_solo_consulta_no_modifica(self):
        r = self.expediente(self.directivo)
        self.assertNotContains(r, "Gestionar equipo")  # el Directivo no ve acciones de gestión
