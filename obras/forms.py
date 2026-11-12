import calendar
from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils import timezone

from . import reportes
from .cuentas import ROLES_CUENTA
from .mensuales import MESES
from .models import (
    Anticipo, CatalogoEstatusValidacion, CatalogoTipoNota, Contrato, Estimacion, NotaBitacora, Obra, ReporteDiario,
    ReporteMensual, Rol, Usuario,
)


class ReporteDiarioForm(forms.ModelForm):
    class Meta:
        model = ReporteDiario
        fields = (
            "fecha_reporte", "jornada_inicio", "jornada_fin", "clima", "temperatura_c",
            "actividades_realizadas", "incidencias", "incidentes_seguridad",
            "avance_fisico_real_porcentaje", "observaciones", "notas", "atraso_causa", "atraso_solicitud",
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
            "observaciones": "Observaciones y acciones correctivas",
            "notas": "Notas de bitácora del día",
            "atraso_causa": "Causa del atraso",
            "atraso_solicitud": "Solicitud",
        }
        help_texts = {
            "notas": "Marca las notas de bitácora relacionadas con este reporte (se muestran las de los últimos 30 días).",
            "atraso_solicitud": "Si pides reprogramación o retención, explica el motivo en la causa del atraso.",
        }
        widgets = {
            "fecha_reporte": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "jornada_inicio": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "jornada_fin": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "actividades_realizadas": forms.Textarea(attrs={"rows": 5}),
            "incidencias": forms.Textarea(attrs={"rows": 3}),
            "incidentes_seguridad": forms.Textarea(attrs={"rows": 3}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
            "atraso_causa": forms.Textarea(attrs={"rows": 3}),
            "notas": forms.CheckboxSelectMultiple,
            "avance_fisico_real_porcentaje": forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.01"}),
        }

    def __init__(self, *args, obra=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.obra = obra
        if self.instance._state.adding:
            self.fields["fecha_reporte"].initial = timezone.localdate()
        self.fields["avance_fisico_real_porcentaje"].validators += [MinValueValidator(0), MaxValueValidator(100)]
        # Todos los datos de la jornada son obligatorios; incidencias, seguridad y observaciones pueden ir vacías.
        for campo in ("jornada_inicio", "jornada_fin", "clima", "temperatura_c"):
            self.fields[campo].required = True
        self.fields["temperatura_c"].validators += [MinValueValidator(-20), MaxValueValidator(55)]
        self.fields["temperatura_c"].widget.attrs.update({"min": -20, "max": 55, "step": "0.1"})
        self.fields["clima"].choices = [("", "Elige el clima")] + ReporteDiario.CLIMAS
        # El navegador avisa en cuanto se elige una fecha futura o ya ocupada (la validación real está en clean).
        ocupadas = ReporteDiario.objects.filter(obra=obra)
        if self.instance.pk:
            ocupadas = ocupadas.exclude(pk=self.instance.pk)
        self.fields["fecha_reporte"].widget.attrs.update({
            "max": timezone.localdate().isoformat(),
            "data-ocupadas": ",".join(f.isoformat() for f in ocupadas.values_list("fecha_reporte", flat=True)),
        })
        self.fields["atraso_solicitud"].choices = [("", "Elige una opción")] + ReporteDiario.SOLICITUDES_ATRASO
        recientes = NotaBitacora.objects.filter(obra=obra, fecha_nota__date__gte=timezone.localdate() - timedelta(days=30))
        if self.instance.pk:
            recientes = recientes | NotaBitacora.objects.filter(pk__in=self.instance.notas.values("pk"))
        self.fields["notas"].queryset = recientes.select_related("tipo").order_by("-folio").distinct()
        self.fields["notas"].label_from_instance = (
            lambda n: f"Nota {n.folio:03d} · {n.tipo.nombre} · {timezone.localtime(n.fecha_nota):%d/%m/%Y} · "
                      f"{n.descripcion[:70]}{'…' if len(n.descripcion) > 70 else ''}"
        )
        self.dias_atraso = None

    def clean_fecha_reporte(self):
        fecha = self.cleaned_data["fecha_reporte"]
        if fecha > timezone.localdate():
            raise forms.ValidationError("No se puede reportar un día que aún no llega.")
        repetidos = ReporteDiario.objects.filter(obra=self.obra, fecha_reporte=fecha)
        if self.instance.pk:
            repetidos = repetidos.exclude(pk=self.instance.pk)
        if repetidos.exists():
            raise forms.ValidationError("Ya existe un reporte diario de esta obra para esa fecha.")
        inicio_obra = self.obra.fecha_inicio_oficial if self.obra else None
        if inicio_obra and fecha < inicio_obra:
            raise forms.ValidationError(
                f"La obra inicia el {inicio_obra:%d/%m/%Y}; no se puede reportar un día anterior."
            )
        return fecha

    def clean_actividades_realizadas(self):
        texto = (self.cleaned_data.get("actividades_realizadas") or "").strip()
        if len(texto) < 10:
            raise forms.ValidationError("Describe las actividades realizadas (al menos 10 caracteres).")
        return texto

    def clean(self):
        datos = super().clean()
        for campo in ("incidencias", "incidentes_seguridad", "observaciones"):
            if isinstance(datos.get(campo), str):
                datos[campo] = datos[campo].strip() or None
        inicio, fin = datos.get("jornada_inicio"), datos.get("jornada_fin")
        if inicio and fin and fin <= inicio:
            self.add_error("jornada_fin", "El fin de jornada debe ser posterior al inicio.")
        # El avance físico es acumulado: no puede ser menor que el de un día anterior ni mayor que el de uno posterior.
        fecha, avance = datos.get("fecha_reporte"), datos.get("avance_fisico_real_porcentaje")
        if fecha and avance is not None and self.obra:
            otros = ReporteDiario.objects.filter(obra=self.obra).exclude(estatus__nombre=reportes.RECHAZADO)
            if self.instance.pk:
                otros = otros.exclude(pk=self.instance.pk)
            antes = otros.filter(fecha_reporte__lt=fecha).order_by("-fecha_reporte").first()
            despues = otros.filter(fecha_reporte__gt=fecha).order_by("fecha_reporte").first()
            if antes and avance < antes.avance_fisico_real_porcentaje:
                self.add_error("avance_fisico_real_porcentaje",
                               f"El avance es acumulado: el reporte del {antes.fecha_reporte:%d/%m/%Y} ya registra "
                               f"{antes.avance_fisico_real_porcentaje} %.")
            elif despues and avance > despues.avance_fisico_real_porcentaje:
                self.add_error("avance_fisico_real_porcentaje",
                               f"El avance es acumulado: el reporte del {despues.fecha_reporte:%d/%m/%Y} registra "
                               f"{despues.avance_fisico_real_porcentaje} %, que es menos.")
        # Flujo 4a de CU-03: con atraso crítico se documenta la causa y la solicitud (reprogramación o retención).
        self.dias_atraso = reportes.dias_atraso(self.obra, datos.get("fecha_reporte"), datos.get("avance_fisico_real_porcentaje"))
        if reportes.atraso_critico(self.dias_atraso):
            texto = f"El avance va {self.dias_atraso} día{'s' if self.dias_atraso != 1 else ''} detrás del programa"
            if not (datos.get("atraso_causa") or "").strip():
                self.add_error("atraso_causa", f"{texto}: documenta la causa del atraso.")
            if not datos.get("atraso_solicitud"):
                self.add_error("atraso_solicitud", f"{texto}: indica si solicitas reprogramación o retención.")
        else:
            datos["atraso_causa"], datos["atraso_solicitud"] = None, None
        return datos


class ValidacionForm(forms.Form):
    """Decisión de un rol validador. El administrador puede además regresar la decisión a Pendiente."""
    rol = forms.ChoiceField(label="Decides como")
    estatus = forms.ModelChoiceField(
        label="Resultado de la revisión",
        queryset=CatalogoEstatusValidacion.objects.none(),
        empty_label="— elegir —",
    )
    comentarios = forms.CharField(label="Comentarios", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, es_admin=False, roles=(), **kwargs):
        super().__init__(*args, **kwargs)
        etiquetas = {"Superintendente Contratista": "Superintendente", "Residente": "Residente"}
        self.fields["rol"].choices = [(r, etiquetas.get(r, r)) for r in roles]
        permitidos = ("Pendiente", "Aprobado", "Rechazado") if es_admin else ("Aprobado", "Rechazado")
        self.fields["estatus"].queryset = CatalogoEstatusValidacion.objects.filter(nombre__in=permitidos).order_by("id")
        self.es_admin = es_admin
        if es_admin:  # CU-14 a CU-17: el administrador siempre deja el motivo del cambio
            self.fields["comentarios"].label = "Motivo del cambio"
            self.fields["comentarios"].required = True

    def clean(self):
        datos = super().clean()
        estatus, comentarios = datos.get("estatus"), (datos.get("comentarios") or "").strip()
        if self.es_admin and not comentarios:
            self.add_error("comentarios", "Escribe el motivo del cambio; queda en el historial.")
        elif estatus and estatus.nombre == "Rechazado" and not comentarios:
            self.add_error("comentarios", "Explica el motivo del rechazo.")
        datos["comentarios"] = comentarios
        return datos


class ProgramaForm(forms.Form):
    fecha = forms.DateField(label="Fecha", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    avance = forms.DecimalField(
        label="Avance programado acumulado (%)", max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        widget=forms.NumberInput(attrs={"min": 0, "max": 100, "step": "0.01"}),
    )


class BaseProgramaFormSet(forms.BaseFormSet):
    def clean(self):
        if any(self.errors):
            return
        filas = [f.cleaned_data for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        fechas = [d["fecha"] for d in filas]
        if len(fechas) != len(set(fechas)):
            raise ValidationError("Hay fechas repetidas en el programa.")
        filas.sort(key=lambda d: d["fecha"])
        for anterior, actual in zip(filas, filas[1:]):
            if actual["avance"] < anterior["avance"]:
                raise ValidationError(
                    f"El avance es acumulado y no puede bajar: el {actual['fecha']:%d/%m/%Y} "
                    f"({actual['avance']} %) es menor que el {anterior['fecha']:%d/%m/%Y} ({anterior['avance']} %)."
                )


# ---------------------------------------------------------------------------
# Reporte mensual (CU-04)
# ---------------------------------------------------------------------------

class ReporteMensualForm(forms.ModelForm):
    mes = forms.TypedChoiceField(label="Mes", coerce=int, choices=[(i + 1, m) for i, m in enumerate(MESES)])

    class Meta:
        model = ReporteMensual
        fields = ("anio", "mes", "fecha_corte", "resumen", "observaciones")
        labels = {
            "anio": "Año", "fecha_corte": "Fecha de corte",
            "resumen": "Resumen del mes", "observaciones": "Observaciones",
        }
        help_texts = {"fecha_corte": "Vacía = último día del mes. El consolidado toma los datos hasta esta fecha."}
        widgets = {
            "anio": forms.NumberInput(attrs={"min": 2000, "max": 2100}),
            "fecha_corte": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "resumen": forms.Textarea(attrs={"rows": 6}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, obra=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.obra = obra
        self.fields["fecha_corte"].required = False
        self.fields["resumen"].required = True
        if not self.instance._state.adding:  # el mes no cambia una vez creado
            self.fields["anio"].disabled = self.fields["mes"].disabled = True
        else:
            hoy = timezone.localdate()
            anterior = hoy.replace(day=1) - timedelta(days=1)  # por omisión, el mes anterior
            self.fields["anio"].initial, self.fields["mes"].initial = anterior.year, anterior.month

    def clean(self):
        datos = super().clean()
        anio, mes = datos.get("anio"), datos.get("mes")
        if not anio or not mes:
            return datos
        if not 2000 <= anio <= 2100:
            self.add_error("anio", "Año fuera de rango.")
            return datos
        inicio = date(anio, mes, 1)
        ultimo = date(anio, mes, calendar.monthrange(anio, mes)[1])
        if inicio > timezone.localdate():
            self.add_error("mes", "No se puede reportar un mes que aún no empieza.")
        corte = datos.get("fecha_corte") or ultimo
        if not inicio <= corte <= ultimo:
            self.add_error("fecha_corte", "La fecha de corte debe caer dentro del mes reportado.")
        datos["fecha_corte"] = corte
        repetidos = ReporteMensual.objects.filter(obra=self.obra, anio=anio, mes=mes)
        if not self.instance._state.adding:
            repetidos = repetidos.exclude(pk=self.instance.pk)
        if repetidos.exists():
            self.add_error("mes", "Ya existe el reporte mensual de ese mes para esta obra.")
        return datos


# ---------------------------------------------------------------------------
# Bitácora de obra (CU-09)
# ---------------------------------------------------------------------------

class NotaBitacoraForm(forms.ModelForm):
    class Meta:
        model = NotaBitacora
        fields = ("tipo", "descripcion", "nota_relacionada")
        labels = {"tipo": "Tipo de nota", "descripcion": "Contenido de la nota", "nota_relacionada": "Responde a la nota"}
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 8})}
        help_texts = {"nota_relacionada": "Opcional: la nota anterior a la que esta responde o corrige."}

    def __init__(self, *args, obra=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.obra = obra
        self.fields["tipo"].queryset = CatalogoTipoNota.objects.order_by("id")
        self.fields["tipo"].empty_label = "— elegir —"
        otras = NotaBitacora.objects.filter(obra=obra).order_by("-folio")
        if self.instance.pk:
            otras = otras.exclude(pk=self.instance.pk)
        self.fields["nota_relacionada"].queryset = otras
        self.fields["nota_relacionada"].label_from_instance = (
            lambda n: f"Folio {n.folio} · {n.fecha_nota:%d/%m/%Y} · {n.descripcion[:60]}"
        )
        self.fields["nota_relacionada"].empty_label = "— ninguna —"

    def clean_descripcion(self):
        texto = (self.cleaned_data.get("descripcion") or "").strip()
        if not texto:
            raise ValidationError("Escribe el contenido de la nota.")
        return texto


class FirmaNotaForm(forms.Form):
    """Firma o rechazo de un rol. El administrador puede además regresar la firma a Pendiente."""
    rol = forms.ChoiceField(label="Firmas como")
    decision = forms.ChoiceField(label="Decisión")
    comentarios = forms.CharField(label="Comentarios", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, es_admin=False, roles=(), **kwargs):
        super().__init__(*args, **kwargs)
        etiquetas = {"Superintendente Contratista": "Superintendente"}
        self.fields["rol"].choices = [(r, etiquetas.get(r, r)) for r in roles]
        opciones = [("Firmada", "Firmar"), ("Rechazada", "Rechazar")]
        if es_admin:
            opciones.append(("Pendiente", "Regresar a pendiente"))
        self.fields["decision"].choices = [("", "— elegir —")] + opciones

    def clean(self):
        datos = super().clean()
        comentarios = (datos.get("comentarios") or "").strip()
        if datos.get("decision") == "Rechazada" and not comentarios:
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


# ---------------------------------------------------------------------------
# Estimaciones (CU-02, CU-05, CU-08)
# ---------------------------------------------------------------------------

class EstimacionForm(forms.ModelForm):
    """Datos generales de la estimación; las cantidades por concepto se capturan en la tabla de la plantilla."""

    class Meta:
        model = Estimacion
        fields = ("periodo_inicio", "fecha_corte", "observaciones", "atraso_no_imputable", "justificacion_atraso")
        labels = {
            "periodo_inicio": "Inicio del periodo",
            "fecha_corte": "Fecha de corte",
            "observaciones": "Observaciones",
            "atraso_no_imputable": "El atraso no es imputable al contratista",
            "justificacion_atraso": "Justificación del atraso",
        }
        help_texts = {
            "fecha_corte": "El periodo no puede ser mayor a un mes (LOPSRM, art. 54).",
            "atraso_no_imputable": "Márcalo solo si el atraso se debe a causas ajenas al contratista; no se aplicará la "
                                   "retención por atraso y quedará a revisión del Supervisor y del Residente.",
            "justificacion_atraso": "Explica la causa y adjunta su evidencia en los documentos de soporte.",
        }
        widgets = {
            "periodo_inicio": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "fecha_corte": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
            "justificacion_atraso": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, contrato=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.contrato = contrato
        if self.instance._state.adding and contrato is not None:
            anterior = Estimacion.objects.filter(contrato=contrato).order_by("-numero_estimacion").first()
            inicio = anterior.fecha_corte + timedelta(days=1) if anterior else (contrato.fecha_inicio or contrato.obra.fecha_inicio_oficial)
            if inicio:
                self.fields["periodo_inicio"].initial = inicio
                self.fields["fecha_corte"].initial = min(
                    inicio + timedelta(days=calendar.monthrange(inicio.year, inicio.month)[1] - 1), timezone.localdate())

    def clean(self):
        from . import estimaciones
        datos = super().clean()
        inicio, corte = datos.get("periodo_inicio"), datos.get("fecha_corte")
        if inicio and corte:
            if corte < inicio:
                self.add_error("fecha_corte", "La fecha de corte no puede ser anterior al inicio del periodo.")
            elif not estimaciones.periodo_valido(inicio, corte):
                self.add_error("fecha_corte", "El periodo no puede ser mayor a un mes (LOPSRM, art. 54).")
            previa = Estimacion.objects.filter(contrato=self.contrato, fecha_corte__gte=inicio)
            if not self.instance._state.adding:
                previa = previa.exclude(pk=self.instance.pk).filter(numero_estimacion__lt=self.instance.numero_estimacion)
            previa = previa.order_by("-fecha_corte").first()
            if previa:
                self.add_error("periodo_inicio", f"Se traslapa con la {previa.folio}, que corta el {previa.fecha_corte:%d/%m/%Y}.")
        if datos.get("atraso_no_imputable") and not (datos.get("justificacion_atraso") or "").strip():
            self.add_error("justificacion_atraso", "Explica por qué el atraso no es imputable al contratista.")
        if not datos.get("atraso_no_imputable"):
            datos["justificacion_atraso"] = None
        return datos


class DecisionEstimacionForm(forms.Form):
    """Decisión del Supervisor (aprobar, observaciones o rechazar) o del Residente (aprobar o rechazar)."""
    rol = forms.ChoiceField(label="Decides como")
    estatus = forms.ModelChoiceField(label="Resultado", queryset=CatalogoEstatusValidacion.objects.none(),
                                     empty_label="Elige el resultado")
    comentarios = forms.CharField(label="Comentarios", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, es_admin=False, roles=(), **kwargs):
        from . import estimaciones
        super().__init__(*args, **kwargs)
        self.es_admin = es_admin
        self.fields["rol"].choices = [(r, r) for r in roles]
        if es_admin:
            nombres = ("Pendiente", "Aprobado", estimaciones.OBSERVACIONES, "Rechazado")
            self.fields["comentarios"].label = "Motivo del cambio"
            self.fields["comentarios"].required = True
        else:
            nombres = sorted({n for r in roles for n in estimaciones.OPCIONES[r]},
                             key=("Aprobado", estimaciones.OBSERVACIONES, "Rechazado").index)
        orden = {n: i for i, n in enumerate(nombres)}
        self.fields["estatus"].queryset = CatalogoEstatusValidacion.objects.filter(nombre__in=nombres)
        self.fields["estatus"].label_from_instance = lambda e: {"Aprobado": "Aprobar", "Rechazado": "Rechazar",
                                                                 estimaciones.OBSERVACIONES: "Regresar con observaciones",
                                                                 "Pendiente": "Regresar a Pendiente"}[e.nombre]
        self.orden = orden

    def clean(self):
        from . import estimaciones
        datos = super().clean()
        rol, estatus = datos.get("rol"), datos.get("estatus")
        comentarios = (datos.get("comentarios") or "").strip()
        if rol and estatus and estatus.nombre == estimaciones.OBSERVACIONES and rol != estimaciones.ROL_SUPERVISOR:
            self.add_error("estatus", "Solo el Supervisor puede regresarla con observaciones.")
        if self.es_admin and not comentarios:
            self.add_error("comentarios", "Escribe el motivo del cambio; queda en el historial.")
        elif estatus and estatus.nombre in ("Rechazado", estimaciones.OBSERVACIONES) and not comentarios:
            self.add_error("comentarios", "Explica qué debe corregirse.")
        datos["comentarios"] = comentarios
        return datos


class DocumentoForm(forms.Form):
    archivo = forms.FileField(label="Archivo")
    categoria = forms.ChoiceField(label="Tipo de documento")
    nombre = forms.CharField(label="Nombre", required=False, max_length=255,
                             help_text="Si lo dejas vacío se usa el nombre del archivo.")
    descripcion = forms.CharField(label="Descripción", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, categorias=None, **kwargs):
        from .documentos import ACEPTA, FORMATOS_LEGIBLES, max_mb
        super().__init__(*args, **kwargs)
        from .models import Documento
        self.fields["categoria"].choices = categorias or Documento.CATEGORIAS
        self.fields["archivo"].widget.attrs["accept"] = ACEPTA
        self.fields["archivo"].help_text = f"{FORMATOS_LEGIBLES}; hasta {max_mb()} MB."

    def clean_archivo(self):
        from .documentos import validar
        archivo = self.cleaned_data["archivo"]
        validar(archivo)
        return archivo


# ---------------------------------------------------------------------------
# Obras, contratos y anticipos (RF-01, RF-03, RF-04)
# ---------------------------------------------------------------------------

_FECHA = {"type": "date"}


class ObraForm(forms.ModelForm):
    class Meta:
        model = Obra
        fields = ("nombre", "ubicacion", "fecha_inicio_oficial", "fecha_fin_oficial")
        labels = {"nombre": "Nombre de la obra", "ubicacion": "Ubicación",
                  "fecha_inicio_oficial": "Fecha de inicio oficial", "fecha_fin_oficial": "Fecha de terminación oficial"}
        help_texts = {"fecha_inicio_oficial": "Con las fechas oficiales se calcula el estado de la obra y el arranque del programa."}
        widgets = {"ubicacion": forms.Textarea(attrs={"rows": 2}),
                   "fecha_inicio_oficial": forms.DateInput(attrs=_FECHA, format="%Y-%m-%d"),
                   "fecha_fin_oficial": forms.DateInput(attrs=_FECHA, format="%Y-%m-%d")}

    def clean(self):
        datos = super().clean()
        inicio, fin = datos.get("fecha_inicio_oficial"), datos.get("fecha_fin_oficial")
        if inicio and fin and fin < inicio:
            self.add_error("fecha_fin_oficial", "La terminación no puede ser anterior al inicio.")
        return datos


class ContratoForm(forms.ModelForm):
    BLOQUEABLES = ("monto_total_contratado", "porcentaje_iva", "porcentaje_retencion", "porcentaje_anticipo",
                   "porcentaje_retencion_atraso")

    class Meta:
        model = Contrato
        fields = ("numero_contrato", "contratista", "fecha_firma", "fecha_inicio", "fecha_fin", "monto_total_contratado",
                  "porcentaje_anticipo", "porcentaje_iva", "porcentaje_retencion", "porcentaje_retencion_atraso",
                  "monto_garantia_cumplimiento", "numero_fianza", "afianzadora")
        labels = {
            "numero_contrato": "Número de contrato", "contratista": "Contratista", "fecha_firma": "Fecha de firma",
            "fecha_inicio": "Inicio de los trabajos", "fecha_fin": "Terminación de los trabajos",
            "monto_total_contratado": "Monto contratado (sin IVA)", "porcentaje_anticipo": "Anticipo (%)",
            "porcentaje_iva": "IVA (%)", "porcentaje_retencion": "Retención por derechos (%)",
            "porcentaje_retencion_atraso": "Retención por atraso (%)",
            "monto_garantia_cumplimiento": "Garantía de cumplimiento ($)", "numero_fianza": "Número de fianza",
            "afianzadora": "Afianzadora",
        }
        help_texts = {
            "porcentaje_anticipo": "Hasta 30 % (LOPSRM, art. 50). Se amortiza en cada estimación.",
            "porcentaje_retencion": "Por ejemplo 0.5 (5 al millar de inspección y vigilancia).",
            "porcentaje_retencion_atraso": "Se aplica sobre el importe atrasado contra el programa (LOPSRM, art. 46 Bis).",
            "monto_garantia_cumplimiento": "Tope de las penas convencionales (LOPSRM, art. 46 Bis).",
        }
        widgets = {c: forms.DateInput(attrs=_FECHA, format="%Y-%m-%d") for c in ("fecha_firma", "fecha_inicio", "fecha_fin")}

    def __init__(self, *args, bloquear_montos=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.bloquear_montos = bloquear_montos
        for campo in ("porcentaje_anticipo", "porcentaje_iva", "porcentaje_retencion", "porcentaje_retencion_atraso"):
            self.fields[campo].validators += [MinValueValidator(0), MaxValueValidator(100)]
            self.fields[campo].widget.attrs.update({"min": 0, "max": 100, "step": "0.01"})
        self.fields["porcentaje_anticipo"].validators.append(MaxValueValidator(30))
        self.fields["monto_total_contratado"].validators.append(MinValueValidator(Decimal("0.01")))
        if self.instance._state.adding:
            self.fields["porcentaje_iva"].initial = 16
            self.fields["porcentaje_retencion_atraso"].initial = 5
        if bloquear_montos:
            for campo in self.BLOQUEABLES:
                self.fields[campo].disabled = True
                self.fields[campo].help_text = "Bloqueado: el contrato ya tiene estimaciones y sus montos no se modifican."

    def clean(self):
        datos = super().clean()
        inicio, fin = datos.get("fecha_inicio"), datos.get("fecha_fin")
        if inicio and fin and fin < inicio:
            self.add_error("fecha_fin", "La terminación no puede ser anterior al inicio.")
        garantia, monto = datos.get("monto_garantia_cumplimiento"), datos.get("monto_total_contratado")
        if garantia is not None and garantia < 0:
            self.add_error("monto_garantia_cumplimiento", "No puede ser negativa.")
        elif garantia and monto and garantia > monto:
            self.add_error("monto_garantia_cumplimiento", "No puede ser mayor que el monto contratado.")
        return datos


class AnticipoForm(forms.ModelForm):
    class Meta:
        model = Anticipo
        fields = ("tipo", "monto", "fecha_programada", "fecha_pago", "estatus", "observaciones")
        labels = {"tipo": "Tipo", "monto": "Monto ($)", "fecha_programada": "Fecha programada",
                  "fecha_pago": "Fecha de pago", "estatus": "Estatus", "observaciones": "Observaciones"}
        widgets = {"fecha_programada": forms.DateInput(attrs=_FECHA, format="%Y-%m-%d"),
                   "fecha_pago": forms.DateInput(attrs=_FECHA, format="%Y-%m-%d"),
                   "observaciones": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, contrato=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.contrato = contrato
        self.fields["monto"].validators.append(MinValueValidator(Decimal("0.01")))
        self.fields["tipo"].choices = Anticipo.TIPOS
        self.fields["estatus"].choices = Anticipo.ESTATUS

    def clean(self):
        datos = super().clean()
        if datos.get("estatus") == "Pagado" and not datos.get("fecha_pago"):
            self.add_error("fecha_pago", "Indica la fecha en que se pagó.")
        monto = datos.get("monto")
        if monto and self.contrato is not None:
            otros = self.contrato.anticipo_set.exclude(estatus="Cancelado")
            if self.instance.pk:
                otros = otros.exclude(pk=self.instance.pk)
            total = sum((a.monto for a in otros), Decimal(0)) + (monto if datos.get("estatus") != "Cancelado" else 0)
            if total > self.contrato.monto_anticipo:
                self.add_error("monto", f"Las ministraciones suman más que el anticipo autorizado "
                                        f"($ {self.contrato.monto_anticipo:,.2f}).")
        return datos
