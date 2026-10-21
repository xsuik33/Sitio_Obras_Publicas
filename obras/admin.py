from django.contrib import admin

from . import models

# UsuarioProyectoRol no se registra: el admin de Django no soporta llaves primarias compuestas.
admin.site.register([
    models.Usuario, models.Rol, models.CatalogoEstatusValidacion, models.Obra, models.Contrato,
    models.Anticipo, models.CatalogoConcepto, models.Estimacion, models.EstimacionDetalle,
    models.ReporteDiario, models.ReporteMensual, models.NotaBitacora, models.Documento,
    models.Evidencia, models.ValidacionEstimacion, models.ValidacionReporteDiario,
])
