-- MS4 · Oportunidades y Cotizaciones. Base ventas_db, un schema por tenant.
-- Se ejecuta dentro del schema del tenant (search_path) al recibir tenant.creado.
-- Referencias LÓGICAS: cuenta_id -> MS2, lead_id -> MS3, propietario -> MS1.

CREATE TABLE IF NOT EXISTS productos (
    id      BIGSERIAL PRIMARY KEY,
    codigo  VARCHAR(20) NOT NULL UNIQUE,
    nombre  VARCHAR(120) NOT NULL,
    precio  NUMERIC(12, 2) NOT NULL CHECK (precio >= 0),
    activo  BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS oportunidades (
    id                    BIGSERIAL PRIMARY KEY,
    titulo                VARCHAR(150) NOT NULL,
    cuenta_id             BIGINT,
    lead_id               VARCHAR(40) UNIQUE,
    propietario           VARCHAR(120),
    etapa                 VARCHAR(15) NOT NULL DEFAULT 'prospeccion'
                          CHECK (etapa IN ('prospeccion', 'calificacion', 'propuesta', 'negociacion', 'ganada', 'perdida')),
    monto                 NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (monto >= 0),
    fecha_cierre_estimada DATE,
    motivo_perdida        VARCHAR(200),
    creado_en             TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en        TIMESTAMPTZ NOT NULL DEFAULT now(),
    cerrado_en            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_oportunidades_etapa ON oportunidades (etapa);
CREATE INDEX IF NOT EXISTS ix_oportunidades_propietario ON oportunidades (propietario);

CREATE TABLE IF NOT EXISTS cotizaciones (
    id             BIGSERIAL PRIMARY KEY,
    oportunidad_id BIGINT NOT NULL REFERENCES oportunidades (id) ON DELETE CASCADE,
    estado         VARCHAR(10) NOT NULL DEFAULT 'borrador'
                   CHECK (estado IN ('borrador', 'enviada', 'aceptada', 'rechazada')),
    descuento_pct  NUMERIC(5, 2) NOT NULL DEFAULT 0,
    subtotal       NUMERIC(12, 2) NOT NULL,
    descuento      NUMERIC(12, 2) NOT NULL,
    base_imponible NUMERIC(12, 2) NOT NULL,
    igv            NUMERIC(12, 2) NOT NULL,
    total          NUMERIC(12, 2) NOT NULL,
    vence_en       DATE NOT NULL,
    documento_key  VARCHAR(200),
    creado_por     VARCHAR(120),
    creado_en      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Regla: una sola cotización aceptada por oportunidad (lo garantiza la BD).
CREATE UNIQUE INDEX IF NOT EXISTS ux_cotizacion_aceptada ON cotizaciones (oportunidad_id) WHERE estado = 'aceptada';

CREATE TABLE IF NOT EXISTS cotizacion_items (
    id              BIGSERIAL PRIMARY KEY,
    cotizacion_id   BIGINT NOT NULL REFERENCES cotizaciones (id) ON DELETE CASCADE,
    producto_id     BIGINT NOT NULL REFERENCES productos (id),
    cantidad        INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario NUMERIC(12, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS eventos_procesados (
    id           UUID PRIMARY KEY,
    procesado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Catálogo inicial para la demo (precios sin IGV).
INSERT INTO productos (codigo, nombre, precio) VALUES
    ('LIC-BAS', 'Licencia básica anual', 1200.00),
    ('LIC-PRO', 'Licencia profesional anual', 3600.00),
    ('IMP-STD', 'Implementación estándar', 5000.00),
    ('CAP-HRS', 'Capacitación (hora)', 150.00),
    ('SOP-PRE', 'Soporte premium anual', 2400.00)
ON CONFLICT (codigo) DO NOTHING;
