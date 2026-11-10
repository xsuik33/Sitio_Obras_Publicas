"""
Recalcula el estatus de los reportes diarios y mensuales con la regla vigente: solo valida el Residente.

Úsalo una vez tras el cambio (antes validaban Residente y Superintendente). Solo toca reportes en revisión
(Pendiente, En revisión o Rechazado) que tengan alguna decisión por rol; los Borradores, los Aprobados y los
registros anteriores a CU-03 se quedan igual. Sin --aplicar solo muestra lo que cambiaría. Es idempotente.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from obras import mensuales, reportes
from obras.models import ReporteDiario, ReporteMensual, ValidacionReporteDiario, ValidacionReporteMensual

EN_REVISION = (reportes.PENDIENTE, reportes.EN_REVISION, reportes.RECHAZADO)


class Command(BaseCommand):
    help = "Recalcula el estatus de reportes diarios y mensuales con la validación solo del Residente."

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true", help="Guarda los cambios (sin esto, solo los muestra).")

    def handle(self, *args, aplicar=False, **opciones):
        cambios = 0
        with transaction.atomic():
            con_decision = ValidacionReporteDiario.objects.filter(rol_validador__isnull=False).values("reporte_diario")
            diarios = ReporteDiario.objects.filter(estatus__nombre__in=EN_REVISION, pk__in=con_decision)
            for r in diarios.select_related("estatus", "obra"):
                nuevo = reportes.estatus_calculado(reportes.decisiones(r))
                if nuevo != r.estatus.nombre:
                    cambios += 1
                    self.stdout.write(f"Diario {r.obra.nombre} {r.fecha_reporte}: {r.estatus.nombre} → {nuevo}")
                    if aplicar:
                        r.estatus = reportes.estatus(nuevo)
                        r.save(update_fields=["estatus"])
            con_decision = ValidacionReporteMensual.objects.values("reporte_mensual")
            mensuales_qs = ReporteMensual.objects.filter(estatus__nombre__in=EN_REVISION, pk__in=con_decision)
            for r in mensuales_qs.select_related("estatus", "obra"):
                nuevo = reportes.estatus_calculado(mensuales.decisiones(r))
                if nuevo != r.estatus.nombre:
                    cambios += 1
                    self.stdout.write(f"Mensual {r.obra.nombre} {mensuales.nombre_mes(r)}: {r.estatus.nombre} → {nuevo}")
                    if aplicar:
                        mensuales._fijar_estatus(r, nuevo)  # congela el consolidado si queda aprobado
        accion = "Actualizados" if aplicar else "Por actualizar (usa --aplicar para guardar)"
        self.stdout.write(self.style.SUCCESS(f"{accion}: {cambios} reporte(s)."))
