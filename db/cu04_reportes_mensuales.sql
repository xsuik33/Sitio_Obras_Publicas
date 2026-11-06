-- =====================================================================
-- CU-04 · Reporte mensual: resumen, estatus con validación doble y cifras congeladas al aprobar
-- Script INCREMENTAL: no borra datos y se puede ejecutar más de una vez.
-- Ejecútalo en el SQL Editor de Supabase (y en tu base local).
-- =====================================================================

-- 1. Columnas nuevas del reporte mensual
ALTER TABLE reportes_mensuales ADD COLUMN IF NOT EXISTS resumen TEXT;
ALTER TABLE reportes_mensuales
  ADD COLUMN IF NOT EXISTS estatus_id SMALLINT NOT NULL DEFAULT 1 REFERENCES catalogo_estatus_validacion(id);
-- Consolidado del mes (diarios, avance, estimaciones, bitácora) guardado al aprobarse; NULL = se calcula al vuelo.
ALTER TABLE reportes_mensuales ADD COLUMN IF NOT EXISTS consolidado JSONB;
ALTER TABLE reportes_mensuales ADD COLUMN IF NOT EXISTS fecha_modificacion TIMESTAMPTZ;

-- La fecha de corte cae dentro del mes reportado.
ALTER TABLE reportes_mensuales DROP CONSTRAINT IF EXISTS ck_reporte_mensual_corte;
ALTER TABLE reportes_mensuales ADD CONSTRAINT ck_reporte_mensual_corte CHECK (
  fecha_corte >= make_date(anio, mes, 1)
  AND fecha_corte < make_date(anio, mes, 1) + INTERVAL '1 month') NOT VALID;

CREATE INDEX IF NOT EXISTS ix_reportes_mens_estatus ON reportes_mensuales (estatus_id);

-- 2. Validaciones del reporte mensual (Residente y Superintendente deciden por separado)
CREATE TABLE IF NOT EXISTS validaciones_reportes_mensuales (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reporte_mensual_id    UUID NOT NULL REFERENCES reportes_mensuales(id) ON DELETE CASCADE,
  usuario_validador_id  UUID NOT NULL REFERENCES usuarios(id),
  rol_validador         VARCHAR(40) NOT NULL
                          CHECK (rol_validador IN ('Residente', 'Superintendente Contratista')),
  estatus_id            SMALLINT NOT NULL REFERENCES catalogo_estatus_validacion(id),
  fecha_validacion      TIMESTAMPTZ NOT NULL DEFAULT now(),
  comentarios           TEXT
);
CREATE INDEX IF NOT EXISTS ix_val_rm_reporte   ON validaciones_reportes_mensuales (reporte_mensual_id);
CREATE INDEX IF NOT EXISTS ix_val_rm_validador ON validaciones_reportes_mensuales (usuario_validador_id);
CREATE INDEX IF NOT EXISTS ix_val_rm_estatus   ON validaciones_reportes_mensuales (estatus_id);

-- 3. Seguridad en Supabase (solo si existe el rol de la aplicación)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'obras_app') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON validaciones_reportes_mensuales TO obras_app';
    EXECUTE 'ALTER TABLE validaciones_reportes_mensuales ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS app_total ON validaciones_reportes_mensuales';
    EXECUTE 'CREATE POLICY app_total ON validaciones_reportes_mensuales FOR ALL TO obras_app USING (true) WITH CHECK (true)';
  END IF;
END $$;
