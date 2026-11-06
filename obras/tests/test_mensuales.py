"""Pruebas del reporte mensual (CU-04). Corren contra PostgreSQL (ver obras/pruebas_bd.py)."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from obras import mensuales, reportes
from obras.models import (
    CatalogoConcepto, Contrato, Estimacion, EstimacionDetalle, NotaBitacora, Obra, ProgramaObra, ReporteDiario,
    ReporteMensual, Rol, Usuario, UsuarioProyectoRol,
)

RES, SINT = reportes.ROL_RESIDENTE, reportes.ROL_SUPERINTENDENTE
MX = ZoneInfo("America/Mexico_City")


@override_settings(ADMIN_EMAILS=[], SITE_URL="")
class BaseMensual(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.obra = Obra.objects.create(nombre="Puente Norte")
        cls.supervisor = cls._usuario("supervisor", "Supervisor")
        cls.residente = cls._usuario("residente", RES)
        cls.superint = cls._usuario("superint", SINT)
        cls.admin = cls._usuario("admin", "Administrador")
        cls.directivo = cls._usuario("directivo", "Directivo")

    @classmethod
    def _usuario(cls, nombre, rol):
        u = Usuario.objects.create_user(f"{nombre}@obras.test", "Clave-segura-123", nombre=nombre.title(), apellidos="Prueba")
        UsuarioProyectoRol.objects.create(usuario=u, obra=cls.obra, rol=Rol.objects.get(nombre=rol))
        return u

    def diario(self, fecha, avance, estatus=reportes.APROBADO, **extra):
        return ReporteDiario.objects.create(
            obra=self.obra, supervisor=self.supervisor, fecha_reporte=fecha, actividades_realizadas="Trabajos",
            avance_fisico_real_porcentaje=Decimal(avance), estatus=reportes.estatus(estatus), **extra,
        )

    def crear(self, anio=2026, mes=10, accion="enviar", **extra):
        self.client.force_login(self.supervisor)
        datos = {"anio": anio, "mes": mes, "fecha_corte": "", "resumen": "Se coló la losa.", "accion": accion, **extra}
        r = self.client.post(reverse("mensual_nuevo", args=[self.obra.pk]), datos)
        self.assertEqual(r.status_code, 302, r.context and "form" in r.context and r.context["form"].errors)
        return ReporteMensual.objects.get(obra=self.obra, anio=anio, mes=mes)

    def decidir(self, usuario, reporte, nombre, rol=None, comentarios=""):
        self.client.force_login(usuario)
        rol = rol or next(r for r in reportes.ROLES_VALIDADORES if r in reportes.roles_en(usuario, self.obra))
        self.client.post(
            reverse("mensual_validar", args=[self.obra.pk, reporte.pk]),
            {"rol": rol, "estatus": reportes.estatus(nombre).pk, "comentarios": comentarios},
        )
        reporte.refresh_from_db()
        return reporte

    def destinatarios(self):
        return sorted(d for m in mail.outbox for d in m.to)


class ConsolidadoTests(BaseMensual):
    def setUp(self):
        self.diario(date(2026, 9, 30), 5)
        self.diario(date(2026, 10, 1), 10, clima="Soleado", incidencias="Falta de cemento")
        self.diario(date(2026, 10, 15), 25, clima="Lluvia")
        self.diario(date(2026, 10, 20), 30, reportes.PENDIENTE, incidentes_seguridad="Caída menor")
        self.diario(date(2026, 10, 21), 31, reportes.BORRADOR)
        ProgramaObra.objects.create(obra=self.obra, fecha=date(2026, 10, 1), avance_programado_porcentaje=10)
        ProgramaObra.objects.create(obra=self.obra, fecha=date(2026, 10, 31), avance_programado_porcentaje=40)
        contrato = Contrato.objects.create(obra=self.obra, numero_contrato="C-1", contratista="Constructora",
                                           monto_total_contratado=Decimal("10000"))
        concepto = CatalogoConcepto.objects.create(obra=self.obra, clave="A1", descripcion="Concreto", unidad_medida="m3",
                                                   volumen_total_contratado=100, precio_unitario=100)
        est = Estimacion.objects.create(contrato=contrato, superintendente=self.superint, numero_estimacion=1,
                                        folio="E-1", periodo_inicio=date(2026, 10, 1), fecha_corte=date(2026, 10, 31),
                                        estatus=reportes.estatus(reportes.APROBADO))
        EstimacionDetalle.objects.create(estimacion=est, concepto=concepto, cantidad_ejecutada=10)
        NotaBitacora.objects.create(obra=self.obra, usuario_creador=self.supervisor, descripcion="Inicio de colado",
                                    fecha_nota=datetime(2026, 10, 3, 9, tzinfo=MX))

    def test_cifras_del_mes(self):
        r = ReporteMensual(obra=self.obra, anio=2026, mes=10, fecha_corte=date(2026, 10, 31))
        c = mensuales.consolidar(r)
        self.assertEqual((c["diarios"]["total"], c["diarios"]["aprobados"], c["diarios"]["sin_aprobar"]), (3, 2, 1))
        self.assertEqual((c["periodo"]["dias"], c["diarios"]["dias_sin_reporte"]), (31, 28))
        self.assertEqual((c["avance"]["inicio"], c["avance"]["corte"], c["avance"]["mes"]), ("5.00", "25.00", "20.00"))
        self.assertEqual((c["avance"]["programado"], c["avance"]["diferencia"]), ("40.00", "-15.00"))
        self.assertEqual(c["avance"]["clase"], "atraso")
        self.assertEqual(dict(c["clima"]), {"Soleado": 1, "Lluvia": 1})
        self.assertEqual([i["texto"] for i in c["incidencias"]], ["Falta de cemento"])
        self.assertEqual([i["texto"] for i in c["seguridad"]], ["Caída menor"])
        self.assertEqual(c["estimaciones"]["aprobado_mes"], "$ 1,000.00")
        self.assertEqual(c["estimaciones"]["avance_financiero"], "10.00")
        self.assertEqual(c["bitacora"]["total"], 1)
        self.assertEqual(len(c["advertencias"]), 2)

    def test_corte_a_mitad_de_mes(self):
        r = ReporteMensual(obra=self.obra, anio=2026, mes=10, fecha_corte=date(2026, 10, 10))
        c = mensuales.consolidar(r)
        self.assertEqual((c["diarios"]["total"], c["avance"]["corte"], c["periodo"]["dias"]), (1, "10.00", 10))

    def test_congela_al_aprobar(self):
        reporte = self.crear()
        self.decidir(self.residente, reporte, reportes.APROBADO)
        reporte = self.decidir(self.superint, reporte, reportes.APROBADO)
        self.assertEqual(reporte.estatus.nombre, reportes.APROBADO)
        self.assertEqual(reporte.consolidado["avance"]["corte"], "25.00")
        ReporteDiario.objects.filter(fecha_reporte=date(2026, 10, 15)).update(avance_fisico_real_porcentaje=50)
        self.assertEqual(mensuales.datos(reporte)[0]["avance"]["corte"], "25.00")
        self.assertEqual(mensuales.consolidar(reporte)["avance"]["corte"], "50.00")
        # el administrador lo regresa a Pendiente: sin motivo no procede (CU-15)
        reporte = self.decidir(self.admin, reporte, reportes.PENDIENTE, rol=RES)
        self.assertEqual(reporte.estatus.nombre, reportes.APROBADO)
        # con motivo sí: se libera el congelado y se avisa a los involucrados
        mail.outbox.clear()
        reporte = self.decidir(self.admin, reporte, reportes.PENDIENTE, rol=RES, comentarios="Se corrigió un diario.")
        self.assertEqual(reporte.estatus.nombre, reportes.EN_REVISION)
        self.assertIsNone(reporte.consolidado)
        self.assertEqual(self.destinatarios(), ["residente@obras.test", "superint@obras.test", "supervisor@obras.test"])
        self.assertIn("Se corrigió un diario.", mail.outbox[0].body)


class FlujoTests(BaseMensual):
    def test_envio_aprobacion_y_correos(self):
        reporte = self.crear()
        self.assertEqual(reporte.estatus.nombre, reportes.PENDIENTE)
        self.assertEqual(self.destinatarios(), ["residente@obras.test", "superint@obras.test"])
        self.assertIn("Advertencias", mail.outbox[0].body)  # el mes no tiene diarios
        mail.outbox.clear()
        reporte = self.decidir(self.residente, reporte, reportes.APROBADO)
        self.assertEqual(reporte.estatus.nombre, reportes.EN_REVISION)
        self.assertEqual(mail.outbox, [])
        reporte = self.decidir(self.superint, reporte, reportes.APROBADO)
        self.assertEqual(reporte.estatus.nombre, reportes.APROBADO)
        self.assertEqual(self.destinatarios(), ["directivo@obras.test", "supervisor@obras.test"])

    def test_cada_rol_decide_una_vez(self):
        reporte = self.crear()
        self.decidir(self.residente, reporte, reportes.APROBADO)
        reporte = self.decidir(self.residente, reporte, reportes.RECHAZADO, comentarios="otra vez")
        self.assertEqual(reporte.estatus.nombre, reportes.EN_REVISION)

    def test_rechazo_correccion_y_reenvio(self):
        reporte = self.crear()
        reporte = self.decidir(self.residente, reporte, reportes.RECHAZADO)
        self.assertEqual(reporte.estatus.nombre, reportes.PENDIENTE)  # sin motivo no se registra
        mail.outbox.clear()
        reporte = self.decidir(self.residente, reporte, reportes.RECHAZADO, comentarios="Falta el resumen de seguridad")
        self.assertEqual(reporte.estatus.nombre, reportes.RECHAZADO)
        self.assertEqual(self.destinatarios(), ["supervisor@obras.test"])
        self.client.force_login(self.supervisor)
        r = self.client.post(reverse("mensual_editar", args=[self.obra.pk, reporte.pk]),
                             {"anio": 2026, "mes": 10, "fecha_corte": "2026-10-31", "resumen": "Corregido"})
        self.assertEqual(r.status_code, 302)
        reporte.refresh_from_db()
        self.assertEqual((reporte.estatus.nombre, reporte.resumen), (reportes.PENDIENTE, "Corregido"))
        self.assertEqual(mensuales.decisiones(reporte), {SINT: None, RES: None})

    def test_borrador_privado_y_aprobado_no_editable(self):
        reporte = self.crear(accion="borrador")
        self.assertEqual(reporte.estatus.nombre, reportes.BORRADOR)
        self.assertEqual(mail.outbox, [])
        self.client.force_login(self.directivo)
        self.assertEqual(self.client.get(reverse("mensual_detalle", args=[self.obra.pk, reporte.pk])).status_code, 403)
        self.assertEqual(len(self.client.get(reverse("mensuales_lista", args=[self.obra.pk])).context["filas"]), 0)
        self.client.force_login(self.supervisor)
        self.client.post(reverse("mensual_editar", args=[self.obra.pk, reporte.pk]),
                         {"fecha_corte": "", "resumen": "Listo", "accion": "enviar"})
        reporte.refresh_from_db()
        self.assertEqual(reporte.estatus.nombre, reportes.PENDIENTE)
        self.decidir(self.residente, reporte, reportes.APROBADO)
        self.decidir(self.superint, reporte, reportes.APROBADO)
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(reverse("mensual_editar", args=[self.obra.pk, reporte.pk])).status_code, 403)

    def test_permisos_por_rol(self):
        self.client.force_login(self.directivo)
        self.assertEqual(self.client.get(reverse("mensual_nuevo", args=[self.obra.pk])).status_code, 403)
        reporte = self.crear()
        self.client.force_login(self.directivo)
        self.assertEqual(self.client.get(reverse("mensual_detalle", args=[self.obra.pk, reporte.pk])).status_code, 200)
        r = self.client.post(reverse("mensual_validar", args=[self.obra.pk, reporte.pk]),
                             {"rol": RES, "estatus": reportes.estatus(reportes.APROBADO).pk})
        self.assertEqual(r.status_code, 403)

    def test_pendientes_y_pdf(self):
        reporte = self.crear()
        self.client.force_login(self.residente)
        self.assertEqual(len(self.client.get(reverse("pendientes")).context["mens_revisar"]), 1)
        r = self.client.get(reverse("mensual_pdf", args=[self.obra.pk, reporte.pk]))
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.decidir(self.residente, reporte, reportes.APROBADO)
        self.decidir(self.superint, reporte, reportes.APROBADO)
        self.assertTrue(self.client.get(reverse("mensual_pdf", args=[self.obra.pk, reporte.pk])).content.startswith(b"%PDF"))
        detalle = self.client.get(reverse("obra_detalle", args=[self.obra.pk]))
        self.assertContains(detalle, reverse("mensuales_lista", args=[self.obra.pk]))


class FormularioTests(BaseMensual):
    def post(self, **datos):
        self.client.force_login(self.supervisor)
        base = {"anio": 2026, "mes": 10, "fecha_corte": "", "resumen": "x", "accion": "borrador"}
        return self.client.post(reverse("mensual_nuevo", args=[self.obra.pk]), {**base, **datos})

    def test_corte_por_omision_y_validaciones(self):
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(ReporteMensual.objects.get().fecha_corte, date(2026, 10, 31))
        self.assertIn("mes", self.post().context["form"].errors)  # mes repetido
        self.assertIn("fecha_corte", self.post(mes=9, fecha_corte="2026-10-01").context["form"].errors)
        self.assertIn("mes", self.post(anio=2030, mes=1).context["form"].errors)  # mes futuro
        self.assertIn("resumen", self.post(mes=8, resumen="").context["form"].errors)

    def test_vista_previa_en_formulario(self):
        self.client.force_login(self.supervisor)
        r = self.client.get(reverse("mensual_nuevo", args=[self.obra.pk]))
        self.assertIsNotNone(r.context["previa"])
        self.assertContains(r, "Vista previa del consolidado")


class ScriptIncrementalTests(TestCase):
    def test_cu04_es_idempotente(self):
        sql = (Path(settings.BASE_DIR) / "db" / "cu04_reportes_mensuales.sql").read_text(encoding="utf-8")
        with connection.cursor() as cursor:
            cursor.execute(sql)
            cursor.execute(sql)
            cursor.execute("SELECT to_regclass('validaciones_reportes_mensuales') IS NOT NULL")
            self.assertTrue(cursor.fetchone()[0])
