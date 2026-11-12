"""Cambios pedidos por el equipo (Doc/latex/Cambios.txt) para el reporte diario:
1. El administrador no elabora reportes: solo el Supervisor.
2. Las notas de bitácora se eligen en un menú desplegable.
3. No se reporta un día futuro ni una fecha que ya tiene reporte (y se avisa al elegirla).
4. Todos los campos de la jornada deben llenarse correctamente."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from obras import reportes
from obras.models import CatalogoTipoNota, NotaBitacora, Obra, ReporteDiario, Rol, Usuario, UsuarioProyectoRol

MX = ZoneInfo("America/Mexico_City")
HOY = timezone.localdate()


@override_settings(ADMIN_EMAILS=["global@obras.test"], SITE_URL="")
class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.obra = Obra.objects.create(nombre="Puente Norte", fecha_inicio_oficial=HOY - timedelta(days=60))
        cls.supervisor = cls._usuario("supervisor", "Supervisor")
        cls.otro_supervisor = cls._usuario("otro", "Supervisor")
        cls.residente = cls._usuario("residente", reportes.ROL_RESIDENTE)
        cls.admin = cls._usuario("admin", "Administrador")
        cls.directivo = cls._usuario("directivo", "Directivo")
        cls.global_admin = Usuario.objects.create_user(
            "global@obras.test", "Clave-segura-123", nombre="Global", apellidos="Prueba"
        )

    @classmethod
    def _usuario(cls, nombre, rol):
        u = Usuario.objects.create_user(f"{nombre}@obras.test", "Clave-segura-123", nombre=nombre.title(), apellidos="Prueba")
        UsuarioProyectoRol.objects.create(usuario=u, obra=cls.obra, rol=Rol.objects.get(nombre=rol))
        return u

    def datos(self, **cambios):
        base = {
            "fecha_reporte": (HOY - timedelta(days=5)).isoformat(), "jornada_inicio": "08:00", "jornada_fin": "17:00",
            "clima": "Soleado", "temperatura_c": "24", "actividades_realizadas": "Colado de losa del eje 3",
            "avance_fisico_real_porcentaje": "20", "accion": "enviar",
        }
        base.update(cambios)
        return {k: v for k, v in base.items() if v is not None}

    def crear(self, usuario=None, **cambios):
        self.client.force_login(usuario or self.supervisor)
        return self.client.post(reverse("reporte_nuevo", args=[self.obra.pk]), self.datos(**cambios))

    def errores(self, respuesta):
        self.assertEqual(respuesta.status_code, 200, "se esperaba el formulario con errores")
        return respuesta.context["form"].errors

    def reporte(self, dias_atras, avance, estatus=reportes.PENDIENTE):
        return ReporteDiario.objects.create(
            obra=self.obra, supervisor=self.supervisor, fecha_reporte=HOY - timedelta(days=dias_atras),
            actividades_realizadas="Trabajos del día", avance_fisico_real_porcentaje=Decimal(avance),
            estatus=reportes.estatus(estatus),
        )


class SoloSupervisorTests(Base):
    """Cambio 1: el administrador (de la obra o del sistema) no hace reportes."""

    def test_supervisor_si_crea(self):
        r = self.crear()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ReporteDiario.objects.get().supervisor, self.supervisor)

    def test_administrador_de_la_obra_no_crea(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("reporte_nuevo", args=[self.obra.pk])).status_code, 403)
        self.assertEqual(self.crear(self.admin).status_code, 403)
        self.assertFalse(ReporteDiario.objects.exists())

    def test_administrador_del_sistema_no_crea(self):
        self.assertEqual(self.crear(self.global_admin).status_code, 403)
        self.assertFalse(ReporteDiario.objects.exists())

    def test_administrador_no_crea_mensuales(self):
        for usuario in (self.admin, self.global_admin):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse("mensual_nuevo", args=[self.obra.pk])).status_code, 403)

    def test_residente_y_directivo_no_crean(self):
        for usuario in (self.residente, self.directivo):
            self.assertEqual(self.crear(usuario).status_code, 403)

    def test_lista_sin_boton_para_el_administrador(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("reportes_lista", args=[self.obra.pk]))
        self.assertFalse(r.context["puede_crear"])
        self.assertNotContains(r, reverse("reporte_nuevo", args=[self.obra.pk]))
        self.client.force_login(self.supervisor)
        self.assertTrue(self.client.get(reverse("reportes_lista", args=[self.obra.pk])).context["puede_crear"])

    def test_administrador_no_edita_el_reporte_del_supervisor(self):
        reporte = self.reporte(5, "20")
        for usuario in (self.admin, self.global_admin):
            self.client.force_login(usuario)
            url = reverse("reporte_editar", args=[self.obra.pk, reporte.pk])
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_otro_supervisor_no_edita_reporte_ajeno(self):
        reporte = self.reporte(5, "20")
        self.client.force_login(self.otro_supervisor)
        r = self.client.get(reverse("reporte_editar", args=[self.obra.pk, reporte.pk]))
        self.assertEqual(r.status_code, 403)

    def test_administrador_sigue_validando_con_motivo(self):
        reporte = self.reporte(5, "20")
        self.client.force_login(self.admin)
        self.client.post(reverse("reporte_validar", args=[self.obra.pk, reporte.pk]), {
            "rol": reportes.ROL_RESIDENTE, "estatus": reportes.estatus(reportes.APROBADO).pk,
            "comentarios": "Revisado en sitio.",
        })
        reporte.refresh_from_db()
        self.assertEqual(reporte.estatus.nombre, reportes.APROBADO)


class NotasDesplegablesTests(Base):
    """Cambio 2: las notas se eligen en un menú desplegable."""

    def setUp(self):
        tipo = CatalogoTipoNota.objects.get(nombre="Avance")
        self.reciente = NotaBitacora.objects.create(obra=self.obra, usuario_creador=self.supervisor, tipo=tipo,
                                                    descripcion="Colado de losa", fecha_nota=timezone.now())
        self.vieja = NotaBitacora.objects.create(obra=self.obra, usuario_creador=self.supervisor, tipo=tipo,
                                                 descripcion="Nota de hace dos meses",
                                                 fecha_nota=datetime.now(MX) - timedelta(days=60))

    def test_menu_desplegable_cerrado_con_las_notas_recientes(self):
        self.client.force_login(self.supervisor)
        r = self.client.get(reverse("reporte_nuevo", args=[self.obra.pk]))
        self.assertContains(r, '<details class="desplegable-notas" id="notas-menu">')
        self.assertContains(r, "Colado de losa")
        self.assertNotContains(r, "Nota de hace dos meses")  # solo las de los últimos 30 días

    def test_se_guardan_las_notas_elegidas(self):
        r = self.crear(notas=[str(self.reciente.pk)])
        self.assertEqual(r.status_code, 302)
        self.assertEqual(list(ReporteDiario.objects.get().notas.all()), [self.reciente])

    def test_no_acepta_una_nota_fuera_de_la_lista(self):
        self.assertIn("notas", self.errores(self.crear(notas=[str(self.vieja.pk)])))

    def test_sin_notas_muestra_enlace_para_registrar(self):
        NotaBitacora.objects.all().delete()
        self.client.force_login(self.supervisor)
        r = self.client.get(reverse("reporte_nuevo", args=[self.obra.pk]))
        self.assertNotContains(r, 'id="notas-menu"')
        self.assertContains(r, reverse("nota_nueva", args=[self.obra.pk]))


class FechaTests(Base):
    """Cambio 3: ni días futuros ni fechas repetidas, con aviso al elegir la fecha."""

    def test_no_acepta_manana(self):
        e = self.errores(self.crear(fecha_reporte=(HOY + timedelta(days=1)).isoformat()))
        self.assertIn("aún no llega", e["fecha_reporte"][0])

    def test_no_acepta_una_fecha_lejana_en_el_futuro(self):
        self.assertIn("fecha_reporte", self.errores(self.crear(fecha_reporte=(HOY + timedelta(days=365)).isoformat())))

    def test_acepta_hoy(self):
        self.assertEqual(self.crear(fecha_reporte=HOY.isoformat()).status_code, 302)

    def test_no_acepta_fecha_ya_reportada(self):
        self.crear(fecha_reporte=HOY.isoformat())
        e = self.errores(self.crear(fecha_reporte=HOY.isoformat(), avance_fisico_real_porcentaje="25"))
        self.assertIn("Ya existe un reporte", e["fecha_reporte"][0])
        self.assertEqual(ReporteDiario.objects.count(), 1)

    def test_borrador_tambien_ocupa_la_fecha(self):
        self.crear(fecha_reporte=HOY.isoformat(), accion="borrador")
        self.assertIn("fecha_reporte", self.errores(self.crear(fecha_reporte=HOY.isoformat())))

    def test_otra_obra_si_puede_usar_la_fecha(self):
        self.reporte(0, "20")
        otra = Obra.objects.create(nombre="Otra obra")
        UsuarioProyectoRol.objects.create(usuario=self.supervisor, obra=otra, rol=Rol.objects.get(nombre="Supervisor"))
        self.client.force_login(self.supervisor)
        r = self.client.post(reverse("reporte_nuevo", args=[otra.pk]), self.datos(fecha_reporte=HOY.isoformat()))
        self.assertEqual(r.status_code, 302)

    def test_no_acepta_dia_anterior_al_inicio_de_la_obra(self):
        e = self.errores(self.crear(fecha_reporte=(HOY - timedelta(days=61)).isoformat()))
        self.assertIn("La obra inicia", e["fecha_reporte"][0])

    def test_editar_conserva_su_propia_fecha(self):
        reporte = self.reporte(5, "20", reportes.BORRADOR)
        self.client.force_login(self.supervisor)
        r = self.client.post(reverse("reporte_editar", args=[self.obra.pk, reporte.pk]),
                             self.datos(actividades_realizadas="Colado corregido del eje 3"))
        self.assertEqual(r.status_code, 302)

    def test_el_campo_avisa_en_el_navegador(self):
        self.reporte(3, "10")
        self.client.force_login(self.supervisor)
        r = self.client.get(reverse("reporte_nuevo", args=[self.obra.pk]))
        attrs = r.context["form"].fields["fecha_reporte"].widget.attrs
        self.assertEqual(attrs["max"], HOY.isoformat())
        self.assertEqual(attrs["data-ocupadas"], (HOY - timedelta(days=3)).isoformat())
        self.assertContains(r, f'max="{HOY.isoformat()}"')
        self.assertContains(r, "setCustomValidity")

    def test_al_editar_su_fecha_no_cuenta_como_ocupada(self):
        reporte = self.reporte(3, "10", reportes.BORRADOR)
        self.client.force_login(self.supervisor)
        r = self.client.get(reverse("reporte_editar", args=[self.obra.pk, reporte.pk]))
        self.assertEqual(r.context["form"].fields["fecha_reporte"].widget.attrs["data-ocupadas"], "")


class CamposTests(Base):
    """Cambio 4: todos los campos de la jornada llenos y con valores válidos."""

    def test_campos_obligatorios(self):
        for campo in ("fecha_reporte", "jornada_inicio", "jornada_fin", "clima", "temperatura_c",
                      "actividades_realizadas", "avance_fisico_real_porcentaje"):
            with self.subTest(campo=campo):
                self.assertIn(campo, self.errores(self.crear(**{campo: None})))
        self.assertFalse(ReporteDiario.objects.exists())

    def test_borrador_tambien_valida(self):
        self.assertIn("clima", self.errores(self.crear(clima=None, accion="borrador")))

    def test_fin_de_jornada_despues_del_inicio(self):
        for fin in ("08:00", "07:30"):
            with self.subTest(fin=fin):
                self.assertIn("jornada_fin", self.errores(self.crear(jornada_fin=fin)))

    def test_temperatura_en_rango(self):
        for t in ("-21", "56", "120"):
            with self.subTest(t=t):
                self.assertIn("temperatura_c", self.errores(self.crear(temperatura_c=t)))
        self.assertEqual(self.crear(temperatura_c="-5.5").status_code, 302)

    def test_temperatura_no_numerica(self):
        self.assertIn("temperatura_c", self.errores(self.crear(temperatura_c="calor")))

    def test_clima_fuera_del_catalogo(self):
        self.assertIn("clima", self.errores(self.crear(clima="Granizo de fuego")))

    def test_actividades_con_contenido(self):
        for texto in ("   ", "Colado"):
            with self.subTest(texto=texto):
                self.assertIn("actividades_realizadas", self.errores(self.crear(actividades_realizadas=texto)))

    def test_avance_entre_0_y_100(self):
        for avance in ("-1", "100.01", "abc"):
            with self.subTest(avance=avance):
                self.assertIn("avance_fisico_real_porcentaje",
                              self.errores(self.crear(avance_fisico_real_porcentaje=avance)))

    def test_hora_invalida(self):
        self.assertIn("jornada_inicio", self.errores(self.crear(jornada_inicio="25:00")))

    def test_avance_no_baja_respecto_a_un_dia_anterior(self):
        self.reporte(10, "30")
        e = self.errores(self.crear(avance_fisico_real_porcentaje="25"))
        self.assertIn("ya registra 30.00 %", e["avance_fisico_real_porcentaje"][0])

    def test_avance_no_sube_respecto_a_un_dia_posterior(self):
        self.reporte(2, "15")
        self.assertIn("avance_fisico_real_porcentaje", self.errores(self.crear(avance_fisico_real_porcentaje="20")))

    def test_avance_igual_al_anterior_si_se_acepta(self):
        self.reporte(10, "20")
        self.assertEqual(self.crear(avance_fisico_real_porcentaje="20").status_code, 302)

    def test_un_reporte_rechazado_no_cuenta_para_el_avance(self):
        self.reporte(10, "30", reportes.RECHAZADO)
        self.assertEqual(self.crear(avance_fisico_real_porcentaje="25").status_code, 302)

    def test_textos_opcionales_vacios_se_guardan_como_nulos(self):
        self.crear(incidencias="   ", observaciones="")
        reporte = ReporteDiario.objects.get()
        self.assertIsNone(reporte.incidencias)
        self.assertIsNone(reporte.observaciones)

    def test_reporte_completo_se_guarda_y_queda_pendiente(self):
        r = self.crear(incidencias="Falta de cemento", incidentes_seguridad="Ninguno", observaciones="Sin novedad")
        reporte = ReporteDiario.objects.get()
        self.assertRedirects(r, reverse("reporte_detalle", args=[self.obra.pk, reporte.pk]))
        self.assertEqual(reporte.estatus.nombre, reportes.PENDIENTE)
        self.assertEqual((reporte.clima, reporte.temperatura_c), ("Soleado", Decimal("24.0")))
