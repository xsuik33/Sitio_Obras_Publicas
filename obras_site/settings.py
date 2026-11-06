"""Configuración del Sistema de Obras (Django + PostgreSQL en Supabase, despliegue en Render)."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(nombre, defecto=False):
    return os.environ.get(nombre, str(defecto)).strip().lower() in ("1", "true", "yes", "si", "sí")


DEBUG = env_bool("DEBUG", False)

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "inseguro-solo-para-desarrollo"
    else:
        raise ImproperlyConfigured("Define la variable de entorno SECRET_KEY.")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = []
RENDER_HOST = os.environ.get("RENDER_EXTERNAL_HOSTNAME")  # Render la define sola
if RENDER_HOST:
    ALLOWED_HOSTS.append(RENDER_HOST)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_HOST}")
if DEBUG:
    ALLOWED_HOSTS += ["localhost", "127.0.0.1"]

# Correos (separados por coma) con acceso al panel /admin/.
ADMIN_EMAILS = [c.strip().lower() for c in os.environ.get("ADMIN_EMAILS", "").split(",") if c.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "obras",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "obras_site.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "obras.context_processors.navegacion",
            ],
        },
    },
]

WSGI_APPLICATION = "obras_site.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "postgres"),
        "USER": os.environ.get("DB_USER", ""),
        "PASSWORD": os.environ.get("DB_PASS", ""),
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "OPTIONS": {"sslmode": os.environ.get("DB_SSL", "prefer")},  # Supabase: require
    }
}

AUTH_USER_MODEL = "obras.Usuario"  # tabla `usuarios`
TEST_RUNNER = "obras.pruebas_bd.RunnerPostgres"  # carga db/esquema_obras.sql en la base de prueba
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "mis_obras"
LOGOUT_REDIRECT_URL = "login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-mx"
TIME_ZONE = "America/Mexico_City"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Evidencias: Supabase Storage (bucket privado) si hay SUPABASE_URL; si no, disco local en media/.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "evidencias")
SUPABASE_URL_CADUCIDAD = 300  # segundos que dura una URL firmada
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {
    "default": {"BACKEND": "obras.almacenamiento.SupabaseStorage" if SUPABASE_URL
                else "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
EVIDENCIA_MAX_MB = 10  # tamaño máximo por archivo
EVIDENCIA_MAX_ARCHIVOS = 10  # por envío
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Correo: API HTTP de Brevo (Render gratuito bloquea SMTP). Sin BREVO_API_KEY, los correos salen por consola.
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_API_URL = os.environ.get("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")
MAIL_FROM_EMAIL = os.environ.get("MAIL_FROM_EMAIL", "")
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "Sistema de Obras")
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")  # p. ej. https://sistema-obras.onrender.com (para los enlaces de los correos)
EMAIL_BACKEND = (
    "obras.correo.BrevoEmailBackend" if BREVO_API_KEY else "django.core.mail.backends.console.EmailBackend"
)

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
