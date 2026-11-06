from django import forms
from django.contrib import admin
from django.contrib.auth import password_validation

from . import models


class UsuarioCreacionForm(forms.ModelForm):
    activo = forms.BooleanField(label="Activo", required=False, initial=True)
    password1 = forms.CharField(label="Contraseña", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirmar contraseña", strip=False, widget=forms.PasswordInput)

    class Meta:
        model = models.Usuario
        fields = ("correo", "nombre", "apellidos", "activo")

    def clean_password2(self):
        p1, p2 = self.cleaned_data.get("password1"), self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Las contraseñas no coinciden.")
        password_validation.validate_password(p2, self.instance)
        return p2

    def save(self, commit=True):
        usuario = super().save(commit=False)
        usuario.set_password(self.cleaned_data["password1"])  # se guarda cifrada, nunca en claro
        if commit:
            usuario.save()
        return usuario


class UsuarioEdicionForm(forms.ModelForm):
    nueva_password = forms.CharField(
        label="Nueva contraseña", required=False, strip=False, widget=forms.PasswordInput,
        help_text="Déjala vacía para conservar la actual.",
    )

    class Meta:
        model = models.Usuario
        fields = ("correo", "nombre", "apellidos", "activo")

    def clean_nueva_password(self):
        password = self.cleaned_data.get("nueva_password")
        if password:
            password_validation.validate_password(password, self.instance)
        return password

    def save(self, commit=True):
        usuario = super().save(commit=False)
        if self.cleaned_data.get("nueva_password"):
            usuario.set_password(self.cleaned_data["nueva_password"])
        if commit:
            usuario.save()
        return usuario


@admin.register(models.Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ("correo", "nombre", "apellidos", "activo")
    search_fields = ("correo", "nombre", "apellidos")
    list_filter = ("activo",)

    def get_form(self, request, obj=None, **kwargs):
        kwargs["form"] = UsuarioCreacionForm if obj is None else UsuarioEdicionForm
        return super().get_form(request, obj, **kwargs)

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return [(None, {"fields": ("correo", "nombre", "apellidos", "activo", "password1", "password2")})]
        return [(None, {"fields": ("correo", "nombre", "apellidos", "activo", "nueva_password")})]


# UsuarioProyectoRol no se registra: el admin de Django no soporta llaves primarias compuestas.
# Los roles por obra se asignan en la página /obras/<id>/roles/.
admin.site.register([
    models.Rol, models.CatalogoEstatusValidacion, models.Obra, models.Contrato,
    models.Anticipo, models.CatalogoConcepto, models.Estimacion, models.EstimacionDetalle,
    models.ReporteDiario, models.ReporteMensual, models.NotaBitacora, models.Documento,
    models.Evidencia, models.ProgramaObra, models.ValidacionEstimacion, models.ValidacionReporteDiario,
])
