# Sistema de Obras

Aplicación web (Django + PostgreSQL) para la gestión de obras: contratos, estimaciones, reportes diarios, bitácora y documentos, con roles por obra.

- **Código del equipo:** rama **`django-obras`** de `crishdzrios06/portal_obras`.
  La rama `main` de ese repositorio es otro proyecto: **no la toques**.
- **Base de datos:** PostgreSQL. El esquema lo define `db/esquema_obras.sql`; Django solo lo usa (modelos `managed = False`).
- **Producción:** Render (aplicación) + Supabase (base de datos).

---

## 1. Requisitos

| Herramienta | Linux / macOS | Windows |
|---|---|---|
| Git | `sudo apt install git` · `sudo pacman -S git` · `brew install git` | https://git-scm.com/download/win |
| GitHub CLI (`gh`) | `sudo apt install gh` · `sudo pacman -S github-cli` · `brew install gh` | `winget install GitHub.cli` |
| Python 3.10+ | `sudo apt install python3 python3-venv` · `sudo pacman -S python` | https://www.python.org/downloads/ (marca **Add python.exe to PATH**) |
| PostgreSQL 13+ | `sudo apt install postgresql` · `sudo pacman -S postgresql` · `brew install postgresql` | https://www.postgresql.org/download/windows/ (anota la contraseña del usuario `postgres`) |

**Windows:** agrega a tu `PATH` la carpeta `bin` de PostgreSQL (por ejemplo `C:\Program Files\PostgreSQL\16\bin`) para usar `psql` y `createdb`. Abre una terminal nueva después de instalar. Usa **PowerShell** en todos los pasos.

**Linux:** si PostgreSQL es nuevo, inicia el servicio (`sudo systemctl enable --now postgresql`) y crea tu usuario:
`sudo -u postgres createuser -s $USER`. En Arch, antes hay que inicializar el clúster (`sudo -iu postgres initdb -D /var/lib/postgres/data`).

---

## 2. Acceso al repositorio
1. Acepta la invitación de colaborador: https://github.com/crishdzrios06/portal_obras/invitations
   (el dueño debe invitarte con permiso de escritura).
2. Inicia sesión una sola vez:
   ```
   gh auth login
   ```
   Elige **GitHub.com → HTTPS → Login with a web browser**.

---

## 3. Clonar el proyecto
Igual en Linux y Windows:
```
git clone -b django-obras https://github.com/crishdzrios06/portal_obras.git obras_site
cd obras_site
```
`-b django-obras` es obligatorio: sin él se descarga `main`, que no es este proyecto.

---

## 4. Entorno virtual y dependencias

**Linux / macOS**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Windows (PowerShell)**
```powershell
py -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Si PowerShell bloquea la activación, ejecuta una vez
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` y vuelve a intentar.
Sabrás que el entorno está activo porque el prompt empieza con `(venv)`.

---

## 5. Base de datos local
Cada persona trabaja con **su propia base local**, para no tocar los datos reales de Supabase.

**Linux / macOS**
```bash
createdb obra_db
psql -d obra_db -v ON_ERROR_STOP=1 -f db/esquema_obras.sql
```

**Windows (PowerShell)**
```powershell
createdb -U postgres obra_db
psql -U postgres -d obra_db -v ON_ERROR_STOP=1 -f db/esquema_obras.sql
```
(pedirá la contraseña de `postgres` que elegiste al instalar PostgreSQL.)

Para empezar de cero: `dropdb obra_db` (en Windows, `dropdb -U postgres obra_db`) y repetir los dos comandos.

---

## 6. Variables de entorno
La aplicación lee su configuración de variables de entorno. Crea un archivo **que nunca se sube** al repositorio
(`dev.sh` y `dev.ps1` están en `.gitignore`).

**Linux / macOS** — crea `dev.sh`:
```bash
cat > dev.sh <<'EOF'
export DEBUG=True SECRET_KEY=temporal
export DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=obra_db DB_USER=$USER DB_SSL=prefer
export ADMIN_EMAILS=tu_correo@ejemplo.com
EOF
```
Si tu PostgreSQL pide contraseña, agrega `export DB_PASS=tu_contraseña`.

**Windows (PowerShell)** — crea `dev.ps1` (con el Bloc de notas o VS Code):
```powershell
$env:DEBUG = "True"
$env:SECRET_KEY = "temporal"
$env:DB_HOST = "127.0.0.1"
$env:DB_PORT = "5432"
$env:DB_NAME = "obra_db"
$env:DB_USER = "postgres"
$env:DB_PASS = "TU_CONTRASEÑA_DE_POSTGRES"
$env:DB_SSL = "prefer"
$env:ADMIN_EMAILS = "tu_correo@ejemplo.com"
```

`ADMIN_EMAILS` es el correo (o varios, separados por coma) con acceso al panel `/admin/`, donde se crean los usuarios.

---

## 7. Primer arranque

**Linux / macOS**
```bash
source venv/bin/activate && source dev.sh
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

**Windows (PowerShell)**
```powershell
venv\Scripts\Activate.ps1
. .\dev.ps1
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```
`createsuperuser` pide un correo (usa el de `ADMIN_EMAILS`), nombre, apellidos y contraseña.
Abre http://127.0.0.1:8000/login/ (aplicación) y http://127.0.0.1:8000/admin/ (panel).

**Cada día**, antes de trabajar, activa el entorno y las variables:
- Linux / macOS: `source venv/bin/activate && source dev.sh`
- Windows: `venv\Scripts\Activate.ps1` y luego `. .\dev.ps1`

Para detener el servidor: `Ctrl + C`.

---

## 8. Subir tus cambios (siempre por rama y pull request)
**No hagas push directo a `django-obras`.** Cada cambio va en tu propia rama. Estos comandos son iguales en Linux y Windows:

```
git switch django-obras
git pull
git switch -c feature/nombre-corto
```
Trabaja y prueba en local. Cuando termines:
```
git add .
git status
git commit -m "Describe el cambio"
git push -u origin feature/nombre-corto
gh pr create --base django-obras --fill
```
- Antes del commit, revisa `git status`: **no deben aparecer** `venv/`, `dev.sh`, `dev.ps1`, `.env` ni archivos con contraseñas.
- `gh pr create` imprime el enlace del pull request. Otra persona del equipo lo revisa y lo acepta (**Merge**).

Después de que acepten tu pull request:
```
git switch django-obras
git pull
git branch -d feature/nombre-corto
```

---

## 9. Reglas del equipo
1. **Nada de secretos en el repositorio:** contraseñas, claves de API, `dev.sh`, `dev.ps1`, `.env`. Si subes uno por error, avisa para regenerarlo.
2. **Cambios en la base de datos** (tablas o columnas): se hacen en `db/esquema_obras.sql` **y** en `obras/models.py`, y se avisa a Saul, que los aplica en Supabase. Los demás aplican el SQL en su base local.
3. **No uses `git push --force`** en ramas compartidas.
4. Antes de abrir un pull request, ejecuta `python manage.py check` (sin errores) y prueba la pantalla que tocaste.
5. Un pull request por cambio, con un título claro.

---

## 10. Roles y permisos
Cada persona tiene uno o más roles **por obra**. Los permisos se definen en `obras/permisos.py` (`PERMISOS_POR_ROL`).

| Rol | Puede |
|---|---|
| Administrador | Todo en su obra: asigna roles, corrige estatus, crea reportes |
| Residente | Ver, decidir el estatus de reportes diarios y estimaciones, bitácora, documentos |
| Supervisor | Ver, crear y corregir reportes diarios, bitácora, documentos |
| Superintendente Contratista | Ver, crear estimaciones, decidir el estatus de reportes diarios, bitácora, documentos |
| Directivo | Solo consultar |

- Residente y Superintendente deciden el estatus de un reporte diario **una sola vez**; si hay un error, solo un administrador lo corrige.
- Los correos de `ADMIN_EMAILS` son administradores de todas las obras y del panel `/admin/`.

---

## 11. Despliegue (para quien mantiene Supabase y Render)

**Repositorios:** el sitio público se despliega desde `xsuik33/sistema-obras` (rama `main`). El equipo trabaja en la rama `django-obras` de `portal_obras`.
Cuando se acepte un pull request, sincroniza y publica:
```
git switch main
git pull portal django-obras
git push origin main
```
Render detecta el commit y redespliega solo. Antes de trabajar cada día: `git pull portal django-obras`.

**Supabase:**
1. Ejecuta el esquema y crea el rol de la aplicación `obras_app` (con permisos mínimos, RLS activada y la Data API apagada).
2. Las tablas propias de Django se crean una sola vez desde la máquina del mantenedor, con el usuario administrador de la base:
   ```
   python manage.py migrate
   ```
   Render **no** ejecuta migraciones ni SQL.

**Render** (Web Service, Python):
- Build: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
- Start: `gunicorn obras_site.wsgi:application`
- Health check: `/salud/`
- Variables: `SECRET_KEY`, `DEBUG=False`, `ADMIN_EMAILS`, `DB_HOST`, `DB_PORT=5432`, `DB_NAME=postgres`,
  `DB_USER=obras_app.<PROJECT_REF>`, `DB_PASS`, `DB_SSL=require`
- Si el build falla por la versión de Python, agrega la variable `PYTHON_VERSION` (por ejemplo `3.12.8`).

---

## 12. Notas del modelo
- Las columnas que calcula la base de datos (`monto_anticipo`, `importe`, `subtotal`, `iva`, `monto_neto`, etc.) no se escriben desde Django.
  Tras crear o cambiar renglones de una estimación usa `estimacion.refresh_from_db()`.
- `UsuarioProyectoRol` usa llave primaria compuesta (Django 5.2+) y no aparece en el panel admin; los roles se asignan en `/obras/<id>/roles/`.
- Para cambiar el esquema: edita primero `db/esquema_obras.sql` y luego `obras/models.py`.
