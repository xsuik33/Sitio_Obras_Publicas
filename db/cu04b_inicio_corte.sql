-- =====================================================================
-- CU-04 (cambio) · Fecha de inicio de corte del reporte mensual.
-- El periodo del reporte va de fecha_inicio_corte a fecha_corte (vacía = primer día del mes).
-- Script INCREMENTAL: no borra datos y se puede ejecutar más de una vez.
-- =====================================================================
ALTER TABLE reportes_mensuales ADD COLUMN IF NOT EXISTS fecha_inicio_corte DATE;

ALTER TABLE reportes_mensuales DROP CONSTRAINT IF EXISTS ck_mensual_inicio_corte;
ALTER TABLE reportes_mensuales ADD CONSTRAINT ck_mensual_inicio_corte CHECK (
  fecha_inicio_corte IS NULL OR (
    fecha_inicio_corte >= make_date(anio, mes, 1)
    AND fecha_inicio_corte <= fecha_corte)) NOT VALID;
