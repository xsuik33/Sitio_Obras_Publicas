from decimal import Decimal

from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone

from .cuentas import ROLES_CUENTA
from .models import CatalogoEstatusValidacion, Obra, ReporteDiario, Rol, Usuario


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
        queryset=CatalogoEstatusValidacion.objects.none(),
        empty_label="— elegir —",
    )
    comentarios = forms.CharField(label="Comentarios", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, es_admin=False, **kwargs):
        super().__init__(*args, **kwargs)
        estatus = CatalogoEstatusValidacion.objects.order_by("id")
        # Solo el administrador puede regresar un reporte a "Pendiente".
        self.fields["estatus"].queryset = estatus if es_admin else estatus.exclude(nombre="Pendiente")

    def clean(self):
        datos = super().clean()
        estatus, comentarios = datos.get("estatus"), (datos.get("comentarios") or "").strip()
        if estatus and estatus.nombre == "Rechazado" and not comentarios:
            self.add_error("comentarios", "Explica el motivo del rechazo.")
        datos["comentarios"] = comentarios
        return datos


# ---------------------------------------------------------------------------
# Cuentas de usuario (CU-13)
# ---------------------------------------------------------------------------

def _validar_passwords(form, usuario=None):
    p1, p2 = form.cleaned_data.get("password1"), form.cleaned_data.get("password2")
    if p1 and p2:
        if p1 != p2:
            form.add_error("password2", "Las contraseñas no coinciden.")
        else:
            try:
                password_validation.validate_password(p1, usuario)
            except ValidationError as error:
                form.add_error("password1", error)


class CuentaNuevaForm(forms.Form):
    nombre = forms.CharField(label="Nombre", max_length=100)
    apellidos = forms.CharField(label="Apellidos", max_length=100)
    correo = forms.EmailField(label="Correo", max_length=100)
    rol = forms.ModelChoiceField(label="Rol", queryset=Rol.objects.none(), empty_label="— elegir —")
    obras = forms.ModelMultipleChoiceField(
        label="Obras donde tendrá este rol", queryset=Obra.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple, help_text="Puedes asignarle obras después, desde su ficha.",
    )
    password1 = forms.CharField(label="Contraseña inicial", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmar contraseña", strip=False, widget=forms.PasswordInput)

    def __init__(self, *args, obras=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rol"].queryset = Rol.objects.filter(nombre__in=ROLES_CUENTA).order_by("nombre")
        self.fields["obras"].queryset = (obras if obras is not None else Obra.objects.none()).order_by("nombre")

    def clean_correo(self):
        correo = self.cleaned_data["correo"].strip().lower()
        if Usuario.objects.filter(correo__iexact=correo).exists():
            raise ValidationError("Ya existe una cuenta con ese correo.")
        return correo

    def clean(self):
        datos = super().clean()
        _validar_passwords(self)
        return datos


class CuentaEdicionForm(forms.Form):
    nombre = forms.CharField(label="Nombre", max_length=100)
    apellidos = forms.CharField(label="Apellidos", max_length=100)
    correo = forms.EmailField(label="Correo", max_length=100)
    activo = forms.BooleanField(
        label="Cuenta activa", required=False,
        help_text="Una cuenta desactivada ya no puede iniciar sesión.",
    )

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario

    def clean_correo(self):
        correo = self.cleaned_data["correo"].strip().lower()
        if Usuario.objects.filter(correo__iexact=correo).exclude(pk=self.usuario.pk).exists():
            raise ValidationError("Ya existe una cuenta con ese correo.")
        return correo


class CuentaPasswordForm(forms.Form):
    password1 = forms.CharField(label="Nueva contraseña", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmar contraseña", strip=False, widget=forms.PasswordInput)

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario

    def clean(self):
        datos = super().clean()
        _validar_passwords(self, self.usuario)
        return datos


class AsignacionForm(forms.Form):
    obra = forms.ModelChoiceField(label="Obra", queryset=Obra.objects.none(), empty_label="— elegir —")
    rol = forms.ModelChoiceField(label="Rol", queryset=Rol.objects.none(), empty_label="— elegir —")

    def __init__(self, *args, obras=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["obra"].queryset = (obras if obras is not None else Obra.objects.none()).order_by("nombre")
        self.fields["rol"].queryset = Rol.objects.order_by("nombre")


# ---------------------------------------------------------------------------
# Catálogo de conceptos (CU-01)
# ---------------------------------------------------------------------------

class ConceptoForm(forms.Form):
    id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    capitulo = forms.CharField(label="Capítulo", max_length=100, required=False)
    clave = forms.CharField(label="Clave", max_length=50)
    descripcion = forms.CharField(label="Descripción", widget=forms.Textarea(attrs={"rows": 2}))
    unidad_medida = forms.CharField(label="Unidad", max_length=20)
    volumen_total_contratado = forms.DecimalField(
        label="Cantidad", max_digits=14, decimal_places=4, min_value=Decimal("0.0001"),
        widget=forms.NumberInput(attrs={"step": "any", "min": "0"}),
    )
    precio_unitario = forms.DecimalField(
        label="Precio unitario", max_digits=15, decimal_places=2, min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    )

    def clean_clave(self):
        return self.cleaned_data["clave"].strip()

    def clean_descripcion(self):
        return self.cleaned_data["descripcion"].strip()

    def clean_unidad_medida(self):
        return self.cleaned_data["unidad_medida"].strip()


class BaseConceptoFormSet(forms.BaseFormSet):
    def clean(self):
        """Flujo alternativo 4b: la clave no puede repetirse dentro del catálogo."""
        vistas = set()
        for form in self.forms:
            datos = getattr(form, "cleaned_data", None)
            if not datos or datos.get("DELETE") or not datos.get("clave"):
                continue
            clave = datos["clave"].upper()
            if clave in vistas:
                form.add_error("clave", f"La clave «{datos['clave']}» ya existe en el catálogo. Usa otra.")
            vistas.add(clave)
