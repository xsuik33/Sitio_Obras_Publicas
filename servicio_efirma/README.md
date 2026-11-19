# Servicio de e.firma del Sistema de Obras (mini proyecto)

Servicio web pequeño y separado del Sistema de Obras que funciona como su **autoridad certificadora**: le da a cada
usuario **su propia e.firma** y sella con ella las notas de bitácora.

**No es la e.firma del SAT** ni usa datos fiscales: el certificado solo lleva el **nombre y el correo** del usuario.
Así el sistema no maneja RFC, CURP ni los archivos `.cer`/`.key` personales de nadie.

## Cómo funciona
1. **Emitir** (`/emitir`): cuando el usuario crea su e.firma en «Mi e.firma», elige un PIN. El servicio genera un par
   de llaves **Ed25519** y un certificado X.509 firmado por la AC del sistema, y devuelve la llave privada
   **cifrada con el PIN** (PKCS#8). El sistema guarda el certificado y la llave cifrada; **el PIN no se guarda**.
2. **Firmar** (`/firmar`): al firmar una nota el usuario escribe su PIN. El servicio abre la llave con el PIN,
   comprueba que el certificado sea de la AC del sistema, esté vigente y corresponda a la llave, y sella la
   **cadena original** de la nota. Devuelve el sello en base64.
3. **Verificar** (`/verificar`): comprueba que un sello corresponda a la cadena y a un certificado de la AC.

La llave de la AC se deriva de `EFIRMA_SEMILLA`, un secreto que comparten el sistema y el servicio, así que no hay
archivos de llaves que guardar. **El servicio no guarda nada.**

## Endpoints (JSON)
| Método | Ruta | Cuerpo | Respuesta |
|---|---|---|---|
| GET | `/salud` | — | `{"estado": "ok"}` |
| POST | `/emitir` | `nombre`, `correo`, `pin` | `certificado`, `llave_cifrada`, `numero`, vigencia |
| POST | `/firmar` | `cadena`, `certificado`, `llave_cifrada`, `pin` | `sello`, `sha256` y datos del certificado (422 si el PIN es incorrecto) |
| POST | `/verificar` | `cadena`, `sello`, `certificado` | `{"valido": true \| false}` |

Todas, menos `/salud`, exigen `Authorization: Bearer <EFIRMA_TOKEN>`.

## Correr localmente
```bash
pip install -r servicio_efirma/requirements.txt
EFIRMA_TOKEN=secreto EFIRMA_SEMILLA=una-semilla-larga flask --app servicio_efirma.app run --port 5001
```
Pruebas (desde la raíz del repositorio): `python -m unittest servicio_efirma.test_servicio`

## Publicar en Render y vincularlo al sistema
1. New → Web Service → mismo repositorio, **Root Directory** `servicio_efirma`,
   Build `pip install -r requirements.txt`, Start `gunicorn app:app`, Health check `/salud`
   (o usa el Blueprint `servicio_efirma/render.yaml`).
2. Variables: `EFIRMA_TOKEN` y `EFIRMA_SEMILLA`, dos textos largos y aleatorios.
3. En el Sistema de Obras agrega `EFIRMA_URL=https://<servicio>.onrender.com` y los **mismos** `EFIRMA_TOKEN`
   y `EFIRMA_SEMILLA`.

Sin `EFIRMA_URL`, el sistema emite y firma con el mismo código dentro de su propio proceso ("modo local").
Si cambia `EFIRMA_SEMILLA`, las e.firmas emitidas antes dejan de ser válidas y cada usuario debe renovar la suya.
