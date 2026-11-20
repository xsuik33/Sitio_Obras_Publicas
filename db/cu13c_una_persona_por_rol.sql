-- =====================================================================
-- CU-13 (cambio) · Cada rol lo tiene UNA sola persona en cada obra (un Residente, un Supervisor, etc.).
-- Se suma a cu13b (una persona tiene un solo rol por obra).
-- Idempotente. No borra nada: si una obra tiene dos personas con el mismo rol, solo avisa para que el
-- administrador le quite el rol a una de ellas en «Gestionar equipo» y vuelva a correr el script.
-- =====================================================================
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM usuarios_proyectos_roles GROUP BY obra_id, rol_id HAVING count(*) > 1) THEN
    RAISE NOTICE 'Hay obras con dos personas en el mismo rol; deja una sola y vuelve a correr este script.';
  ELSE
    CREATE UNIQUE INDEX IF NOT EXISTS ux_una_persona_por_rol ON usuarios_proyectos_roles (obra_id, rol_id);
  END IF;
END $$;
