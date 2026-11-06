-- =====================================================================
-- CU-03 (ajustes) · Reporte diario: alerta por atraso crítico y notas de bitácora vinculadas
-- Script INCREMENTAL: no borra datos y se puede ejecutar más de una vez.
-- Ejecútalo en el SQL Editor de Supabase (y en tu base local).
-- =====================================================================

-- 1. Atraso crítico (flujo 4a): causa documentada por el supervisor y lo que solicita.
ALTER TABLE reportes_diarios ADD COLUMN IF NOT EXISTS atraso_causa TEXT;
ALTER TABLE reportes_diarios ADD COLUMN IF NOT EXISTS atraso_solicitud VARCHAR(20);
ALTER TABLE reportes_diarios DROP CONSTRAINT IF EXISTS ck_reporte_atraso_solicitud;
ALTER TABLE reportes_diarios ADD CONSTRAINT ck_reporte_atraso_solicitud
  CHECK (atraso_solicitud IS NULL OR atraso_solicitud IN ('Reprogramación', 'Retención', 'Ninguna'));

-- 2. Notas de bitácora vinculadas al reporte diario (paso 3).
CREATE TABLE IF NOT EXISTS reportes_diarios_notas (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reporte_diario_id  UUID NOT NULL REFERENCES reportes_diarios(id) ON DELETE CASCADE,
  nota_bitacora_id   UUID NOT NULL REFERENCES notas_bitacora(id) ON DELETE CASCADE,
  CONSTRAINT uq_reporte_nota UNIQUE (reporte_diario_id, nota_bitacora_id)
);
CREATE INDEX IF NOT EXISTS ix_rd_notas_nota ON reportes_diarios_notas (nota_bitacora_id);

-- 3. Seguridad en Supabase (solo si existe el rol de la aplicación)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'obras_app') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON reportes_diarios_notas TO obras_app';
    EXECUTE 'ALTER TABLE reportes_diarios_notas ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS app_total ON reportes_diarios_notas';
    EXECUTE 'CREATE POLICY app_total ON reportes_diarios_notas FOR ALL TO obras_app USING (true) WITH CHECK (true)';
  END IF;
END $$;
