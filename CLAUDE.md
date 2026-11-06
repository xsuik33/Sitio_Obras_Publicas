# Sistema de Obras (Django + PostgreSQL)
- Responde en español, conciso. Corrige errores de forma autónoma.
- Modelos con `managed = False`: el esquema lo define `db/esquema_obras.sql`. Cambios de BD: primero SQL, luego `obras/models.py`. Los cambios para una base con datos van en un script incremental en `db/` (idempotente, sin borrar datos).
- Permisos por rol y por obra en `obras/permisos.py`. Reglas del reporte diario en `obras/reportes.py`; del mensual en `obras/mensuales.py`; de la bitácora en `obras/bitacora.py`; de evidencias en `obras/evidencias.py` (archivos en Supabase Storage, `obras/almacenamiento.py`).
- Remotes: origin = xsuik33/sistema-obras (Render, main); portal = crishdzrios06/portal_obras (rama django-obras); nuevo = xsuik33/Sitio_Obras_Publicas (main). Subir con `git pushall` (solo origin y nuevo; a portal no se sube).
- Nunca poner secretos (contraseñas, claves de API, .env, dev.sh) en el repo ni en el chat.
- Verificar con `python manage.py check` y `python manage.py test obras` antes de entregar (las pruebas crean una base de prueba en PostgreSQL y cargan `db/esquema_obras.sql` + `db/cu*.sql`; ver `obras/pruebas_bd.py`).
- Casos de uso hechos: CU-13, CU-01, CU-03, CU-09, CU-04, evidencias en Supabase Storage. Pendiente: CU-12 (consulta del expediente; el Directivo solo consulta).
