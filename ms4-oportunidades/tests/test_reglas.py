import datetime
from decimal import Decimal

import pytest

from src import reglas
from src.comun import ReglaNoCumplida, SinPermiso


def cambio(actual, nueva, rol='vendedor', cot=True, aceptada=True, motivo=None):
    reglas.validar_cambio_etapa(actual, nueva, rol, cot, aceptada, motivo)


def test_se_avanza_de_una_en_una():
    cambio('prospeccion', 'calificacion')
    with pytest.raises(ReglaNoCumplida):
        cambio('prospeccion', 'propuesta')


def test_ganada_y_perdida_son_finales():
    for final in ('ganada', 'perdida'):
        with pytest.raises(ReglaNoCumplida):
            cambio(final, 'negociacion', rol='gerente')


def test_solo_el_gerente_retrocede():
    with pytest.raises(SinPermiso):
        cambio('negociacion', 'propuesta')
    cambio('negociacion', 'propuesta', rol='gerente')


def test_requisitos_por_etapa():
    with pytest.raises(ReglaNoCumplida):
        cambio('calificacion', 'propuesta', cot=False)
    with pytest.raises(ReglaNoCumplida):
        cambio('negociacion', 'ganada', aceptada=False)
    with pytest.raises(ReglaNoCumplida):
        cambio('calificacion', 'perdida', motivo='  ')
    cambio('calificacion', 'perdida', motivo='Eligió a la competencia')


def test_monto_ponderado():
    assert reglas.monto_ponderado(10000, 'propuesta') == Decimal('5000.00')
    assert reglas.monto_ponderado(10000, 'perdida') == Decimal('0.00')


def test_calculo_de_cotizacion_con_igv():
    r = reglas.calcular_cotizacion([(2, Decimal('1200')), (10, Decimal('150'))], 10, 'vendedor')
    assert r['subtotal'] == Decimal('3900.00')
    assert r['descuento'] == Decimal('390.00')
    assert r['base_imponible'] == Decimal('3510.00')
    assert r['igv'] == Decimal('631.80')
    assert r['total'] == Decimal('4141.80')


def test_descuento_maximo_por_rol():
    with pytest.raises(SinPermiso):
        reglas.calcular_cotizacion([(1, 100)], 15, 'vendedor')
    assert reglas.calcular_cotizacion([(1, 100)], 25, 'gerente')['descuento'] == Decimal('25.00')


def test_cotizacion_vencida_no_se_acepta():
    hoy = datetime.date(2026, 11, 20)
    with pytest.raises(ReglaNoCumplida):
        reglas.validar_estado_cotizacion('enviada', 'aceptada', datetime.date(2026, 11, 19), hoy)
    reglas.validar_estado_cotizacion('enviada', 'aceptada', datetime.date(2026, 11, 20), hoy)


def test_borrador_no_se_acepta_sin_enviar():
    with pytest.raises(ReglaNoCumplida):
        reglas.validar_estado_cotizacion('borrador', 'aceptada', datetime.date(2026, 12, 1), datetime.date(2026, 11, 1))
