"""Pruebas de estimaciones (CU-02, CU-05, CU-08, CU-16, CU-17). Corren contra PostgreSQL (ver obras/pruebas_bd.py)."""
from datetime import date
from decimal import Decimal

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from obras import bitacora, estimaciones, reportes
from obras.documentos import validar
from obras.models import (
    CatalogoConcepto, CatalogoTipoNota, Contrato, Estimacion, NotaBitacora, Obra, ProgramaObra, ReporteDiario, Rol,
    Usuario, UsuarioProyectoRol,
)

MEMORIA = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
PDF = b"%PDF-1.7\n" + b"x" * 64


@override_settings(ADMIN_EMAILS=[], SITE_URL="", STORAGES=MEMORIA)
class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Programa lineal: 1 de enero (0 %) al 31 de enero (30 %), 1 % por día.
        cls.obra = Obra.objects.create(nombre="Puente Norte", fecha_inicio_oficial=date(2026, 1, 1))
        ProgramaObra.objects.create(obra=cls.obra, fecha=date(2026, 1, 31), avance_programado_porcentaje=30)
        cls.contrato = Contrato.objects.create(
            obra=cls.obra, numero_contrato="LP-2026-001", contratista="Constructora", monto_total_contratado=Decimal("1000000"),
            porcentaje_anticipo=30, porcentaje_retencion=Decimal("0.5"), porcentaje_iva=16, fecha_inicio=date(2026, 1, 1))
        cls.a = CatalogoConcepto.objects.create(obra=cls.obra, clave="A1", descripcion="Concreto", unidad_medida="m3",
                                                volumen_total_contratado=100, precio_unitario=1000)
        cls.b = CatalogoConcepto.objects.create(obra=cls.obra, clave="B1", descripcion="Acero", unidad_medida="ton",
                                                volumen_total_contratado=50, precio_unitario=18000)
        cls.superint = cls._usuario("superint", reportes.ROL_SUPERINTENDENTE)
        cls.supervisor = cls._usuario("supervisor", "Supervisor")
        cls.residente = cls._usuario("residente", reportes.ROL_RESIDENTE)
        cls.admin = cls._usuario("admin", "Administrador")

    @classmethod
    def _usuario(cls, nombre, rol):
        u = Usuario.objects.create_user(f"{nombre}@obras.test", "Clave-segura-123", nombre=nombre.title(), apellidos="Prueba")
        UsuarioProyectoRol.objects.create(usuario=u, obra=cls.obra, rol=Rol.objects.get(nombre=rol))
        return u

    def crear(self, cantidades=None, inicio="2026-01-01", corte="2026-01-31", esperar_ok=True, **extra):
        self.client.force_login(self.superint)
        datos = {"periodo_inicio": inicio, "fecha_corte": corte, "observaciones": "", **extra}
        for concepto, cantidad in (cantidades or {self.a: "10"}).items():
            datos[f"cant_{concepto.pk}"] = cantidad
        r = self.client.post(reverse("estimacion_nueva", args=[self.obra.pk]), datos)
        if esperar_ok:
            self.assertEqual(r.status_code, 302, r.context and "form" in r.context and r.context["form"].errors)
            return Estimacion.objects.order_by("-numero_estimacion").first()
        return r

    def generadores(self, est):
        self.client.force_login(self.superint)
        archivo = SimpleUploadedFile("generadores.pdf", PDF, content_type="application/pdf")
        self.client.post(reverse("estimacion_documento_subir", args=[self.obra.pk, est.pk]),
                         {"archivo": archivo, "categoria": "Números generadores"})

    def enviar(self, est):
        self.client.force_login(self.superint)
        self.client.post(reverse("estimacion_enviar", args=[self.obra.pk, est.pk]))
        est.refresh_from_db()
        return est

    def decidir(self, usuario, est, nombre, rol, comentarios=""):
        self.client.force_login(usuario)
        self.client.post(reverse("estimacion_validar", args=[self.obra.pk, est.pk]),
                         {"rol": rol, "estatus": reportes.estatus(nombre).pk, "comentarios": comentarios})
        est.refresh_from_db()
        return est

    def destinatarios(self):
        return sorted({d for m in mail.outbox for d in m.to})


class CapturaTests(Base):
    def test_calculos_al_guardar(self):
        est = self.crear({self.a: "10", self.b: "0.5"})
        self.assertEqual((est.folio, est.estatus.nombre), ("EST-001", estimaciones.BORRADOR))
        self.assertEqual(est.subtotal, Decimal("19000.00"))               # 10 × 1,000 + 0.5 × 18,000
        self.assertEqual(est.amortizacion_anticipo, Decimal("5700.00"))   # 30 %
        self.assertEqual(est.iva, Decimal("2128.00"))                     # 16 % de (19,000 − 5,700)
        self.assertEqual(est.retencion, Decimal("95.00"))                 # 0.5 %
        self.assertEqual(est.monto_neto, Decimal("15333.00"))
        self.assertEqual(est.retencion_atraso, 0)  # sin reportes aprobados no hay avance real con qué comparar

    def test_periodo_mayor_a_un_mes_y_cantidad_invalida(self):
        r = self.crear(corte="2026-02-05", esperar_ok=False)
        self.assertIn("fecha_corte", r.context["form"].errors)
        r = self.crear({self.a: "-3"}, esperar_ok=False)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Estimacion.objects.exists())

    def test_excedente_requiere_nota_firmada(self):
        r = self.crear({self.a: "120"}, esperar_ok=False)
        self.assertContains(r, "Rebasa el volumen contratado")
        tipo = CatalogoTipoNota.objects.get(nombre="Instrucción")
        nota = bitacora.crear_nota(NotaBitacora(obra=self.obra, tipo=tipo, descripcion="Se autoriza excedente"), self.superint)
        r = self.crear({self.a: "120"}, esperar_ok=False, **{f"nota_{self.a.pk}": str(nota.pk)})
        self.assertContains(r, "Rebasa el volumen contratado")  # todavía no la firman todos
        NotaBitacora.objects.filter(pk=nota.pk).update(estado_firma=bitacora.FIRMADA)
        est = self.crear({self.a: "120"}, **{f"nota_{self.a.pk}": str(nota.pk)})
        renglon = est.estimaciondetalle_set.get()
        self.assertTrue(renglon.es_excedente)
        self.assertEqual(renglon.nota_bitacora, NotaBitacora.objects.get(pk=nota.pk))

    def test_solo_una_estimacion_abierta(self):
        est = self.crear()
        r = self.client.get(reverse("estimacion_nueva", args=[self.obra.pk]))
        self.assertRedirects(r, reverse("estimacion_detalle", args=[self.obra.pk, est.pk]))

    def test_borrador_privado(self):
        est = self.crear()
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(reverse("estimacion_detalle", args=[self.obra.pk, est.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("estimaciones_lista", args=[self.obra.pk])).context["filas"], [])

    def test_documentos_por_contenido(self):
        self.assertEqual(validar(SimpleUploadedFile("hoja.xlsx", b"PK\x03\x04" + b"0" * 40)), "xlsx")
        self.assertEqual(validar(SimpleUploadedFile("plano.dwg", b"AC1032" + b"0" * 40)), "dwg")
        with self.assertRaises(Exception):
            validar(SimpleUploadedFile("virus.pdf", b"MZ\x90\x00" + b"0" * 40))


class FlujoTests(Base):
    def test_flujo_completo_con_observaciones(self):
        est = self.crear()
        est = self.enviar(est)
        self.assertEqual(est.estatus.nombre, estimaciones.BORRADOR)  # faltan los números generadores
        self.generadores(est)
        mail.outbox.clear()
        est = self.enviar(est)
        self.assertEqual(est.estatus.nombre, estimaciones.PENDIENTE)
        self.assertEqual(self.destinatarios(), ["supervisor@obras.test"])
        # bloqueada mientras está en validación
        self.assertEqual(self.client.get(reverse("estimacion_editar", args=[self.obra.pk, est.pk])).status_code, 403)
        # el Residente todavía no decide; el Supervisor la regresa con observaciones (exige comentario)
        est = self.decidir(self.residente, est, reportes.APROBADO, estimaciones.ROL_RESIDENTE)
        self.assertEqual(est.estatus.nombre, estimaciones.PENDIENTE)
        est = self.decidir(self.supervisor, est, estimaciones.OBSERVACIONES, "Supervisor")
        self.assertEqual(est.estatus.nombre, estimaciones.PENDIENTE)
        mail.outbox.clear()
        est = self.decidir(self.supervisor, est, estimaciones.OBSERVACIONES, "Supervisor", "Falta el croquis.")
        self.assertEqual(est.estatus.nombre, estimaciones.OBSERVACIONES)
        self.assertEqual(self.destinatarios(), ["superint@obras.test"])
        # corrige y reenvía: las decisiones se reinician
        est = self.enviar(est)
        self.assertEqual(est.estatus.nombre, estimaciones.PENDIENTE)
        self.assertEqual(estimaciones.decisiones(est), {"Supervisor": None, "Residente": None})
        mail.outbox.clear()
        est = self.decidir(self.supervisor, est, reportes.APROBADO, "Supervisor")
        self.assertEqual(est.estatus.nombre, estimaciones.EN_REVISION)
        self.assertEqual(self.destinatarios(), ["residente@obras.test", "superint@obras.test"])
        self.client.force_login(self.residente)
        self.assertEqual(len(self.client.get(reverse("pendientes")).context["est_revisar"]), 1)
        mail.outbox.clear()
        est = self.decidir(self.residente, est, reportes.APROBADO, estimaciones.ROL_RESIDENTE)
        self.assertEqual(est.estatus.nombre, estimaciones.APROBADO)
        self.assertEqual(self.destinatarios(), ["superint@obras.test", "supervisor@obras.test"])
        # ya aprobada, se puede crear la siguiente
        self.client.force_login(self.superint)
        self.assertEqual(self.client.get(reverse("estimacion_nueva", args=[self.obra.pk])).status_code, 200)

    def test_rechazo_del_residente(self):
        est = self.crear()
        self.generadores(est)
        est = self.enviar(est)
        est = self.decidir(self.supervisor, est, reportes.APROBADO, "Supervisor")
        est = self.decidir(self.residente, est, reportes.RECHAZADO, estimaciones.ROL_RESIDENTE, "Volúmenes mal medidos.")
        self.assertEqual(est.estatus.nombre, estimaciones.RECHAZADO)
        self.assertIn("Residente", estimaciones.estado_legible(est))
        self.client.force_login(self.superint)
        self.assertEqual(self.client.get(reverse("estimacion_editar", args=[self.obra.pk, est.pk])).status_code, 200)

    def test_admin_cambia_desde_validaciones_con_motivo(self):
        est = self.crear()
        self.generadores(est)
        est = self.enviar(est)
        est = self.decidir(self.supervisor, est, reportes.APROBADO, "Supervisor")
        self.client.force_login(self.admin)
        lista = self.client.get(reverse("validaciones"), {"tipo": "estimaciones"})
        url = reverse("validacion_cambiar", args=["estimaciones", est.pk])
        self.assertContains(lista, url)
        datos = {"rol": "Supervisor", "estatus": reportes.estatus(reportes.PENDIENTE).pk}
        self.assertEqual(self.client.post(url, datos).status_code, 200)  # sin motivo
        mail.outbox.clear()
        self.client.post(url, {**datos, "comentarios": "Se aprobó sin revisar el croquis."})
        est.refresh_from_db()
        self.assertEqual(est.estatus.nombre, estimaciones.PENDIENTE)
        self.assertEqual(self.destinatarios(), ["residente@obras.test", "superint@obras.test", "supervisor@obras.test"])


class RetencionAtrasoTests(Base):
    def diario(self, fecha, avance):
        ReporteDiario.objects.create(obra=self.obra, supervisor=self.supervisor, fecha_reporte=fecha,
                                     actividades_realizadas="x", avance_fisico_real_porcentaje=Decimal(avance),
                                     estatus=reportes.estatus(reportes.APROBADO))

    def aprobar(self, est):
        self.generadores(est)
        est = self.enviar(est)
        est = self.decidir(self.supervisor, est, reportes.APROBADO, "Supervisor")
        return self.decidir(self.residente, est, reportes.APROBADO, estimaciones.ROL_RESIDENTE)

    def test_retiene_y_devuelve(self):
        self.diario(date(2026, 1, 31), 20)  # programado 30 %, real 20 % → 10 puntos de atraso
        est = self.crear()
        self.assertEqual(est.retencion_atraso, Decimal("5000.00"))  # 5 % de 1,000,000 × 10 %
        self.assertEqual(est.dias_atraso, 10)  # el 20 % se programó para el 21 de enero
        self.assertEqual(est.monto_neto, Decimal("8120.00") - Decimal("50.00") - Decimal("5000.00"))
        self.aprobar(est)
        # en febrero el programa ya terminó (30 %) y la obra se regulariza: se devuelve lo retenido
        self.diario(date(2026, 2, 28), 30)
        siguiente = self.crear(inicio="2026-02-01", corte="2026-02-28")
        self.assertEqual((siguiente.retencion_atraso, siguiente.devolucion_retencion), (0, Decimal("5000.00")))

    def test_atraso_no_imputable_no_retiene(self):
        self.diario(date(2026, 1, 31), 20)
        r = self.crear(esperar_ok=False, atraso_no_imputable="on")
        self.assertIn("justificacion_atraso", r.context["form"].errors)
        est = self.crear(atraso_no_imputable="on", justificacion_atraso="Lluvias extraordinarias documentadas.")
        self.assertEqual(est.retencion_atraso, 0)
