"""Reglas de negocio de MS4 (Oportunidades y Cotizaciones). Sin BD ni HTTP."""
import datetime
from decimal import ROUND_HALF_UP, Decimal

from .comun import DatoInvalido, ReglaNoCumplida, SinPermiso

ETAPAS = ('prospeccion', 'calificacion', 'propuesta', 'negociacion', 'ganada', 'perdida')
ABIERTAS = ETAPAS[:4]
FINALES = ('ganada', 'perdida')
PROBABILIDAD = {
    'prospeccion': Decimal('0.10'), 'calificacion': Decimal('0.25'), 'propuesta': Decimal('0.50'),
    'negociacion': Decimal('0.75'), 'ganada': Decimal('1'), 'perdida': Decimal('0'),
}
DESCUENTO_MAXIMO = {'vendedor': Decimal('10'), 'gerente': Decimal('25'), 'admin': Decimal('100')}
IGV = Decimal('0.18')
DIAS_VIGENCIA = 15
TRANSICIONES_COTIZACION = {
    'borrador': ('enviada',),
    'enviada': ('aceptada', 'rechazada'),
    'aceptada': (),
    'rechazada': (),
}

_CENTIMO = Decimal('0.01')


def redondear(monto):
    return Decimal(monto).quantize(_CENTIMO, rounding=ROUND_HALF_UP)


def validar_etapa(etapa):
    if etapa not in ETAPAS:
        raise DatoInvalido('etapa_invalida', f'Etapas: {", ".join(ETAPAS)}')
    return etapa


def validar_cambio_etapa(actual, nueva, rol, tiene_cotizacion, tiene_cotizacion_aceptada, motivo=None):
    """Lanza ReglaNoCumplida/SinPermiso si el cambio no se permite.

    - Ganada y perdida son finales.
    - Se avanza de una en una, o se salta a perdida desde cualquier etapa abierta.
    - Retroceder solo lo hace el gerente (o admin).
    - Propuesta exige cotización; ganada exige cotización aceptada; perdida exige motivo.
    """
    validar_etapa(nueva)
    if actual in FINALES:
        raise ReglaNoCumplida('etapa_final', f'La oportunidad ya está {actual}')
    if nueva == actual:
        raise ReglaNoCumplida('misma_etapa', f'Ya está en {actual}')

    i_actual, i_nueva = ETAPAS.index(actual), ETAPAS.index(nueva)
    if nueva != 'perdida':
        if i_nueva < i_actual and rol not in ('gerente', 'admin'):
            raise SinPermiso('retroceso_no_permitido', 'Solo el gerente puede regresar una etapa')
        if i_nueva > i_actual + 1:
            raise ReglaNoCumplida('salto_de_etapa', f'De {actual} solo se avanza a {ETAPAS[i_actual + 1]}')

    if nueva == 'propuesta' and not tiene_cotizacion:
        raise ReglaNoCumplida('falta_cotizacion', 'Para pasar a propuesta se necesita al menos una cotización')
    if nueva == 'ganada' and not tiene_cotizacion_aceptada:
        raise ReglaNoCumplida('falta_cotizacion_aceptada', 'Para ganar se necesita una cotización aceptada')
    if nueva == 'perdida' and not (motivo or '').strip():
        raise ReglaNoCumplida('falta_motivo', 'Indica el motivo de la pérdida')


def monto_ponderado(monto, etapa):
    """Pronóstico: monto x probabilidad de la etapa."""
    return redondear(Decimal(str(monto)) * PROBABILIDAD[etapa])


def validar_descuento(descuento_pct, rol):
    descuento = Decimal(str(descuento_pct or 0))
    if descuento < 0:
        raise DatoInvalido('descuento_invalido', 'El descuento no puede ser negativo')
    maximo = DESCUENTO_MAXIMO.get(rol, Decimal('0'))
    if descuento > maximo:
        raise SinPermiso('descuento_excedido', f'Un {rol} puede dar como máximo {maximo} % de descuento')
    return descuento


def calcular_cotizacion(lineas, descuento_pct, rol):
    """lineas = [(cantidad, precio_unitario_sin_igv), ...]

    subtotal -> menos descuento = base imponible -> + IGV 18 % = total.
    """
    if not lineas:
        raise DatoInvalido('cotizacion_vacia', 'La cotización necesita al menos un producto')
    for cantidad, _ in lineas:
        if not isinstance(cantidad, int) or cantidad <= 0:
            raise DatoInvalido('cantidad_invalida', 'Cada cantidad debe ser un entero mayor que 0')
    descuento_pct = validar_descuento(descuento_pct, rol)
    subtotal = redondear(sum(Decimal(str(precio)) * cantidad for cantidad, precio in lineas))
    descuento = redondear(subtotal * descuento_pct / 100)
    base = subtotal - descuento
    igv = redondear(base * IGV)
    return {
        'descuento_pct': descuento_pct,
        'subtotal': subtotal,
        'descuento': descuento,
        'base_imponible': base,
        'igv': igv,
        'total': base + igv,
    }


def vencimiento_cotizacion(hoy):
    return hoy + datetime.timedelta(days=DIAS_VIGENCIA)


def puede_cotizar(etapa):
    if etapa in FINALES:
        raise ReglaNoCumplida('etapa_final', 'No se cotiza una oportunidad cerrada')


def validar_estado_cotizacion(actual, nuevo, vence_en, hoy):
    if nuevo not in TRANSICIONES_COTIZACION:
        raise DatoInvalido('estado_invalido', f'Estados: {", ".join(TRANSICIONES_COTIZACION)}')
    if nuevo not in TRANSICIONES_COTIZACION[actual]:
        raise ReglaNoCumplida('transicion_invalida', f'Una cotización {actual} no puede pasar a {nuevo}')
    if nuevo == 'aceptada' and hoy > vence_en:
        raise ReglaNoCumplida('cotizacion_vencida', f'Venció el {vence_en.isoformat()}')
