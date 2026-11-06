"""
Modelos del Sistema de Obras.

El esquema lo crea y mantiene PostgreSQL (db/esquema_obras.sql), no Django:
todos los modelos son `managed = False`. No ejecutes `makemigrations` por cambios
de columnas: primero se cambia el SQL y después se actualiza este archivo.

Columnas calculadas por la BD (GeneratedField o triggers) no se escriben desde Django.
"""
import uuid

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models import DecimalField, F, Value
from django.db.models.functions import Now, Round


def pk_uuid():
    return models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)


def dinero(**kw):
    return models.DecimalField(max_digits=15, decimal_places=2, **kw)


def fecha_auto():
    """TIMESTAMPTZ con DEFAULT now() en la BD."""
    return models.DateTimeField(db_default=Now(), editable=False)


# ---------------------------------------------------------------------------
# Usuarios y roles
# ---------------------------------------------------------------------------

class UsuarioManager(BaseUserManager):
    def create_user(self, correo, password=None, **extra):
        if not correo:
            raise ValueError("El correo es obligatorio")
        usuario = self.model(correo=self.normalize_email(correo), **extra)
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_superuser(self, correo, password=None, **extra):
        # El acceso al panel /admin/ se concede con la variable ADMIN_EMAILS.
        return self.create_user(correo, password, **extra)


class Usuario(AbstractBaseUser):
    id = pk_uuid()
    nombre = models.CharField(max_length=100)
    apellidos = models.CharField(max_length=100)
    correo = models.EmailField(max_length=100, unique=True)  # CITEXT en la BD
    password = models.CharField(max_length=255, db_column="password_hash")
    activo = models.BooleanField(db_default=True)
    fecha_creacion = fecha_auto()

    last_login = None  # la tabla no tiene esta columna

    USERNAME_FIELD = "correo"
    REQUIRED_FIELDS = ["nombre", "apellidos"]
    objects = UsuarioManager()

    class Meta:
        managed = False
        db_table = "usuarios"
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"

    def __str__(self):
        return f"{self.nombre} {self.apellidos}"

    @property
    def is_active(self):
        return self.activo

    @property
    def es_admin_panel(self):
        return self.correo.lower() in settings.ADMIN_EMAILS

    @property
    def is_staff(self):
        return self.activo and self.es_admin_panel

    @property
    def is_superuser(self):
        return self.activo and self.es_admin_panel

    def has_perm(self, perm, obj=None):
        return self.is_superuser

    def has_module_perms(self, app_label):
        return self.is_superuser


class Rol(models.Model):
    id = pk_uuid()
    nombre = models.CharField(max_length=50, unique=True)

    class Meta:
        managed = False
        db_table = "roles"
        verbose_name = "Rol"
        verbose_name_plural = "Roles"

    def __str__(self):
        return self.nombre


class CatalogoEstatusValidacion(models.Model):
    id = models.SmallAutoField(primary_key=True)
    nombre = models.CharField(max_length=50, unique=True)
    descripcion = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "catalogo_estatus_validacion"
        verbose_name = "Estatus de validación"
        verbose_name_plural = "Estatus de validación"

    def __str__(self):
        return self.nombre


# ---------------------------------------------------------------------------
# Obra, contrato y catálogo de conceptos
# ---------------------------------------------------------------------------

class Obra(models.Model):
    id = pk_uuid()
    nombre = models.CharField(max_length=255)
    ubicacion = models.TextField(null=True, blank=True)
    fecha_inicio_oficial = models.DateField(null=True, blank=True)
    fecha_fin_oficial = models.DateField(null=True, blank=True)
    # Contador de folios de la bitácora; lo incrementa el trigger trg_nota_folio.
    ultimo_folio_bitacora = models.IntegerField(db_default=0, editable=False)
    fecha_registro = fecha_auto()

    class Meta:
        managed = False
        db_table = "obras"
        verbose_name = "Obra"
        verbose_name_plural = "Obras"

    def __str__(self):
        return self.nombre


class UsuarioProyectoRol(models.Model):
    """Rol de un usuario en una obra. Llave primaria compuesta (Django 5.2+)."""
    pk = models.CompositePrimaryKey("usuario_id", "obra_id", "rol_id")
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE)
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE)
    rol = models.ForeignKey(Rol, on_delete=models.CASCADE)

    class Meta:
        managed = False
        db_table = "usuarios_proyectos_roles"
        verbose_name = "Rol en obra"
        verbose_name_plural = "Roles en obras"


class Contrato(models.Model):
    id = pk_uuid()
    obra = models.OneToOneField(Obra, on_delete=models.CASCADE)
    numero_contrato = models.CharField(max_length=100, unique=True)
    contratista = models.CharField(max_length=255)
    fecha_firma = models.DateField(null=True, blank=True)
    fecha_inicio = models.DateField(null=True, blank=True)
    fecha_fin = models.DateField(null=True, blank=True)
    monto_total_contratado = dinero()
    porcentaje_iva = models.DecimalField(max_digits=5, decimal_places=2, db_default=16)
    porcentaje_retencion = models.DecimalField(max_digits=5, decimal_places=2, db_default=0)
    porcentaje_anticipo = models.DecimalField(max_digits=5, decimal_places=2, db_default=0)
    monto_anticipo = models.GeneratedField(
        expression=Round(
            F("monto_total_contratado") * F("porcentaje_anticipo") / Value(100, output_field=DecimalField()), 2
        ),
        output_field=DecimalField(max_digits=15, decimal_places=2),
        db_persist=True,
    )
    fecha_registro = fecha_auto()

    class Meta:
        managed = False
        db_table = "contratos"
        verbose_name = "Contrato"
        verbose_name_plural = "Contratos"

    def __str__(self):
        return self.numero_contrato


class Anticipo(models.Model):
    TIPOS = [("Inicio de obra", "Inicio de obra"), ("Materiales", "Materiales"), ("Otro", "Otro")]
    ESTATUS = [("Programado", "Programado"), ("Pagado", "Pagado"), ("Cancelado", "Cancelado")]

    id = pk_uuid()
    contrato = models.ForeignKey(Contrato, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=30, choices=TIPOS, db_default="Inicio de obra")
    monto = dinero()
    fecha_programada = models.DateField(null=True, blank=True)
    fecha_pago = models.DateField(null=True, blank=True)
    estatus = models.CharField(max_length=20, choices=ESTATUS, db_default="Programado")
    observaciones = models.TextField(null=True, blank=True)
    fecha_registro = fecha_auto()

    class Meta:
        managed = False
        db_table = "anticipos"
        verbose_name = "Anticipo"
        verbose_name_plural = "Anticipos"


class CatalogoConcepto(models.Model):
    id = pk_uuid()
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE)
    capitulo = models.CharField(max_length=100, null=True, blank=True)
    clave = models.CharField(max_length=50)
    descripcion = models.TextField()
    unidad_medida = models.CharField(max_length=20)
    volumen_total_contratado = models.DecimalField(max_digits=14, decimal_places=4)
    precio_unitario = dinero()
    importe_contratado = models.GeneratedField(
        expression=Round(F("volumen_total_contratado") * F("precio_unitario"), 2),
        output_field=DecimalField(max_digits=18, decimal_places=2),
        db_persist=True,
    )

    class Meta:
        managed = False
        db_table = "catalogo_conceptos"
        verbose_name = "Concepto de catálogo"
        verbose_name_plural = "Catálogo de conceptos"
        constraints = [models.UniqueConstraint(fields=["obra", "clave"], name="uq_conceptos_obra_clave")]

    def __str__(self):
        return f"{self.clave} - {self.descripcion[:40]}"


# ---------------------------------------------------------------------------
# Estimaciones
# ---------------------------------------------------------------------------

class Estimacion(models.Model):
    """
    subtotal, retencion, iva y monto_neto los calculan triggers de la BD
    a partir de los renglones (EstimacionDetalle) y de amortizacion_anticipo.
    Tras cambiar renglones usa `estimacion.refresh_from_db()`.
    """
    TIPOS = [("Ordinaria", "Ordinaria"), ("Extraordinaria", "Extraordinaria"), ("Finiquito", "Finiquito")]
    CALCULADOS = ("subtotal", "retencion", "iva", "monto_neto")

    id = pk_uuid()
    contrato = models.ForeignKey(Contrato, on_delete=models.PROTECT)
    superintendente = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    numero_estimacion = models.IntegerField()
    folio = models.CharField(max_length=50, unique=True)
    tipo = models.CharField(max_length=20, choices=TIPOS, db_default="Ordinaria")
    periodo_inicio = models.DateField()
    fecha_corte = models.DateField()
    fecha_presentacion = models.DateField(null=True, blank=True)
    subtotal = dinero(db_default=0, editable=False)
    amortizacion_anticipo = dinero(db_default=0)
    retencion = dinero(db_default=0, editable=False)
    iva = dinero(db_default=0, editable=False)
    monto_neto = dinero(db_default=0, editable=False)
    estatus = models.ForeignKey(CatalogoEstatusValidacion, on_delete=models.PROTECT, db_default=1)
    observaciones = models.TextField(null=True, blank=True)
    fecha_registro = fecha_auto()

    class Meta:
        managed = False
        db_table = "estimaciones"
        verbose_name = "Estimación"
        verbose_name_plural = "Estimaciones"
        constraints = [
            models.UniqueConstraint(fields=["contrato", "numero_estimacion"], name="uq_estimaciones_contrato_numero")
        ]

    def __str__(self):
        return self.folio

    def save(self, *args, **kwargs):
        # Evita sobrescribir con valores viejos los totales que mantiene la BD.
        if not self._state.adding and kwargs.get("update_fields") is None:
            kwargs["update_fields"] = [
                f.name for f in self._meta.concrete_fields
                if f.name not in self.CALCULADOS and not f.primary_key
            ]
        super().save(*args, **kwargs)


class EstimacionDetalle(models.Model):
    """
    Captura solo estimacion, concepto y cantidad_ejecutada. Si precio_unitario va vacío,
    la BD toma el del catálogo. Acumulado anterior, importe y excedente los calcula la BD.
    """
    id = pk_uuid()
    estimacion = models.ForeignKey(Estimacion, on_delete=models.CASCADE)
    concepto = models.ForeignKey(CatalogoConcepto, on_delete=models.PROTECT)
    cantidad_ejecutada = models.DecimalField(max_digits=14, decimal_places=4)
    cantidad_acumulada_anterior = models.DecimalField(max_digits=14, decimal_places=4, db_default=0, editable=False)
    cantidad_acumulada = models.GeneratedField(
        expression=F("cantidad_acumulada_anterior") + F("cantidad_ejecutada"),
        output_field=DecimalField(max_digits=14, decimal_places=4),
        db_persist=True,
    )
    precio_unitario = dinero(null=True, blank=True)
    importe = models.GeneratedField(
        expression=Round(F("cantidad_ejecutada") * F("precio_unitario"), 2),
        output_field=DecimalField(max_digits=15, decimal_places=2),
        db_persist=True,
    )
    es_excedente = models.BooleanField(db_default=False, editable=False)
    numero_generador = models.CharField(max_length=50, null=True, blank=True)
    observaciones = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "estimaciones_detalles"
        verbose_name = "Renglón de estimación"
        verbose_name_plural = "Renglones de estimación"
        constraints = [
            models.UniqueConstraint(fields=["estimacion", "concepto"], name="uq_detalle_estimacion_concepto")
        ]


# ---------------------------------------------------------------------------
# Reportes, bitácora, documentos y evidencias
# ---------------------------------------------------------------------------

class ReporteDiario(models.Model):
    CLIMAS = [(c, c) for c in ("Soleado", "Nublado", "Lluvia", "Tormenta", "Viento fuerte", "Otro")]

    id = pk_uuid()
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE)
    supervisor = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    fecha_reporte = models.DateField()
    jornada_inicio = models.TimeField(null=True, blank=True)
    jornada_fin = models.TimeField(null=True, blank=True)
    clima = models.CharField(max_length=20, choices=CLIMAS, null=True, blank=True)
    temperatura_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    actividades_realizadas = models.TextField()
    incidencias = models.TextField(null=True, blank=True)
    incidentes_seguridad = models.TextField(null=True, blank=True)
    avance_fisico_real_porcentaje = models.DecimalField(max_digits=5, decimal_places=2)
    observaciones = models.TextField(null=True, blank=True)
    estatus = models.ForeignKey(CatalogoEstatusValidacion, on_delete=models.PROTECT, db_default=1)
    fecha_registro = fecha_auto()

    class Meta:
        managed = False
        db_table = "reportes_diarios"
        verbose_name = "Reporte diario"
        verbose_name_plural = "Reportes diarios"
        constraints = [models.UniqueConstraint(fields=["obra", "fecha_reporte"], name="uq_reporte_obra_fecha")]


class ProgramaObra(models.Model):
    """Avance físico programado ACUMULADO (%) en una fecha clave; entre fechas se interpola."""
    id = pk_uuid()
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE)
    fecha = models.DateField()
    avance_programado_porcentaje = models.DecimalField(max_digits=5, decimal_places=2)
    fecha_registro = fecha_auto()

    class Meta:
        managed = False
        db_table = "programa_obra"
        verbose_name = "Programa de obra"
        verbose_name_plural = "Programa de obra"
        constraints = [models.UniqueConstraint(fields=["obra", "fecha"], name="uq_programa_obra_fecha")]


class ReporteMensual(models.Model):
    """
    Reporte mensual (CU-04). Mismo flujo que el diario (ver obras/mensuales.py).
    `consolidado` guarda las cifras del mes al aprobarse; mientras es NULL se calculan al vuelo.
    """
    id = pk_uuid()
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE)
    supervisor = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    anio = models.SmallIntegerField()
    mes = models.SmallIntegerField()
    fecha_corte = models.DateField()
    resumen = models.TextField(null=True, blank=True)
    observaciones = models.TextField(null=True, blank=True)
    estatus = models.ForeignKey(CatalogoEstatusValidacion, on_delete=models.PROTECT, db_default=1)
    consolidado = models.JSONField(null=True, blank=True, editable=False)
    fecha_registro = fecha_auto()
    fecha_modificacion = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        managed = False
        db_table = "reportes_mensuales"
        verbose_name = "Reporte mensual"
        verbose_name_plural = "Reportes mensuales"
        constraints = [models.UniqueConstraint(fields=["obra", "anio", "mes"], name="uq_reporte_mensual")]


class CatalogoTipoNota(models.Model):
    id = models.SmallAutoField(primary_key=True)
    nombre = models.CharField(max_length=50, unique=True)
    descripcion = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "catalogo_tipos_nota"
        verbose_name = "Tipo de nota"
        verbose_name_plural = "Tipos de nota"

    def __str__(self):
        return self.nombre


class NotaBitacora(models.Model):
    """
    Nota de la bitácora de obra (CU-09). El folio lo asigna la BD (trigger trg_nota_folio).
    estado_firma se deriva de las firmas (ver obras/bitacora.py).
    """
    FIRMAS = [("Pendiente", "Pendiente"), ("Firmada", "Firmada"), ("Rechazada", "Rechazada")]

    id = pk_uuid()
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE)
    folio = models.IntegerField(db_default=0, editable=False)
    tipo = models.ForeignKey(CatalogoTipoNota, on_delete=models.PROTECT, db_default=7)
    usuario_creador = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    fecha_nota = fecha_auto()
    fecha_modificacion = models.DateTimeField(null=True, blank=True, editable=False)
    descripcion = models.TextField()
    nota_relacionada = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="notas_que_la_citan"
    )
    estado_firma = models.CharField(max_length=20, choices=FIRMAS, db_default="Pendiente")

    class Meta:
        managed = False
        db_table = "notas_bitacora"
        verbose_name = "Nota de bitácora"
        verbose_name_plural = "Notas de bitácora"
        constraints = [models.UniqueConstraint(fields=["obra", "folio"], name="uq_notas_obra_folio")]

    def __str__(self):
        return f"Nota {self.folio}"


class FirmaNotaBitacora(models.Model):
    """Decisión de un rol sobre una nota. La última de cada rol es la vigente; 'Pendiente' reinicia."""
    DECISIONES = [("Firmada", "Firmada"), ("Rechazada", "Rechazada"), ("Pendiente", "Pendiente")]

    id = pk_uuid()
    nota_bitacora = models.ForeignKey(NotaBitacora, on_delete=models.CASCADE, related_name="firmas")
    usuario_firmante = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    rol_firmante = models.CharField(max_length=40)
    decision = models.CharField(max_length=20, choices=DECISIONES)
    fecha_firma = fecha_auto()
    comentarios = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "firmas_notas_bitacora"
        verbose_name = "Firma de nota de bitácora"
        verbose_name_plural = "Firmas de notas de bitácora"


class Documento(models.Model):
    id = pk_uuid()
    obra = models.ForeignKey(Obra, on_delete=models.CASCADE)
    usuario_subida = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    nombre_archivo = models.CharField(max_length=255)
    ruta_almacenamiento = models.CharField(max_length=500)
    tipo_archivo = models.CharField(max_length=50, null=True, blank=True)
    descripcion = models.TextField(null=True, blank=True)
    fecha_subida = fecha_auto()

    class Meta:
        managed = False
        db_table = "documentos"
        verbose_name = "Documento"
        verbose_name_plural = "Documentos"


class Evidencia(models.Model):
    """Pertenece a exactamente uno: reporte diario, reporte mensual o nota de bitácora."""
    id = pk_uuid()
    ruta = models.CharField(max_length=500)
    tipo = models.CharField(max_length=50, null=True, blank=True)
    descripcion = models.TextField(null=True, blank=True)
    usuario_subida = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    fecha_subida = fecha_auto()
    reporte_diario = models.ForeignKey(ReporteDiario, on_delete=models.CASCADE, null=True, blank=True)
    reporte_mensual = models.ForeignKey(ReporteMensual, on_delete=models.CASCADE, null=True, blank=True)
    nota_bitacora = models.ForeignKey(NotaBitacora, on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "evidencias"
        verbose_name = "Evidencia"
        verbose_name_plural = "Evidencias"


# ---------------------------------------------------------------------------
# Validaciones (historial de revisiones)
# ---------------------------------------------------------------------------

class ValidacionEstimacion(models.Model):
    id = pk_uuid()
    estimacion = models.ForeignKey(Estimacion, on_delete=models.CASCADE)
    usuario_validador = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    estatus = models.ForeignKey(CatalogoEstatusValidacion, on_delete=models.PROTECT)
    fecha_validacion = fecha_auto()
    comentarios = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "validaciones_estimaciones"
        verbose_name = "Validación de estimación"
        verbose_name_plural = "Validaciones de estimaciones"


class ValidacionReporteDiario(models.Model):
    id = pk_uuid()
    reporte_diario = models.ForeignKey(ReporteDiario, on_delete=models.CASCADE)
    usuario_validador = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    # Rol en cuyo nombre se decidió (Residente o Superintendente Contratista); nulo en registros anteriores a CU-03.
    rol_validador = models.CharField(max_length=40, null=True, blank=True)
    estatus = models.ForeignKey(CatalogoEstatusValidacion, on_delete=models.PROTECT)
    fecha_validacion = fecha_auto()
    comentarios = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "validaciones_reportes_diarios"
        verbose_name = "Validación de reporte diario"
        verbose_name_plural = "Validaciones de reportes diarios"


class ValidacionReporteMensual(models.Model):
    id = pk_uuid()
    reporte_mensual = models.ForeignKey(ReporteMensual, on_delete=models.CASCADE, related_name="validaciones")
    usuario_validador = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    rol_validador = models.CharField(max_length=40)
    estatus = models.ForeignKey(CatalogoEstatusValidacion, on_delete=models.PROTECT)
    fecha_validacion = fecha_auto()
    comentarios = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "validaciones_reportes_mensuales"
        verbose_name = "Validación de reporte mensual"
        verbose_name_plural = "Validaciones de reportes mensuales"
