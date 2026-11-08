"""MS4 · Oportunidades y Cotizaciones. PostgreSQL ventas_db, un schema por tenant.

Publica: oportunidad.etapa_cambiada, oportunidad.ganada.
Consume (cola crm-ms4): tenant.creado, lead.convertido.
Guarda cada cotización como documento HTML en S3.
"""
import html
import os
from decimal import Decimal

import boto3

from . import db, reglas
from .comun import (DatoInvalido, NoEncontrado, ReglaNoCumplida, ahora, entero, enrutar,
                    exigir_rol, leer_cuerpo, log, paginacion, parametro, procesar_cola, publicar,
                    puede_ver, responder, ruta)

s3 = boto3.client('s3')
BUCKET = os.environ['BUCKET_DOCUMENTOS']


def _hoy():
    return ahora().date()


def _oportunidad_visible(con, ctx, oportunidad_id, bloquear=False):
    sql = 'SELECT * FROM oportunidades WHERE id = :id' + (' FOR UPDATE' if bloquear else '')
    oportunidad = db.fila(con, sql, id=oportunidad_id)
    if not oportunidad or not puede_ver(ctx, oportunidad['propietario']):
        raise NoEncontrado('oportunidad_no_encontrada', oportunidad_id)
    return oportunidad


def _con_ponderado(oportunidad):
    oportunidad['probabilidad'] = reglas.PROBABILIDAD[oportunidad['etapa']]
    oportunidad['monto_ponderado'] = reglas.monto_ponderado(oportunidad['monto'], oportunidad['etapa'])
    return oportunidad


def _url_documento(key):
    return s3.generate_presigned_url('get_object', Params={'Bucket': BUCKET, 'Key': key}, ExpiresIn=3600)


# ---------- Rutas ----------

def salud(event, ctx):
    return responder(200, {'status': 'ok', 'servicio': 'ms4'})


def listar_oportunidades(event, ctx):
    page, limit = paginacion(event)
    con = db.conexion(ctx['tenant_id'])
    condiciones, params = [], {}
    if parametro(event, 'etapa'):
        condiciones.append('etapa = :etapa')
        params['etapa'] = reglas.validar_etapa(parametro(event, 'etapa'))
    if ctx['rol'] == 'vendedor':
        condiciones.append('propietario = :yo')
        params['yo'] = ctx['usuario_id']
    where = f'WHERE {" AND ".join(condiciones)}' if condiciones else ''
    total = db.fila(con, f'SELECT count(*) AS total FROM oportunidades {where}', **params)['total']
    items = db.filas(
        con, f'SELECT * FROM oportunidades {where} ORDER BY actualizado_en DESC LIMIT :limit OFFSET :offset',
        limit=limit, offset=(page - 1) * limit, **params,
    )
    return responder(200, {'total': total, 'page': page, 'limit': limit, 'items': [_con_ponderado(o) for o in items]})


def obtener_oportunidad(event, ctx):
    con = db.conexion(ctx['tenant_id'])
    oportunidad = _con_ponderado(_oportunidad_visible(con, ctx, entero(ruta(event, 'id'), 'id', minimo=1)))
    oportunidad['cotizaciones'] = db.filas(
        con, 'SELECT * FROM cotizaciones WHERE oportunidad_id = :o ORDER BY id DESC', o=oportunidad['id'])
    for cotizacion in oportunidad['cotizaciones']:
        # URL firmada (1 hora) al HTML guardado en S3; firmar es local, no llama a S3.
        cotizacion['documento_url'] = _url_documento(cotizacion['documento_key']) if cotizacion['documento_key'] else None
    return responder(200, oportunidad)


def crear_oportunidad(event, ctx):
    datos = leer_cuerpo(event)
    titulo = (datos.get('titulo') or '').strip()
    if not titulo:
        raise DatoInvalido('campo_obligatorio', 'titulo es obligatorio')
    monto = Decimal(str(datos.get('monto', 0)))
    if monto < 0:
        raise DatoInvalido('monto_invalido', 'El monto no puede ser negativo')
    # Un gerente puede crearla a nombre de otro vendedor.
    propietario = datos.get('propietario') if ctx['rol'] in ('gerente', 'admin') else None
    con = db.conexion(ctx['tenant_id'])
    oportunidad = db.fila(
        con,
        """INSERT INTO oportunidades (titulo, cuenta_id, propietario, monto, fecha_cierre_estimada)
           VALUES (:titulo, :cuenta_id, :propietario, :monto, CAST(:cierre AS date)) RETURNING *""",
        titulo=titulo[:150], cuenta_id=datos.get('cuenta_id'), propietario=propietario or ctx['usuario_id'],
        monto=monto, cierre=datos.get('fecha_cierre_estimada'),
    )
    return responder(201, _con_ponderado(oportunidad))


def cambiar_etapa(event, ctx):
    datos = leer_cuerpo(event)
    nueva = datos.get('etapa')
    con = db.conexion(ctx['tenant_id'])
    with db.transaccion(con):
        oportunidad = _oportunidad_visible(con, ctx, entero(ruta(event, 'id'), 'id', minimo=1), bloquear=True)
        cotizaciones = db.filas(con, 'SELECT estado, total FROM cotizaciones WHERE oportunidad_id = :o', o=oportunidad['id'])
        aceptada = next((c for c in cotizaciones if c['estado'] == 'aceptada'), None)
        reglas.validar_cambio_etapa(
            oportunidad['etapa'], nueva, ctx['rol'],
            tiene_cotizacion=bool(cotizaciones), tiene_cotizacion_aceptada=aceptada is not None,
            motivo=datos.get('motivo'),
        )
        # Al ganar, el monto real es el total de la cotización aceptada.
        monto = aceptada['total'] if nueva == 'ganada' else oportunidad['monto']
        actualizada = db.fila(
            con,
            """UPDATE oportunidades SET etapa = :etapa, monto = :monto, motivo_perdida = :motivo,
                      actualizado_en = now(),
                      cerrado_en = CASE WHEN :etapa IN ('ganada', 'perdida') THEN now() END
               WHERE id = :id RETURNING *""",
            etapa=nueva, monto=monto, motivo=datos.get('motivo') if nueva == 'perdida' else None, id=oportunidad['id'],
        )
    # Se publica DESPUÉS del COMMIT: nunca se anuncia algo que no quedó guardado.
    resumen = {
        'oportunidad_id': actualizada['id'], 'titulo': actualizada['titulo'], 'cuenta_id': actualizada['cuenta_id'],
        'lead_id': actualizada['lead_id'], 'propietario': actualizada['propietario'], 'monto': actualizada['monto'],
    }
    publicar('oportunidad.etapa_cambiada', ctx['tenant_id'], {**resumen, 'desde': oportunidad['etapa'], 'hacia': nueva})
    if nueva == 'ganada':
        publicar('oportunidad.ganada', ctx['tenant_id'], resumen)
    return responder(200, _con_ponderado(actualizada))


def crear_cotizacion(event, ctx):
    datos = leer_cuerpo(event)
    items = datos.get('items') or []
    if not isinstance(items, list) or not items:
        raise DatoInvalido('cotizacion_vacia', 'items debe ser una lista con al menos un producto')
    con = db.conexion(ctx['tenant_id'])
    with db.transaccion(con):
        oportunidad = _oportunidad_visible(con, ctx, entero(datos.get('oportunidad_id'), 'oportunidad_id', minimo=1))
        reglas.puede_cotizar(oportunidad['etapa'])
        ids = [entero(i.get('producto_id'), 'producto_id', minimo=1) for i in items]
        productos = {p['id']: p for p in db.filas(
            con, 'SELECT * FROM productos WHERE activo AND id = ANY(CAST(:ids AS bigint[]))', ids=ids)}
        faltantes = [i for i in ids if i not in productos]
        if faltantes:
            raise NoEncontrado('producto_no_encontrado', faltantes)
        lineas = [(i.get('cantidad'), productos[pid]['precio']) for i, pid in zip(items, ids)]
        calculo = reglas.calcular_cotizacion(lineas, datos.get('descuento_pct', 0), ctx['rol'])
        cotizacion = db.fila(
            con,
            """INSERT INTO cotizaciones (oportunidad_id, descuento_pct, subtotal, descuento, base_imponible,
                                         igv, total, vence_en, creado_por)
               VALUES (:o, :descuento_pct, :subtotal, :descuento, :base_imponible, :igv, :total, :vence, :yo)
               RETURNING *""",
            o=oportunidad['id'], vence=reglas.vencimiento_cotizacion(_hoy()), yo=ctx['usuario_id'], **calculo,
        )
        for (cantidad, precio), pid in zip(lineas, ids):
            con.run(
                'INSERT INTO cotizacion_items (cotizacion_id, producto_id, cantidad, precio_unitario) VALUES (:c, :p, :n, :precio)',
                c=cotizacion['id'], p=pid, n=cantidad, precio=precio,
            )
        key = f'cotizaciones/{ctx["tenant_id"]}/{cotizacion["id"]}.html'
        s3.put_object(
            Bucket=BUCKET, Key=key, ContentType='text/html; charset=utf-8',
            Body=_html_cotizacion(oportunidad, cotizacion, lineas, [productos[p] for p in ids]).encode('utf-8'),
        )
        cotizacion = db.fila(con, 'UPDATE cotizaciones SET documento_key = :k WHERE id = :id RETURNING *', k=key, id=cotizacion['id'])
    cotizacion['documento_url'] = _url_documento(key)
    return responder(201, cotizacion)


def cambiar_estado_cotizacion(event, ctx):
    nuevo = leer_cuerpo(event).get('estado')
    con = db.conexion(ctx['tenant_id'])
    with db.transaccion(con):
        cotizacion = db.fila(con, 'SELECT * FROM cotizaciones WHERE id = :id FOR UPDATE', id=entero(ruta(event, 'id'), 'id', minimo=1))
        if not cotizacion:
            raise NoEncontrado('cotizacion_no_encontrada', ruta(event, 'id'))
        _oportunidad_visible(con, ctx, cotizacion['oportunidad_id'])
        reglas.validar_estado_cotizacion(cotizacion['estado'], nuevo, cotizacion['vence_en'], _hoy())
        if nuevo == 'aceptada' and db.fila(
                con, "SELECT id FROM cotizaciones WHERE oportunidad_id = :o AND estado = 'aceptada'",
                o=cotizacion['oportunidad_id']):
            raise ReglaNoCumplida('ya_hay_aceptada', 'La oportunidad ya tiene una cotización aceptada')
        cotizacion = db.fila(con, 'UPDATE cotizaciones SET estado = :e WHERE id = :id RETURNING *', e=nuevo, id=cotizacion['id'])
    return responder(200, cotizacion)


def pronostico(event, ctx):
    """Pronóstico de ventas: monto ponderado de lo abierto por vendedor y mes de cierre."""
    exigir_rol(ctx, 'gerente', 'admin')
    con = db.conexion(ctx['tenant_id'])
    grupos = db.filas(
        con,
        """SELECT propietario, to_char(fecha_cierre_estimada, 'YYYY-MM') AS mes, etapa,
                  count(*) AS oportunidades, sum(monto) AS monto
           FROM oportunidades WHERE etapa IN ('prospeccion', 'calificacion', 'propuesta', 'negociacion')
           GROUP BY propietario, mes, etapa""",
    )
    resumen = {}
    for g in grupos:
        clave = (g['propietario'], g['mes'])
        fila = resumen.setdefault(clave, {'propietario': g['propietario'], 'mes': g['mes'],
                                          'oportunidades': 0, 'monto': Decimal('0'), 'monto_ponderado': Decimal('0')})
        fila['oportunidades'] += g['oportunidades']
        fila['monto'] += g['monto']
        fila['monto_ponderado'] += reglas.monto_ponderado(g['monto'], g['etapa'])
    items = sorted(resumen.values(), key=lambda f: (f['mes'] or '9999-99', f['propietario'] or ''))
    return responder(200, {'items': items, 'total_ponderado': sum((f['monto_ponderado'] for f in items), Decimal('0'))})


def listar_productos(event, ctx):
    con = db.conexion(ctx['tenant_id'])
    return responder(200, {'items': db.filas(con, 'SELECT * FROM productos WHERE activo ORDER BY nombre')})


def _html_cotizacion(oportunidad, cotizacion, lineas, productos):
    e = html.escape
    filas = ''.join(
        f'<tr><td>{e(p["nombre"])}</td><td>{cantidad}</td><td>S/ {precio}</td>'
        f'<td>S/ {reglas.redondear(Decimal(str(precio)) * cantidad)}</td></tr>'
        for (cantidad, precio), p in zip(lineas, productos)
    )
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><title>Cotización {cotizacion['id']}</title>
<body style="font-family:sans-serif;max-width:720px;margin:32px auto">
<h1>Cotización N.º {cotizacion['id']}</h1>
<p>{e(oportunidad['titulo'])} · vence el {cotizacion['vence_en']}</p>
<table border="1" cellpadding="6" style="border-collapse:collapse;width:100%">
<tr><th>Producto</th><th>Cantidad</th><th>Precio</th><th>Importe</th></tr>{filas}</table>
<p>Subtotal: S/ {cotizacion['subtotal']}<br>Descuento ({cotizacion['descuento_pct']} %): S/ {cotizacion['descuento']}<br>
Base imponible: S/ {cotizacion['base_imponible']}<br>IGV 18 %: S/ {cotizacion['igv']}<br>
<strong>Total: S/ {cotizacion['total']}</strong></p></body></html>"""


RUTAS = {
    'GET /ms4/health': salud,
    'GET /ms4/oportunidades': listar_oportunidades,
    'GET /ms4/oportunidades/{id}': obtener_oportunidad,
    'POST /ms4/oportunidades': crear_oportunidad,
    'PATCH /ms4/oportunidades/{id}/etapa': cambiar_etapa,
    'POST /ms4/cotizaciones': crear_cotizacion,
    'PATCH /ms4/cotizaciones/{id}/estado': cambiar_estado_cotizacion,
    'GET /ms4/pronostico': pronostico,
    'GET /ms4/productos': listar_productos,
}


def main(event, _context):
    return enrutar(RUTAS, event)


# ---------- Consumidor de eventos ----------

def _al_convertir_lead(con, datos):
    """lead.convertido -> abre una oportunidad en prospección para el mismo vendedor."""
    con.run(
        """INSERT INTO oportunidades (titulo, lead_id, propietario, monto)
           VALUES (:titulo, :lead_id, :propietario, :monto) ON CONFLICT (lead_id) DO NOTHING""",
        titulo=f'Oportunidad · {datos.get("empresa", "sin nombre")}'[:150], lead_id=datos['lead_id'],
        propietario=datos.get('propietario'), monto=Decimal(str(datos.get('monto_estimado') or 0)),
    )


def _manejar(mensaje):
    if mensaje['evento'] == 'tenant.creado':
        db.crear_schema(mensaje['tenant_id'])
        return
    if mensaje['evento'] != 'lead.convertido':
        log.info('Evento ignorado: %s', mensaje['evento'])
        return
    con = db.conexion(mensaje['tenant_id'])
    with db.transaccion(con):
        if not db.ya_procesado(con, mensaje['id']):
            _al_convertir_lead(con, mensaje['datos'])


def consumidor(event, _context):
    return procesar_cola(event, _manejar)
