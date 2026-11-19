-- =====================================================================
-- Ubicación de la obra en mapa: latitud y longitud (WGS84) para mostrarla con OpenStreetMap.
-- Script INCREMENTAL: no borra datos y se puede ejecutar más de una vez.
-- =====================================================================
ALTER TABLE obras ADD COLUMN IF NOT EXISTS latitud  NUMERIC(9,6);
ALTER TABLE obras ADD COLUMN IF NOT EXISTS longitud NUMERIC(9,6);

ALTER TABLE obras DROP CONSTRAINT IF EXISTS ck_obras_coordenadas;
ALTER TABLE obras ADD CONSTRAINT ck_obras_coordenadas CHECK (
  (latitud IS NULL AND longitud IS NULL)
  OR (latitud BETWEEN -90 AND 90 AND longitud BETWEEN -180 AND 180)) NOT VALID;
