"""Pruebas de los ajustes al documento de especificación: atraso crítico en días y notas vinculadas (CU-03)
y módulo de validaciones del administrador con motivo obligatorio (CU-14, CU-15)."""
from datetime import date
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from obras import bitacora, reportes
from obras.models import (
    CatalogoTipoNota, NotaBitacora, Obra, ProgramaObra, ReporteDiario, Rol, Usuario, UsuarioProyectoRol,
)

RES, SINT = reportes.ROL_RESIDENTE, reportes.ROL_SUPERINTENDENTE


@override_settings(ADMIN_EMAILS=[], SITE_URL="")
class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Programa lineal: del 1 al 31 de enero va de 0 % a 30 % (1 % por día).
        cls.obra = Obra.objects.create(nombre="Puente Norte", fecha_inicio_oficial=date(2026, 1, 1))
        ProgramaObra.objects.create(obra=cls.obra, fecha=date(2026, 1, 31), avance_programado_porcentaje=30)
        cls.supervisor = cls._usuario("supervisor", "Supervisor")
        cls.residente = cls._usuario("residente", RES)
        cls.superint = cls._usuario("superint", SINT)
        cls.admin = cls._usuario("admin", "Administrador")

    @classmethod
    def _usuario(cls, nombre, rol):
        u = Usuario.objects.create_user(f"{nombre}@obras.test", "Clave-segura-123", nombre=nombre.title(), apellidos="Prueba")
        UsuarioProyectoRol.objects.create(usuario=u, obra=cls.obra, rol=Rol.objects.get(nombre=rol))
        return u

    def nuevo(self, avance, fecha="2026-01-21", **extra):
        self.client.force_login(self.supervisor)
        datos = {"fecha_reporte": fecha, "actividades_realizadas": "Colado", "avance_fisico_real_porcentaje": avance,
                 "accion": "enviar", **extra}
        return self.client.post(reverse("reporte_nuevo", args=[self.obra.pk]), datos)


class AtrasoTests(Base):
    def test_dias_de_atraso(self):
        self.assertEqual(reportes.dias_atraso(self.obra, date(2026, 1, 21), Decimal("15")), 5)
        self.assertEqual(reportes.dias_atraso(self.obra, date(2026, 1, 21), Decimal("20")), 0)
        self.assertEqual(reportes.dias_atraso(self.obra, date(2026, 1, 21), Decimal("25")), 0)  # adelantado
        self.assertEqual(reportes.dias_atraso(self.obra, date(2026, 3, 1), Decimal("30")), 0)  # programa terminado
        sin_programa = Obra.objects.create(nombre="Sin programa")
        self.assertIsNone(reportes.dias_atraso(sin_programa, date(2026, 1, 21), Decimal("15")))

    def test_atraso_critico_exige_causa_y_solicitud(self):
        r = self.nuevo("15")
        self.assertEqual(r.status_code, 200)
        self.assertIn("atraso_causa", r.context["form"].errors)
        self.assertIn("atraso_solicitud", r.context["form"].errors)
        self.assertIn("5 días detrás del programa", r.context["form"].errors["atraso_causa"][0])
        self.assertFalse(ReporteDiario.objects.exists())
        r = self.nuevo("15", atraso_causa="Lluvias y falta de cemento.", atraso_solicitud="Reprogramación")
        self.assertEqual(r.status_code, 302)
        reporte = ReporteDiario.objects.get()
        self.assertEqual((reporte.atraso_causa, reporte.atraso_solicitud), ("Lluvias y falta de cemento.", "Reprogramación"))
        detalle = self.client.get(reverse("reporte_detalle", args=[self.obra.pk, reporte.pk]))
        self.assertContains(detalle, "Atraso crítico · 5 días detrás del programa")
        self.assertContains(detalle, "Solicita: Reprogramación")
        pdf = self.client.get(reverse("reporte_pdf", args=[self.obra.pk, reporte.pk]))
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_sin_atraso_no_se_guardan_datos_de_atraso(self):
        self.nuevo("20", atraso_causa="No aplica", atraso_solicitud="Retención")
        reporte = ReporteDiario.objects.get()
        self.assertIsNone(reporte.atraso_causa)
        self.assertIsNone(reporte.atraso_solicitud)


class NotasVinculadasTests(Base):
    def test_vincular_notas_de_la_obra(self):
        self.client.force_login(self.supervisor)
        tipo = CatalogoTipoNota.objects.get(nombre="Instrucción")
        nota = bitacora.crear_nota(NotaBitacora(obra=self.obra, tipo=tipo, descripcion="Colar losa"), self.supervisor)
        otra_obra = Obra.objects.create(nombre="Otra")
        ajena = NotaBitacora.objects.create(obra=otra_obra, tipo=tipo, descripcion="Ajena", usuario_creador=self.supervisor)
        r = self.nuevo("20", notas=[str(ajena.pk)])
        self.assertIn("notas", r.context["form"].errors)  # no se puede vincular una nota de otra obra
        r = self.nuevo("20", notas=[str(nota.pk)])
        self.assertEqual(r.status_code, 302)
        reporte = ReporteDiario.objects.get()
        self.assertEqual(list(reporte.notas.all()), [nota])
        detalle = self.client.get(reverse("reporte_detalle", args=[self.obra.pk, reporte.pk]))
        self.assertContains(detalle, reverse("nota_detalle", args=[self.obra.pk, nota.pk]))


class ValidacionesTests(Base):
    def setUp(self):
        self.nuevo("20")
        self.reporte = ReporteDiario.objects.get()
        self.client.force_login(self.residente)
        self.client.post(reverse("reporte_validar", args=[self.obra.pk, self.reporte.pk]),
                         {"rol": RES, "estatus": reportes.estatus(reportes.APROBADO).pk})
        self.url = reverse("validacion_cambiar", args=["diarios", self.reporte.pk])

    def test_solo_administradores(self):
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(reverse("validaciones")).status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("validaciones")).status_code, 302)

    def test_lista_y_cambio_con_motivo(self):
        self.client.force_login(self.admin)
        lista = self.client.get(reverse("validaciones"), {"estatus": reportes.EN_REVISION})
        self.assertContains(lista, self.url)
        self.assertEqual(len(self.client.get(reverse("validaciones"), {"estatus": reportes.APROBADO}).context["filas"]), 0)
        datos = {"rol": RES, "estatus": reportes.estatus(reportes.PENDIENTE).pk}
        r = self.client.post(self.url, datos)  # sin motivo
        self.assertEqual(r.status_code, 200)
        self.assertIn("comentarios", r.context["form"].errors)
        mail.outbox.clear()
        r = self.client.post(self.url, {**datos, "comentarios": "El residente aprobó por error."})
        self.assertRedirects(r, reverse("validaciones") + "?tipo=diarios")
        self.reporte.refresh_from_db()
        self.assertEqual(self.reporte.estatus.nombre, reportes.PENDIENTE)
        ultima = self.reporte.validacionreportediario_set.order_by("-fecha_validacion").first()
        self.assertEqual((ultima.usuario_validador, ultima.comentarios), (self.admin, "El residente aprobó por error."))
        self.assertEqual(sorted(d for m in mail.outbox for d in m.to),
                         ["residente@obras.test", "superint@obras.test", "supervisor@obras.test"])
        self.assertIn("Cambio de estatus en reporte diario", mail.outbox[0].subject)

    def test_desde_el_reporte_tambien_exige_motivo(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("reporte_validar", args=[self.obra.pk, self.reporte.pk]),
                         {"rol": RES, "estatus": reportes.estatus(reportes.PENDIENTE).pk})
        self.reporte.refresh_from_db()
        self.assertEqual(self.reporte.estatus.nombre, reportes.EN_REVISION)
