-- =====================================================================
-- CU-03 · Reportes diarios: validación doble, Borrador y programa de obra
-- Script INCREMENTAL: no borra datos y se puede ejecutar más de una vez.
-- Ejecútalo en el SQL Editor de Supabase (y en tu base local).
-- =====================================================================

-- 1. Nuevo estatus "Borrador" (reporte guardado, aún no enviado a revisión)
INSERT INTO catalogo_estatus_validacion (id, nombre, descripcion)
VALUES (5, 'Borrador', 'Guardado por su autor, aún no enviado a revisión')
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('catalogo_estatus_validacion', 'id'),
              (SELECT max(id) FROM catalogo_estatus_validacion));

-- 2. Cada validación del reporte diario registra EN NOMBRE DE QUÉ ROL se decidió
--    (el Superintendente y el Residente validan por separado).
ALTER TABLE validaciones_reportes_diarios
  ADD COLUMN IF NOT EXISTS rol_validador VARCHAR(40);
ALTER TABLE validaciones_reportes_diarios
  DROP CONSTRAINT IF EXISTS ck_validacion_rol;
ALTER TABLE validaciones_reportes_diarios
  ADD CONSTRAINT ck_validacion_rol
  CHECK (rol_validador IS NULL OR rol_validador IN ('Residente', 'Superintendente Contratista'));

-- 3. Programa de obra: avance físico programado ACUMULADO (%) en fechas clave.
--    Entre dos fechas el sistema interpola linealmente.
CREATE TABLE IF NOT EXISTS programa_obra (
  id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  obra_id                       UUID NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
  fecha                         DATE NOT NULL,
  avance_programado_porcentaje  NUMERIC(5,2) NOT NULL
                                  CHECK (avance_programado_porcentaje BETWEEN 0 AND 100),
  fecha_registro                TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_programa_obra_fecha UNIQUE (obra_id, fecha)
);

-- 4. Seguridad en Supabase (solo si existe el rol de la aplicación)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'obras_app') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON programa_obra TO obras_app';
    EXECUTE 'ALTER TABLE programa_obra ENABLE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS app_total ON programa_obra';
    EXECUTE 'CREATE POLICY app_total ON programa_obra FOR ALL TO obras_app USING (true) WITH CHECK (true)';
  END IF;
END $$;
