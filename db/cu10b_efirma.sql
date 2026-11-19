-- =====================================================================
-- CU-10 (cambio) · Firma de notas de bitácora con e.firma.
-- Cada firma guarda la cadena original que se firmó, el sello (RSA-SHA256 en base64) y el certificado
-- público del firmante. La llave privada y la contraseña NUNCA se guardan.
-- Script INCREMENTAL: no borra datos y se puede ejecutar más de una vez.
-- =====================================================================
ALTER TABLE firmas_notas_bitacora ADD COLUMN IF NOT EXISTS cadena_original    TEXT;
ALTER TABLE firmas_notas_bitacora ADD COLUMN IF NOT EXISTS sello              TEXT;
ALTER TABLE firmas_notas_bitacora ADD COLUMN IF NOT EXISTS certificado        TEXT;
ALTER TABLE firmas_notas_bitacora ADD COLUMN IF NOT EXISTS certificado_numero VARCHAR(40);
ALTER TABLE firmas_notas_bitacora ADD COLUMN IF NOT EXISTS firmante_rfc       VARCHAR(13);
ALTER TABLE firmas_notas_bitacora ADD COLUMN IF NOT EXISTS firmante_nombre    VARCHAR(255);
