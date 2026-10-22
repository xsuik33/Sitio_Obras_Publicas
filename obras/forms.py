from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone

from .models import CatalogoEstatusValidacion, ReporteDiario


class ReporteDiarioForm(forms.ModelForm):
    class Meta:
        model = ReporteDiario
        fields = (
            "fecha_reporte", "jornada_inicio", "jornada_fin", "clima", "temperatura_c",
            "actividades_realizadas", "incidencias", "incidentes_seguridad",
            "avance_fisico_real_porcentaje", "observaciones",
        )
        labels = {
            "fecha_reporte": "Fecha del reporte",
            "jornada_inicio": "Inicio de jornada",
            "jornada_fin": "Fin de jornada",
            "clima": "Clima",
            "temperatura_c": "Temperatura (°C)",
            "actividades_realizadas": "Actividades realizadas",
            "incidencias": "Incidencias",
            "incidentes_seguridad": "Incidentes de seguridad",
            "avance_fisico_real_porcentaje": "Avance físico real (%)",
            "observaciones": "Observaciones",
        }
        widgets = {
            "fecha_reporte": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "jornada_inicio": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "jornada_fin": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "actividades_realizadas": forms.Textarea(attrs={"rows": 5}),
            "incidencias": forms.Textarea(attrs={"rows": 3}),
            "incidentes_seguridad": forms.Textarea(attrs={"rows": 3}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
            "avance_fisico_real_porcentaje": forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.01"}),
        }

    def __init__(self, *args, obra=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.obra = obra
        if not self.instance.pk:
            self.fields["fecha_reporte"].initial = timezone.localdate()
        self.fields["avance_fisico_real_porcentaje"].validators += [MinValueValidator(0), MaxValueValidator(100)]
        self.fields["clima"].required = False

    def clean_fecha_reporte(self):
        fecha = self.cleaned_data["fecha_reporte"]
        repetidos = ReporteDiario.objects.filter(obra=self.obra, fecha_reporte=fecha)
        if self.instance.pk:
            repetidos = repetidos.exclude(pk=self.instance.pk)
        if repetidos.exists():
            raise forms.ValidationError("Ya existe un reporte diario de esta obra para esa fecha.")
        return fecha

    def clean(self):
        datos = super().clean()
        inicio, fin = datos.get("jornada_inicio"), datos.get("jornada_fin")
        if inicio and fin and fin <= inicio:
            self.add_error("jornada_fin", "El fin de jornada debe ser posterior al inicio.")
        return datos


class ValidacionForm(forms.Form):
    estatus = forms.ModelChoiceField(
        label="Resultado de la revisión",
        queryset=CatalogoEstatusValidacion.objects.exclude(nombre="Pendiente").order_by("id"),
        empty_label="— elegir —",
    )
    comentarios = forms.CharField(label="Comentarios", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self):
        datos = super().clean()
        estatus, comentarios = datos.get("estatus"), (datos.get("comentarios") or "").strip()
        if estatus and estatus.nombre == "Rechazado" and not comentarios:
            self.add_error("comentarios", "Explica el motivo del rechazo.")
        datos["comentarios"] = comentarios
        return datos
