# Sistema de Obras (Django + PostgreSQL)

Django con modelos `managed = False`: **el esquema lo crea la BD** (`db/esquema_obras.sql`), no las migraciones de Django.

## 1. Dónde trabajar
En tu Arch, en una carpeta **local** (por ejemplo `~/proyectos/obras_site`), no dentro de `/mnt/Tunel/...`:
el entorno virtual es lento y a veces falla en carpetas montadas por red.

## 2. Preparar el entorno local
```bash
mkdir -p ~/proyectos && cd ~/proyectos
unzip ~/Downloads/obras_site.zip && cd obras_site
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # edita DB_USER, ADMIN_EMAILS, SECRET_KEY
set -a; source .env; set +a   # carga las variables en esta terminal
```

## 3. Base de datos local (recrearla con el esquema final)
```bash
dropdb obra_db; createdb obra_db
psql -d obra_db -v ON_ERROR_STOP=1 -f db/esquema_obras.sql
python manage.py migrate        # solo crea las tablas propias de Django (auth_*, django_*)
python manage.py createsuperuser   # correo = uno de ADMIN_EMAILS
python manage.py runserver
```
Abre http://127.0.0.1:8000/ y http://127.0.0.1:8000/admin/

## 4. Subir a GitHub
```bash
git init -b main && git add . && git commit -m "Proyecto inicial"
git remote add origin git@github.com:TU_USUARIO/sistema-obras.git
git push -u origin main
```

## 5. Supabase
1. Ejecuta `db/esquema_obras.sql` en el SQL Editor. Si pregunta por RLS, elige "Run without RLS".
2. Crea el rol `obras_app` y desactiva la Data API (ver la conversación).
3. **Migraciones de Django contra Supabase** (una sola vez, desde tu máquina, con el usuario administrador):
```bash
DB_HOST=aws-0-REGION.pooler.supabase.com DB_NAME=postgres DB_SSL=require \
DB_USER=postgres.PROJECT_REF DB_PASS='tu_password_postgres' python manage.py migrate
```
Después de eso, la app usa solo `obras_app` (sin permiso para crear tablas).
4. **Evidencias (Storage)**: Storage → New bucket → nombre `evidencias`, **privado** (sin "Public bucket").
   Copia la **Secret key** `sb_secret_…` (Project Settings → API Keys) solo a las variables de Render; nunca al repo.
   Los archivos no son públicos: la app revisa permisos y redirige a una URL firmada que dura 5 minutos.

## 6. Render
New + → Web Service → conecta el repositorio (o usa el Blueprint `render.yaml`).
- Build: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
- Start: `gunicorn obras_site.wsgi:application`
- Variables: `SECRET_KEY`, `DEBUG=False`, `ADMIN_EMAILS`, `DB_HOST`, `DB_PORT=5432`, `DB_NAME=postgres`,
  `DB_USER=obras_app.PROJECT_REF`, `DB_PASS`, `DB_SSL=require`
- Evidencias: `SUPABASE_URL=https://PROJECT_REF.supabase.co`, `SUPABASE_SERVICE_KEY` (Secret key `sb_secret_…`),
  `SUPABASE_BUCKET=evidencias`. Sin ellas se guardan en el disco de Render, que se borra en cada despliegue.
- Salud: `/salud/`
Si el build falla por la versión de Python, agrega la variable `PYTHON_VERSION` (por ejemplo `3.12.8`).

## Notas del modelo
- Las columnas calculadas por la BD (`monto_anticipo`, `importe`, `subtotal`, `iva`, `monto_neto`, etc.) no se escriben desde Django.
  Tras crear o cambiar renglones de una estimación usa `estimacion.refresh_from_db()`.
- `UsuarioProyectoRol` usa llave primaria compuesta (Django 5.2+) y no aparece en el panel admin.
- Para cambiar el esquema: edita primero `db/esquema_obras.sql` y luego `obras/models.py`.
