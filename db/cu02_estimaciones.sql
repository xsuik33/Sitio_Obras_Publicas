-- =====================================================================
-- CU-02, CU-05, CU-08, CU-16, CU-17 · Estimaciones con validación Supervisor → Residente
-- Basado en la LOPSRM (arts. 46 Bis, 54) y su Reglamento (arts. 88, 118, 132, 143).
-- Script INCREMENTAL: no borra datos y se puede ejecutar más de una vez.
-- Ejecútalo en el SQL Editor de Supabase (y en tu base local).
-- =====================================================================

-- 1. Estatus «Con observaciones»: el Supervisor regresa la estimación para corregir sin rechazarla.
INSERT INTO catalogo_estatus_validacion (id, nombre, descripcion)
VALUES (6, 'Con observaciones', 'Regresado a su autor para corregir, sin rechazarlo')
ON CONFLICT (id) DO NOTHING;
SELECT setval(pg_get_serial_sequence('catalogo_estatus_validacion', 'id'), (SELECT max(id) FROM catalogo_estatus_validacion));

-- 2. Contrato: retención por atraso y garantías.
ALTER TABLE contratos ADD COLUMN IF NOT EXISTS porcentaje_retencion_atraso NUMERIC(5,2) NOT NULL DEFAULT 5;
ALTER TABLE contratos DROP CONSTRAINT IF EXISTS ck_contratos_ret_atraso;
ALTER TABLE contratos ADD CONSTRAINT ck_contratos_ret_atraso CHECK (porcentaje_retencion_atraso BETWEEN 0 AND 100);
ALTER TABLE contratos ADD COLUMN IF NOT EXISTS monto_garantia_cumplimiento NUMERIC(15,2);
ALTER TABLE contratos ADD COLUMN IF NOT EXISTS numero_fianza VARCHAR(100);
ALTER TABLE contratos ADD COLUMN IF NOT EXISTS afianzadora VARCHAR(255);

-- 3. Estimación: folio por contrato, retención por atraso, justificación y fechas de envío.
ALTER TABLE estimaciones DROP CONSTRAINT IF EXISTS estimaciones_folio_key;
ALTER TABLE estimaciones DROP CONSTRAINT IF EXISTS uq_estimaciones_contrato_folio;
ALTER TABLE estimaciones ADD CONSTRAINT uq_estimaciones_contrato_folio UNIQUE (contrato_id, folio);
ALTER TABLE estimaciones ADD COLUMN IF NOT EXISTS retencion_atraso NUMERIC(15,2) NOT NULL DEFAULT 0;
ALTER TABLE estimaciones ADD COLUMN IF NOT EXISTS devolucion_retencion NUMERIC(15,2) NOT NULL DEFAULT 0;
ALTER TABLE estimaciones ADD COLUMN IF NOT EXISTS dias_atraso INTEGER;
ALTER TABLE estimaciones ADD COLUMN IF NOT EXISTS atraso_no_imputable BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE estimaciones ADD COLUMN IF NOT EXISTS justificacion_atraso TEXT;
ALTER TABLE estimaciones ADD COLUMN IF NOT EXISTS fecha_envio TIMESTAMPTZ;
ALTER TABLE estimaciones ADD COLUMN IF NOT EXISTS fecha_modificacion TIMESTAMPTZ;
ALTER TABLE estimaciones DROP CONSTRAINT IF EXISTS ck_estimaciones_retenciones;
ALTER TABLE estimaciones ADD CONSTRAINT ck_estimaciones_retenciones CHECK (retencion_atraso >= 0 AND devolucion_retencion >= 0);

-- Monto neto: importe - amortización + IVA - retención por derechos - retención por atraso + devolución.
CREATE OR REPLACE FUNCTION fn_calcular_totales_estimacion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_iva NUMERIC(5,2);
  v_ret NUMERIC(5,2);
BEGIN
  SELECT porcentaje_iva, porcentaje_retencion INTO v_iva, v_ret
    FROM contratos WHERE id = NEW.contrato_id;
  NEW.retencion  := ROUND(NEW.subtotal * v_ret / 100, 2);
  NEW.iva        := ROUND((NEW.subtotal - NEW.amortizacion_anticipo) * v_iva / 100, 2);
  NEW.monto_neto := NEW.subtotal - NEW.amortizacion_anticipo + NEW.iva - NEW.retencion
                    - NEW.retencion_atraso + NEW.devolucion_retencion;
  RETURN NEW;
END $$;

-- 4. Renglón excedente: nota de bitácora firmada que lo autorizó (Reglamento art. 118).
ALTER TABLE estimaciones_detalles ADD COLUMN IF NOT EXISTS nota_bitacora_id UUID REFERENCES notas_bitacora(id);

-- 5. Validaciones: qué rol decidió (Supervisor primero, Residente después).
ALTER TABLE validaciones_estimaciones ADD COLUMN IF NOT EXISTS rol_validador VARCHAR(40);
ALTER TABLE validaciones_estimaciones DROP CONSTRAINT IF EXISTS ck_val_est_rol;
ALTER TABLE validaciones_estimaciones ADD CONSTRAINT ck_val_est_rol
  CHECK (rol_validador IS NULL OR rol_validador IN ('Supervisor', 'Residente'));
CREATE INDEX IF NOT EXISTS ix_val_est_estimacion ON validaciones_estimaciones (estimacion_id);

-- 6. Documentos: categoría y, si es soporte de una estimación, a cuál pertenece (Reglamento art. 132).
ALTER TABLE documentos ADD COLUMN IF NOT EXISTS categoria VARCHAR(40) NOT NULL DEFAULT 'Otro';
ALTER TABLE documentos ADD COLUMN IF NOT EXISTS estimacion_id UUID REFERENCES estimaciones(id) ON DELETE CASCADE;
ALTER TABLE documentos DROP CONSTRAINT IF EXISTS ck_documentos_categoria;
ALTER TABLE documentos ADD CONSTRAINT ck_documentos_categoria CHECK (categoria IN (
  'Contrato', 'Reporte', 'Documento técnico', 'Plano', 'Fotografía', 'Factura', 'Números generadores',
  'Croquis', 'Prueba de laboratorio', 'Otro'));
CREATE INDEX IF NOT EXISTS ix_documentos_estimacion ON documentos (estimacion_id) WHERE estimacion_id IS NOT NULL;
