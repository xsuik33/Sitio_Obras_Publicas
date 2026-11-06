-- =====================================================================
-- Sistema de gestión de obras · Esquema PostgreSQL v2
-- Requiere PostgreSQL 13+ (gen_random_uuid nativo) y la extensión citext
-- Uso: psql -d mi_db -f esquema_obras.sql
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS citext;

-- ---------------------------------------------------------------------
-- 1. OBRAS Y CONTRATO
-- ---------------------------------------------------------------------

CREATE TABLE obras (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre                VARCHAR(255) NOT NULL,
  ubicacion             TEXT,
  fecha_inicio_oficial  DATE,
  fecha_fin_oficial     DATE,
  ultimo_folio_bitacora INTEGER NOT NULL DEFAULT 0,  -- contador de folios de la bitácora (lo usa un trigger)
  fecha_registro        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_obras_fechas CHECK (
    fecha_inicio_oficial IS NULL OR fecha_fin_oficial IS NULL
    OR fecha_fin_oficial >= fecha_inicio_oficial)
);

-- Un contrato por obra (1:1): el contrato es un atributo de la obra.
CREATE TABLE contratos (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  obra_id                 UUID NOT NULL UNIQUE REFERENCES obras(id) ON DELETE CASCADE,
  numero_contrato         VARCHAR(100) NOT NULL UNIQUE,
  contratista             VARCHAR(255) NOT NULL,
  fecha_firma             DATE,
  fecha_inicio            DATE,
  fecha_fin               DATE,
  monto_total_contratado  NUMERIC(15,2) NOT NULL CHECK (monto_total_contratado >= 0),
  porcentaje_iva          NUMERIC(5,2)  NOT NULL DEFAULT 16 CHECK (porcentaje_iva BETWEEN 0 AND 100),
  porcentaje_retencion    NUMERIC(5,2)  NOT NULL DEFAULT 0  CHECK (porcentaje_retencion BETWEEN 0 AND 100),
  porcentaje_anticipo     NUMERIC(5,2)  NOT NULL DEFAULT 0  CHECK (porcentaje_anticipo BETWEEN 0 AND 100),
  -- Retención económica por atraso (LOPSRM art. 46 Bis) y garantías (art. 48).
  porcentaje_retencion_atraso NUMERIC(5,2) NOT NULL DEFAULT 5,
  monto_garantia_cumplimiento NUMERIC(15,2),
  numero_fianza           VARCHAR(100),
  afianzadora             VARCHAR(255),
  monto_anticipo          NUMERIC(15,2) GENERATED ALWAYS AS
                            (ROUND(monto_total_contratado * porcentaje_anticipo / 100, 2)) STORED,
  fecha_registro          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_contratos_ret_atraso CHECK (porcentaje_retencion_atraso BETWEEN 0 AND 100),
  CONSTRAINT ck_contratos_fechas CHECK (
    fecha_inicio IS NULL OR fecha_fin IS NULL OR fecha_fin >= fecha_inicio)
);

-- Ministraciones de anticipo del contrato.
CREATE TABLE anticipos (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contrato_id       UUID NOT NULL REFERENCES contratos(id) ON DELETE CASCADE,
  tipo              VARCHAR(30) NOT NULL DEFAULT 'Inicio de obra'
                      CHECK (tipo IN ('Inicio de obra', 'Materiales', 'Otro')),
  monto             NUMERIC(15,2) NOT NULL CHECK (monto > 0),
  fecha_programada  DATE,
  fecha_pago        DATE,
  estatus           VARCHAR(20) NOT NULL DEFAULT 'Programado'
                      CHECK (estatus IN ('Programado', 'Pagado', 'Cancelado')),
  observaciones     TEXT,
  fecha_registro    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE catalogo_conceptos (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  obra_id                   UUID NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
  capitulo                  VARCHAR(100),
  clave                     VARCHAR(50) NOT NULL,
  descripcion               TEXT NOT NULL,
  unidad_medida             VARCHAR(20) NOT NULL,
  volumen_total_contratado  NUMERIC(14,4) NOT NULL CHECK (volumen_total_contratado >= 0),
  precio_unitario           NUMERIC(15,2) NOT NULL CHECK (precio_unitario >= 0),
  importe_contratado        NUMERIC(18,2) GENERATED ALWAYS AS
                              (ROUND(volumen_total_contratado * precio_unitario, 2)) STORED,
  CONSTRAINT uq_conceptos_obra_clave UNIQUE (obra_id, clave)
);

-- ---------------------------------------------------------------------
-- 2. USUARIOS, ROLES Y ESTATUS
-- ---------------------------------------------------------------------

CREATE TABLE usuarios (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre          VARCHAR(100) NOT NULL,
  apellidos       VARCHAR(100) NOT NULL,
  correo          CITEXT NOT NULL UNIQUE,
  password_hash   VARCHAR(255) NOT NULL,
  activo          BOOLEAN NOT NULL DEFAULT true,
  fecha_creacion  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE roles (
  id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre  VARCHAR(50) NOT NULL UNIQUE
);

CREATE TABLE usuarios_proyectos_roles (
  usuario_id  UUID NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  obra_id     UUID NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
  rol_id      UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  PRIMARY KEY (usuario_id, obra_id, rol_id)
);

-- IDs explícitos: las tablas con estatus_id usan DEFAULT 1 ('Pendiente').
CREATE TABLE catalogo_estatus_validacion (
  id           SMALLINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  nombre       VARCHAR(50) NOT NULL UNIQUE,
  descripcion  TEXT
);

-- Tipos de nota de bitácora. ID 7 = 'Otra' (valor por omisión).
CREATE TABLE catalogo_tipos_nota (
  id           SMALLINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  nombre       VARCHAR(50) NOT NULL UNIQUE,
  descripcion  TEXT
);

-- ---------------------------------------------------------------------
-- 3. ESTIMACIONES
-- ---------------------------------------------------------------------

-- La obra se obtiene por contrato (contrato 1:1 con obra).
-- subtotal, retencion, iva y monto_neto los calculan triggers.
CREATE TABLE estimaciones (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contrato_id            UUID NOT NULL REFERENCES contratos(id),
  superintendente_id     UUID NOT NULL REFERENCES usuarios(id),
  numero_estimacion      INTEGER NOT NULL CHECK (numero_estimacion > 0),
  folio                  VARCHAR(50) NOT NULL,          -- EST-001, EST-002… por contrato
  tipo                   VARCHAR(20) NOT NULL DEFAULT 'Ordinaria'
                           CHECK (tipo IN ('Ordinaria', 'Extraordinaria', 'Finiquito')),
  periodo_inicio         DATE NOT NULL,
  fecha_corte            DATE NOT NULL,
  fecha_presentacion     DATE,
  subtotal               NUMERIC(15,2) NOT NULL DEFAULT 0,
  amortizacion_anticipo  NUMERIC(15,2) NOT NULL DEFAULT 0 CHECK (amortizacion_anticipo >= 0),
  retencion              NUMERIC(15,2) NOT NULL DEFAULT 0,
  iva                    NUMERIC(15,2) NOT NULL DEFAULT 0,
  monto_neto             NUMERIC(15,2) NOT NULL DEFAULT 0,
  retencion_atraso       NUMERIC(15,2) NOT NULL DEFAULT 0,   -- LOPSRM 46 Bis / Reglamento 88
  devolucion_retencion   NUMERIC(15,2) NOT NULL DEFAULT 0,
  dias_atraso            INTEGER,
  atraso_no_imputable    BOOLEAN NOT NULL DEFAULT false,
  justificacion_atraso   TEXT,
  estatus_id             SMALLINT NOT NULL DEFAULT 1 REFERENCES catalogo_estatus_validacion(id),
  observaciones          TEXT,
  fecha_envio            TIMESTAMPTZ,
  fecha_modificacion     TIMESTAMPTZ,
  fecha_registro         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_estimaciones_contrato_numero UNIQUE (contrato_id, numero_estimacion),
  CONSTRAINT uq_estimaciones_contrato_folio UNIQUE (contrato_id, folio),
  CONSTRAINT ck_estimaciones_retenciones CHECK (retencion_atraso >= 0 AND devolucion_retencion >= 0),
  CONSTRAINT ck_estimaciones_fechas CHECK (fecha_corte >= periodo_inicio)
);

CREATE TABLE estimaciones_detalles (
  id                           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  estimacion_id                UUID NOT NULL REFERENCES estimaciones(id) ON DELETE CASCADE,
  concepto_id                  UUID NOT NULL REFERENCES catalogo_conceptos(id),
  cantidad_ejecutada           NUMERIC(14,4) NOT NULL CHECK (cantidad_ejecutada >= 0),
  cantidad_acumulada_anterior  NUMERIC(14,4) NOT NULL DEFAULT 0,
  cantidad_acumulada           NUMERIC(14,4) GENERATED ALWAYS AS
                                 (cantidad_acumulada_anterior + cantidad_ejecutada) STORED,
  precio_unitario              NUMERIC(15,2) NOT NULL CHECK (precio_unitario >= 0),
  importe                      NUMERIC(15,2) GENERATED ALWAYS AS
                                 (ROUND(cantidad_ejecutada * precio_unitario, 2)) STORED,
  es_excedente                 BOOLEAN NOT NULL DEFAULT false,
  numero_generador             VARCHAR(50),
  observaciones                TEXT,
  nota_bitacora_id             UUID,   -- orden escrita del excedente (Reglamento 118); FK más abajo
  CONSTRAINT uq_detalle_estimacion_concepto UNIQUE (estimacion_id, concepto_id)
);

-- ---------------------------------------------------------------------
-- 4. REPORTES DIARIOS
-- ---------------------------------------------------------------------

CREATE TABLE reportes_diarios (
  id                             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  obra_id                        UUID NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
  supervisor_id                  UUID NOT NULL REFERENCES usuarios(id),
  fecha_reporte                  DATE NOT NULL,
  jornada_inicio                 TIME,
  jornada_fin                    TIME,
  clima                          VARCHAR(20)
                                   CHECK (clima IN ('Soleado', 'Nublado', 'Lluvia', 'Tormenta', 'Viento fuerte', 'Otro')),
  temperatura_c                  NUMERIC(4,1),
  actividades_realizadas         TEXT NOT NULL,
  incidencias                    TEXT,
  incidentes_seguridad           TEXT,
  avance_fisico_real_porcentaje  NUMERIC(5,2) NOT NULL
                                   CHECK (avance_fisico_real_porcentaje BETWEEN 0 AND 100),
  observaciones                  TEXT,
  -- Atraso crítico (1 día o más detrás del programa): causa y solicitud del supervisor.
  atraso_causa                   TEXT,
  atraso_solicitud               VARCHAR(20),
  estatus_id                     SMALLINT NOT NULL DEFAULT 1 REFERENCES catalogo_estatus_validacion(id),
  fecha_registro                 TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_reporte_obra_fecha UNIQUE (obra_id, fecha_reporte),
  CONSTRAINT ck_reporte_atraso_solicitud CHECK (atraso_solicitud IS NULL OR atraso_solicitud IN ('Reprogramación', 'Retención', 'Ninguna'))
);

-- Programa de obra: avance físico programado ACUMULADO en fechas clave (se interpola entre ellas).
CREATE TABLE programa_obra (
  id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  obra_id                       UUID NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
  fecha                         DATE NOT NULL,
  avance_programado_porcentaje  NUMERIC(5,2) NOT NULL
                                  CHECK (avance_programado_porcentaje BETWEEN 0 AND 100),
  fecha_registro                TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_programa_obra_fecha UNIQUE (obra_id, fecha)
);

-- ---------------------------------------------------------------------
-- 5. REPORTES MENSUALES, BITÁCORA, DOCUMENTOS Y EVIDENCIAS
-- ---------------------------------------------------------------------

CREATE TABLE reportes_mensuales (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  obra_id         UUID NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
  supervisor_id   UUID NOT NULL REFERENCES usuarios(id),
  anio            SMALLINT NOT NULL CHECK (anio BETWEEN 2000 AND 2100),
  mes             SMALLINT NOT NULL CHECK (mes BETWEEN 1 AND 12),
  fecha_corte     DATE NOT NULL,
  resumen         TEXT,
  observaciones   TEXT,
  estatus_id      SMALLINT NOT NULL DEFAULT 1 REFERENCES catalogo_estatus_validacion(id),
  -- Consolidado del mes guardado al aprobarse; NULL = se calcula al vuelo.
  consolidado     JSONB,
  fecha_registro  TIMESTAMPTZ NOT NULL DEFAULT now(),
  fecha_modificacion TIMESTAMPTZ,
  CONSTRAINT uq_reporte_mensual UNIQUE (obra_id, anio, mes),
  CONSTRAINT ck_reporte_mensual_corte CHECK (
    fecha_corte >= make_date(anio, mes, 1)
    AND fecha_corte < make_date(anio, mes, 1) + INTERVAL '1 month')
);

-- folio: consecutivo por obra, lo asigna el trigger trg_nota_folio (nunca se reutiliza).
-- estado_firma se deriva de firmas_notas_bitacora (lo mantiene la aplicación).
CREATE TABLE notas_bitacora (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  obra_id              UUID NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
  folio                INTEGER NOT NULL DEFAULT 0,
  tipo_id              SMALLINT NOT NULL DEFAULT 7 REFERENCES catalogo_tipos_nota(id),
  usuario_creador_id   UUID NOT NULL REFERENCES usuarios(id),
  fecha_nota           TIMESTAMPTZ NOT NULL DEFAULT now(),
  fecha_modificacion   TIMESTAMPTZ,
  descripcion          TEXT NOT NULL,
  nota_relacionada_id  UUID REFERENCES notas_bitacora(id) ON DELETE SET NULL,
  estado_firma         VARCHAR(20) NOT NULL DEFAULT 'Pendiente'
                         CHECK (estado_firma IN ('Pendiente', 'Firmada', 'Rechazada')),
  CONSTRAINT uq_notas_obra_folio UNIQUE (obra_id, folio),
  CONSTRAINT ck_nota_no_se_relaciona_consigo CHECK (nota_relacionada_id IS NULL OR nota_relacionada_id <> id)
);

ALTER TABLE estimaciones_detalles
  ADD CONSTRAINT fk_detalle_nota_bitacora FOREIGN KEY (nota_bitacora_id) REFERENCES notas_bitacora(id);

-- Notas de bitácora vinculadas a un reporte diario.
CREATE TABLE reportes_diarios_notas (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reporte_diario_id  UUID NOT NULL REFERENCES reportes_diarios(id) ON DELETE CASCADE,
  nota_bitacora_id   UUID NOT NULL REFERENCES notas_bitacora(id) ON DELETE CASCADE,
  CONSTRAINT uq_reporte_nota UNIQUE (reporte_diario_id, nota_bitacora_id)
);

CREATE TABLE documentos (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  obra_id             UUID NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
  usuario_subida_id   UUID NOT NULL REFERENCES usuarios(id),
  nombre_archivo      VARCHAR(255) NOT NULL,
  ruta_almacenamiento VARCHAR(500) NOT NULL,
  tipo_archivo        VARCHAR(50),
  descripcion         TEXT,
  fecha_subida        TIMESTAMPTZ NOT NULL DEFAULT now(),
  categoria           VARCHAR(40) NOT NULL DEFAULT 'Otro',
  estimacion_id       UUID REFERENCES estimaciones(id) ON DELETE CASCADE,   -- soporte de una estimación
  CONSTRAINT ck_documentos_categoria CHECK (categoria IN (
    'Contrato', 'Reporte', 'Documento técnico', 'Plano', 'Fotografía', 'Factura', 'Números generadores',
    'Croquis', 'Prueba de laboratorio', 'Otro'))
);

CREATE TABLE evidencias (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ruta                VARCHAR(500) NOT NULL,
  tipo                VARCHAR(50),
  descripcion         TEXT,
  usuario_subida_id   UUID NOT NULL REFERENCES usuarios(id),
  fecha_subida        TIMESTAMPTZ NOT NULL DEFAULT now(),
  reporte_diario_id   UUID REFERENCES reportes_diarios(id) ON DELETE CASCADE,
  reporte_mensual_id  UUID REFERENCES reportes_mensuales(id) ON DELETE CASCADE,
  nota_bitacora_id    UUID REFERENCES notas_bitacora(id) ON DELETE CASCADE,
  CONSTRAINT ck_evidencia_un_padre CHECK
    (num_nonnulls(reporte_diario_id, reporte_mensual_id, nota_bitacora_id) = 1)
);

-- ---------------------------------------------------------------------
-- 6. VALIDACIONES (historial de revisiones)
-- ---------------------------------------------------------------------

CREATE TABLE validaciones_estimaciones (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  estimacion_id         UUID NOT NULL REFERENCES estimaciones(id) ON DELETE CASCADE,
  usuario_validador_id  UUID NOT NULL REFERENCES usuarios(id),
  estatus_id            SMALLINT NOT NULL REFERENCES catalogo_estatus_validacion(id),
  fecha_validacion      TIMESTAMPTZ NOT NULL DEFAULT now(),
  comentarios           TEXT,
  rol_validador         VARCHAR(40) CHECK (rol_validador IS NULL OR rol_validador IN ('Supervisor', 'Residente'))
);

CREATE TABLE validaciones_reportes_diarios (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reporte_diario_id     UUID NOT NULL REFERENCES reportes_diarios(id) ON DELETE CASCADE,
  usuario_validador_id  UUID NOT NULL REFERENCES usuarios(id),
  rol_validador         VARCHAR(40)
                          CHECK (rol_validador IS NULL OR rol_validador IN ('Residente', 'Superintendente Contratista')),
  estatus_id            SMALLINT NOT NULL REFERENCES catalogo_estatus_validacion(id),
  fecha_validacion      TIMESTAMPTZ NOT NULL DEFAULT now(),
  comentarios           TEXT
);

-- Firmas de las notas de bitácora: la última decisión de cada rol es la vigente.
-- 'Pendiente' marca un reinicio (nota corregida y reenviada, o corrección del administrador).
CREATE TABLE firmas_notas_bitacora (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nota_bitacora_id      UUID NOT NULL REFERENCES notas_bitacora(id) ON DELETE CASCADE,
  usuario_firmante_id   UUID NOT NULL REFERENCES usuarios(id),
  rol_firmante          VARCHAR(40) NOT NULL
                          CHECK (rol_firmante IN ('Residente', 'Supervisor', 'Superintendente Contratista')),
  decision              VARCHAR(20) NOT NULL CHECK (decision IN ('Firmada', 'Rechazada', 'Pendiente')),
  fecha_firma           TIMESTAMPTZ NOT NULL DEFAULT now(),
  comentarios           TEXT
);

CREATE TABLE validaciones_reportes_mensuales (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  reporte_mensual_id    UUID NOT NULL REFERENCES reportes_mensuales(id) ON DELETE CASCADE,
  usuario_validador_id  UUID NOT NULL REFERENCES usuarios(id),
  rol_validador         VARCHAR(40) NOT NULL
                          CHECK (rol_validador IN ('Residente', 'Superintendente Contratista')),
  estatus_id            SMALLINT NOT NULL REFERENCES catalogo_estatus_validacion(id),
  fecha_validacion      TIMESTAMPTZ NOT NULL DEFAULT now(),
  comentarios           TEXT
);

-- ---------------------------------------------------------------------
-- 7. ÍNDICES (Postgres no indexa FKs automáticamente)
-- ---------------------------------------------------------------------

CREATE INDEX ix_anticipos_contrato        ON anticipos (contrato_id);
CREATE INDEX ix_upr_obra                  ON usuarios_proyectos_roles (obra_id);
CREATE INDEX ix_upr_rol                   ON usuarios_proyectos_roles (rol_id);
CREATE INDEX ix_estimaciones_superint     ON estimaciones (superintendente_id);
CREATE INDEX ix_estimaciones_estatus      ON estimaciones (estatus_id);
CREATE INDEX ix_detalles_concepto         ON estimaciones_detalles (concepto_id);
CREATE INDEX ix_reportes_diarios_super    ON reportes_diarios (supervisor_id);
CREATE INDEX ix_reportes_diarios_estatus  ON reportes_diarios (estatus_id);
CREATE INDEX ix_reportes_mens_super       ON reportes_mensuales (supervisor_id);
CREATE INDEX ix_reportes_mens_estatus     ON reportes_mensuales (estatus_id);
CREATE INDEX ix_val_rm_reporte            ON validaciones_reportes_mensuales (reporte_mensual_id);
CREATE INDEX ix_val_rm_validador          ON validaciones_reportes_mensuales (usuario_validador_id);
CREATE INDEX ix_val_rm_estatus            ON validaciones_reportes_mensuales (estatus_id);
CREATE INDEX ix_notas_obra_fecha          ON notas_bitacora (obra_id, fecha_nota);
CREATE INDEX ix_notas_creador             ON notas_bitacora (usuario_creador_id);
CREATE INDEX ix_notas_tipo                ON notas_bitacora (tipo_id);
CREATE INDEX ix_notas_relacionada         ON notas_bitacora (nota_relacionada_id) WHERE nota_relacionada_id IS NOT NULL;
CREATE INDEX ix_firmas_nota               ON firmas_notas_bitacora (nota_bitacora_id);
CREATE INDEX ix_firmas_firmante           ON firmas_notas_bitacora (usuario_firmante_id);
CREATE INDEX ix_documentos_obra           ON documentos (obra_id);
CREATE INDEX ix_documentos_usuario        ON documentos (usuario_subida_id);
CREATE INDEX ix_evidencias_rd             ON evidencias (reporte_diario_id)  WHERE reporte_diario_id  IS NOT NULL;
CREATE INDEX ix_evidencias_rm             ON evidencias (reporte_mensual_id) WHERE reporte_mensual_id IS NOT NULL;
CREATE INDEX ix_evidencias_nb             ON evidencias (nota_bitacora_id)   WHERE nota_bitacora_id   IS NOT NULL;
CREATE INDEX ix_evidencias_usuario        ON evidencias (usuario_subida_id);
CREATE INDEX ix_rd_notas_nota             ON reportes_diarios_notas (nota_bitacora_id);
CREATE INDEX ix_documentos_estimacion     ON documentos (estimacion_id) WHERE estimacion_id IS NOT NULL;
CREATE INDEX ix_val_est_estimacion        ON validaciones_estimaciones (estimacion_id);
CREATE INDEX ix_val_est_validador         ON validaciones_estimaciones (usuario_validador_id);
CREATE INDEX ix_val_est_estatus           ON validaciones_estimaciones (estatus_id);
CREATE INDEX ix_val_rd_reporte            ON validaciones_reportes_diarios (reporte_diario_id);
CREATE INDEX ix_val_rd_validador          ON validaciones_reportes_diarios (usuario_validador_id);
CREATE INDEX ix_val_rd_estatus            ON validaciones_reportes_diarios (estatus_id);

-- ---------------------------------------------------------------------
-- 8. TRIGGERS
-- ---------------------------------------------------------------------

-- 8.1 Los anticipos registrados no pueden exceder el anticipo autorizado en el contrato.
CREATE FUNCTION fn_validar_tope_anticipos() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_tope   NUMERIC(15,2);
  v_previo NUMERIC(15,2);
BEGIN
  IF NEW.estatus = 'Cancelado' THEN
    RETURN NEW;
  END IF;
  SELECT monto_anticipo INTO v_tope FROM contratos WHERE id = NEW.contrato_id;
  SELECT COALESCE(SUM(monto), 0) INTO v_previo
    FROM anticipos
   WHERE contrato_id = NEW.contrato_id AND estatus <> 'Cancelado' AND id <> NEW.id;
  IF v_previo + NEW.monto > v_tope THEN
    RAISE EXCEPTION 'Los anticipos (%) exceden el anticipo autorizado del contrato (%)',
      v_previo + NEW.monto, v_tope;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_anticipos_tope
  BEFORE INSERT OR UPDATE ON anticipos
  FOR EACH ROW EXECUTE FUNCTION fn_validar_tope_anticipos();

-- 8.2 Precio, acumulado anterior y excedente de cada renglón de estimación.
CREATE FUNCTION fn_preparar_detalle_estimacion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_obra_concepto UUID;
  v_obra_contrato UUID;
  v_precio        NUMERIC(15,2);
  v_volumen       NUMERIC(14,4);
  v_contrato      UUID;
  v_numero        INTEGER;
BEGIN
  SELECT obra_id, precio_unitario, volumen_total_contratado
    INTO v_obra_concepto, v_precio, v_volumen
    FROM catalogo_conceptos WHERE id = NEW.concepto_id;

  SELECT e.contrato_id, e.numero_estimacion, c.obra_id
    INTO v_contrato, v_numero, v_obra_contrato
    FROM estimaciones e JOIN contratos c ON c.id = e.contrato_id
   WHERE e.id = NEW.estimacion_id;

  IF v_obra_concepto <> v_obra_contrato THEN
    RAISE EXCEPTION 'El concepto no pertenece a la obra del contrato de la estimación';
  END IF;

  NEW.precio_unitario := COALESCE(NEW.precio_unitario, v_precio);

  SELECT COALESCE(SUM(d.cantidad_ejecutada), 0)
    INTO NEW.cantidad_acumulada_anterior
    FROM estimaciones_detalles d
    JOIN estimaciones e ON e.id = d.estimacion_id
   WHERE d.concepto_id = NEW.concepto_id
     AND e.contrato_id = v_contrato
     AND e.numero_estimacion < v_numero;

  NEW.es_excedente := (NEW.cantidad_acumulada_anterior + NEW.cantidad_ejecutada) > v_volumen;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_detalle_preparar
  BEFORE INSERT OR UPDATE ON estimaciones_detalles
  FOR EACH ROW EXECUTE FUNCTION fn_preparar_detalle_estimacion();

-- 8.3 El subtotal de la estimación es la suma de sus renglones.
CREATE FUNCTION fn_actualizar_subtotal_estimacion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_id UUID;
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_id := OLD.estimacion_id;
  ELSE
    v_id := NEW.estimacion_id;
  END IF;
  UPDATE estimaciones
     SET subtotal = COALESCE((SELECT SUM(importe) FROM estimaciones_detalles WHERE estimacion_id = v_id), 0)
   WHERE id = v_id;
  RETURN NULL;
END $$;

CREATE TRIGGER trg_detalle_subtotal
  AFTER INSERT OR UPDATE OR DELETE ON estimaciones_detalles
  FOR EACH ROW EXECUTE FUNCTION fn_actualizar_subtotal_estimacion();

-- 8.4 Retención, IVA y monto neto a partir del subtotal y la amortización.
--     IVA = (subtotal - amortización) * %IVA ; retención = subtotal * %retención
CREATE FUNCTION fn_calcular_totales_estimacion() RETURNS trigger LANGUAGE plpgsql AS $$
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

CREATE TRIGGER trg_estimacion_totales
  BEFORE INSERT OR UPDATE ON estimaciones
  FOR EACH ROW EXECUTE FUNCTION fn_calcular_totales_estimacion();

-- 8.5 Folio consecutivo de la nota de bitácora por obra (bloquea la fila de la obra: sin choques).
CREATE FUNCTION fn_asignar_folio_bitacora() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.folio IS NULL OR NEW.folio = 0 THEN
    UPDATE obras SET ultimo_folio_bitacora = ultimo_folio_bitacora + 1
     WHERE id = NEW.obra_id
     RETURNING ultimo_folio_bitacora INTO NEW.folio;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_nota_folio
  BEFORE INSERT ON notas_bitacora
  FOR EACH ROW EXECUTE FUNCTION fn_asignar_folio_bitacora();

-- ---------------------------------------------------------------------
-- 9. VISTA: resumen financiero del contrato (solo estimaciones aprobadas)
-- ---------------------------------------------------------------------

CREATE VIEW v_resumen_contrato AS
SELECT c.id                       AS contrato_id,
       c.obra_id,
       c.numero_contrato,
       c.monto_total_contratado,
       c.monto_anticipo           AS anticipo_autorizado,
       COALESCE(a.pagado, 0)      AS anticipo_pagado,
       COALESCE(e.amortizado, 0)  AS anticipo_amortizado,
       COALESCE(a.pagado, 0) - COALESCE(e.amortizado, 0) AS saldo_por_amortizar,
       COALESCE(e.estimado, 0)    AS importe_estimado_aprobado,
       CASE WHEN c.monto_total_contratado > 0
            THEN ROUND(COALESCE(e.estimado, 0) * 100 / c.monto_total_contratado, 2)
            ELSE 0 END            AS avance_financiero_pct
  FROM contratos c
  LEFT JOIN (SELECT contrato_id, SUM(monto) AS pagado
               FROM anticipos WHERE estatus = 'Pagado' GROUP BY contrato_id) a
         ON a.contrato_id = c.id
  LEFT JOIN (SELECT es.contrato_id,
                    SUM(es.subtotal)              AS estimado,
                    SUM(es.amortizacion_anticipo) AS amortizado
               FROM estimaciones es
               JOIN catalogo_estatus_validacion s ON s.id = es.estatus_id
              WHERE s.nombre = 'Aprobado'
              GROUP BY es.contrato_id) e
         ON e.contrato_id = c.id;

-- ---------------------------------------------------------------------
-- 10. DATOS INICIALES
-- ---------------------------------------------------------------------

INSERT INTO roles (nombre) VALUES
  ('Administrador'),
  ('Residente'),
  ('Supervisor'),
  ('Superintendente Contratista'),
  ('Directivo');

INSERT INTO catalogo_estatus_validacion (id, nombre, descripcion) VALUES
  (1, 'Pendiente', 'Registrado, aún sin revisión'),
  (2, 'Aprobado', 'Revisado y aceptado'),
  (3, 'Rechazado', 'Revisado y devuelto con observaciones'),
  (4, 'En revisión', 'Revisión en curso'),
  (5, 'Borrador', 'Guardado por su autor, aún no enviado a revisión'),
  (6, 'Con observaciones', 'Regresado a su autor para corregir, sin rechazarlo');

SELECT setval(pg_get_serial_sequence('catalogo_estatus_validacion', 'id'), 6);

INSERT INTO catalogo_tipos_nota (id, nombre, descripcion) VALUES
  (1, 'Apertura',    'Apertura de la bitácora y datos generales de la obra'),
  (2, 'Instrucción', 'Instrucción u orden de trabajo'),
  (3, 'Solicitud',   'Solicitud de información, aprobación o cambio'),
  (4, 'Avance',      'Registro de avance o de trabajos ejecutados'),
  (5, 'Incidencia',  'Incidencia, problema o situación extraordinaria'),
  (6, 'Cierre',      'Cierre de la bitácora'),
  (7, 'Otra',        'Cualquier otro asunto');

SELECT setval(pg_get_serial_sequence('catalogo_tipos_nota', 'id'), 7);
