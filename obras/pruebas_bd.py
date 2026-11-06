"""
Runner de pruebas para PostgreSQL.

Los modelos son `managed = False`, así que Django no crea las tablas en la base de prueba.
Antes de las migraciones de Django se carga db/esquema_obras.sql y después los scripts
incrementales db/cu*.sql (con eso también se comprueba que sean idempotentes).

Uso (necesita un usuario de PostgreSQL con permiso CREATEDB):
    DB_NAME=obra_db DB_USER=... DB_HOST=... python manage.py test obras
"""
from pathlib import Path

from django.conf import settings
from django.db import connections
from django.db.models.signals import pre_migrate
from django.test.runner import DiscoverRunner

DIR_SQL = Path(settings.BASE_DIR) / "db"


def _cargar_esquema(sender, using="default", **kwargs):
    conexion = connections[using]
    with conexion.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.usuarios') IS NOT NULL")
        if cursor.fetchone()[0]:
            return
        for archivo in [DIR_SQL / "esquema_obras.sql", *sorted(DIR_SQL.glob("cu*.sql"))]:
            cursor.execute(archivo.read_text(encoding="utf-8"))


class RunnerPostgres(DiscoverRunner):
    def setup_databases(self, **kwargs):
        pre_migrate.connect(_cargar_esquema, dispatch_uid="obras_cargar_esquema")
        try:
            return super().setup_databases(**kwargs)
        finally:
            pre_migrate.disconnect(dispatch_uid="obras_cargar_esquema")
