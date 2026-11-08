# MS4 · Oportunidades y Cotizaciones — CRM SaaS (CS2032 · Grupo 1)

Pipeline de ventas (6 etapas con reglas), cotizaciones con IGV 18 % y descuento máximo según el rol (el documento HTML se guarda en **S3**) y pronóstico ponderado.
Base de datos: **RDS PostgreSQL**, base `ventas_db`, **un schema por tenant** (Patrón 2). Arquitectura: [crm-grupo1-infra](https://github.com/enriquetorres-cell/crm-grupo1-infra).

## Endpoints (`/ms4`, todos con token salvo health)

| Método | Ruta | Qué hace |
| --- | --- | --- |
| GET | `/ms4/health` | público |
| GET | `/ms4/oportunidades?page=&limit=&etapa=` | listado paginado con monto ponderado |
| GET | `/ms4/oportunidades/{id}` | detalle con sus cotizaciones |
| POST | `/ms4/oportunidades` | crea una oportunidad |
| PATCH | `/ms4/oportunidades/{id}/etapa` | avanza de una en una; propuesta exige cotización, ganada exige una aceptada, perdida exige motivo; solo el gerente retrocede |
| POST | `/ms4/cotizaciones` | calcula subtotal, descuento, IGV y total, y genera el HTML en S3 |
| PATCH | `/ms4/cotizaciones/{id}/estado` | `borrador → enviada → aceptada / rechazada` (vigencia de 15 días) |
| GET | `/ms4/pronostico` | gerente/admin: monto ponderado por vendedor y mes |
| GET | `/ms4/productos` | catálogo |

## Eventos

- **Publica:** `oportunidad.etapa_cambiada` (MS5), `oportunidad.ganada` (MS2, MS5).
- **Consume (cola `crm-ms4`):** `tenant.creado` (crea el schema y el catálogo), `lead.convertido` (abre una oportunidad en prospección).

## Modelo de datos (por schema `tenant_x`)

Ver [`sql/esquema.sql`](sql/esquema.sql): `productos`, `oportunidades` 1—N `cotizaciones` 1—N `cotizacion_items` N—1 `productos`, más `eventos_procesados`.
Referencias lógicas: `cuenta_id` → MS2, `lead_id` → MS3, `propietario` → MS1.

## Desplegar

Requiere desplegados `crm-compartido`, `crm-rds` y `crm-ms1-usuarios`:

```bash
npm install
export CRM_DB_PASSWORD='...'      # PowerShell: $env:CRM_DB_PASSWORD = '...'
npx serverless deploy --stage dev
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

`src/comun.py` y `src/db.py` son copias de `comun/` del repo infra: edítalos allá y corre `sincronizar-comun`.
