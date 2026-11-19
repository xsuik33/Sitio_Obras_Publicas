# Servicio de e.firma (mini proyecto)

Servicio web pequeño y separado del Sistema de Obras que **sella notas de bitácora con la e.firma** del firmante.
El Sistema de Obras le manda la *cadena original* de la nota junto con el `.cer`, el `.key` y la contraseña que
el usuario sube en la página de firma; el servicio comprueba la e.firma y devuelve el **sello digital**.

## Qué hace
1. Lee el certificado (`.cer`, X.509 en DER o PEM) y la llave privada (`.key`, PKCS#8 cifrada con la contraseña),
   el mismo formato de la e.firma del SAT.
2. Comprueba que la llave corresponda al certificado y que el certificado esté vigente.
3. Firma la cadena original con **RSA + SHA-256** y devuelve el sello en base64, el SHA-256 de la cadena,
   el certificado público y sus datos (número de certificado, nombre, RFC, CURP y vigencia).
4. La llave privada y la contraseña **solo viven en memoria** durante la petición: no se guardan ni se registran.

## Endpoints
| Método | Ruta | Cuerpo | Respuesta |
|---|---|---|---|
| GET | `/salud` | — | `{"estado": "ok"}` |
| POST | `/firmar` | multipart: `cadena`, `cer`, `key`, `contrasena` | sello y datos del certificado (422 si la e.firma no es válida) |
| POST | `/verificar` | JSON: `cadena`, `sello`, `certificado` | `{"valido": true \| false}` |

`/firmar` y `/verificar` exigen el encabezado `Authorization: Bearer <EFIRMA_TOKEN>`.

## Correr localmente
```bash
pip install -r servicio_efirma/requirements.txt
EFIRMA_TOKEN=secreto flask --app servicio_efirma.app run --port 5001
```
Pruebas (desde la raíz del repositorio): `python -m unittest servicio_efirma.test_servicio`

## Publicar en Render y vincularlo al sistema
1. New → Web Service → mismo repositorio, **Root Directory** `servicio_efirma`,
   Build `pip install -r requirements.txt`, Start `gunicorn app:app`, Health check `/salud`
   (o usa el Blueprint `servicio_efirma/render.yaml`).
2. Variable `EFIRMA_TOKEN`: un texto largo y aleatorio.
3. En el servicio del Sistema de Obras agrega `EFIRMA_URL=https://<servicio>.onrender.com` y el mismo `EFIRMA_TOKEN`.

Si el sistema no tiene `EFIRMA_URL`, firma con el mismo código dentro de su propio proceso ("modo local").

## Límites (es un proyecto escolar)
No valida la cadena de confianza contra los certificados raíz del SAT ni consulta si el certificado fue revocado
(OCSP). En producción habría que agregar ambas comprobaciones y un sello de tiempo.
