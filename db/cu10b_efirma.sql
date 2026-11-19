-- =====================================================================
-- CU-10 (cambio) · Firma de notas de bitácora con la e.firma del sistema (no la del SAT).
-- 1) efirmas_usuarios: la e.firma de cada usuario: su certificado (solo nombre y correo) y su llave privada
--    CIFRADA con el PIN que eligió. El PIN no se guarda en ningún lado.
-- 2) firmas_notas_bitacora: cada firma guarda la cadena original que se selló, el sello y el certificado.
-- Script INCREMENTAL: no borra datos y se puede ejecutar más de una vez.
-- =====================================================================
CREATE TABLE IF NOT EXISTS efirmas_usuarios (
  usuario_id     UUID PRIMARY KEY REFERENCES usuarios(id) ON DELETE CASCADE,
  certificado    TEXT NOT NULL,
  llave_cifrada  TEXT NOT NULL,
  numero         VARCHAR(32) NOT NULL,
  valido_hasta   TIMESTAMPTZ NOT NULL,
  fecha_emision  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE firmas_notas_bitacora ADD COLUMN IF NOT EXISTS cadena_original    TEXT;
ALTER TABLE firmas_notas_bitacora ADD COLUMN IF NOT EXISTS sello              TEXT;
ALTER TABLE firmas_notas_bitacora ADD COLUMN IF NOT EXISTS certificado        TEXT;
ALTER TABLE firmas_notas_bitacora ADD COLUMN IF NOT EXISTS certificado_numero VARCHAR(40);
ALTER TABLE firmas_notas_bitacora ADD COLUMN IF NOT EXISTS firmante_nombre    VARCHAR(255);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'obras_app') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON efirmas_usuarios TO obras_app';
    EXECUTE 'ALTER TABLE efirmas_usuarios ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS app_total ON efirmas_usuarios';
    EXECUTE 'CREATE POLICY app_total ON efirmas_usuarios FOR ALL TO obras_app USING (true) WITH CHECK (true)';
  END IF;
END $$;
