-- CU-13 (cambio): cada persona tiene a lo más UN rol en cada obra y ya no existe el rol
-- "Administrador" dentro de las obras (el administrador es el del sistema, ADMIN_EMAILS).
-- Idempotente: se puede correr varias veces.

-- 1. Las asignaciones del rol Administrador por obra ya no dan permisos; se retiran.
DELETE FROM usuarios_proyectos_roles
 WHERE rol_id IN (SELECT id FROM roles WHERE nombre = 'Administrador');

-- 2. Índice único usuario + obra. Si alguien tiene dos roles en la misma obra, no se borra nada:
--    se avisa para que el administrador le deje uno solo y se vuelva a correr el script.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM usuarios_proyectos_roles GROUP BY usuario_id, obra_id HAVING count(*) > 1) THEN
    RAISE NOTICE 'Hay personas con más de un rol en la misma obra; deja uno solo y vuelve a correr este script.';
  ELSE
    CREATE UNIQUE INDEX IF NOT EXISTS ux_un_rol_por_obra ON usuarios_proyectos_roles (usuario_id, obra_id);
  END IF;
END $$;
