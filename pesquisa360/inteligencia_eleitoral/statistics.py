"""Calculo estatistico puro do Potencial de Crescimento (Prompt 03).

Somente funcoes puras e deterministicas, sem banco e sem HTTP.

Metodo de incerteza do MVP: WILSON_AAS_APPROX — intervalo de Wilson para
proporcao, como APROXIMACAO sob hipotese de Amostragem Aleatoria Simples.
O sistema nao dispoe de estratos, clusters, PSU ou desenho complexo, e os
intervalos NUNCA devem ser apresentados como erro correto de desenho
complexo (D07).

Este modulo NAO calcula significancia: nenhum p-value, z-test, qui-quadrado
ou flag de "significativo". A referencia do produto contem o proprio
segmento (nao sao amostras independentes), entao um teste de duas proporcoes
independentes seria metodologicamente invalido aqui.

Todas as taxas sao fracoes 0-1 em Decimal; arredondamento e apresentacao e
nao acontece aqui.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Optional

from pesquisa360.inteligencia_eleitoral.results import (
    FavorableDirection,
    ObservedDirection,
    StatisticalInterval,
)

WILSON_METHOD = "WILSON_AAS_APPROX"

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)


def z_value(confidence_level: Decimal | float) -> Decimal:
    """Quantil normal padrao para o intervalo bilateral (ex.: 0.95 -> ~1.95996).

    Usa a aproximacao racional de Acklam para a inversa da Normal padrao
    (erro absoluto ~1.15e-9), suficiente para o intervalo aproximado do MVP.
    """
    level = float(confidence_level)
    if not (0.0 < level < 1.0):
        raise ValueError("confidence_level deve estar entre 0 e 1 (exclusivos)")
    p = 1.0 - (1.0 - level) / 2.0

    # Coeficientes de Acklam.
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)
    p_low, p_high = 0.02425, 1.0 - 0.02425

    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        z = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        z = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
        )
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        z = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    return Decimal(repr(z))


def wilson_interval(
    numerator: int,
    base_n: int,
    confidence_level: Decimal | float,
) -> Optional[StatisticalInterval]:
    """Intervalo de Wilson em fracoes 0-1. `base_n == 0` -> indisponivel (None).

    center = (p + z^2/(2n)) / (1 + z^2/n)
    half   = z / (1 + z^2/n) * sqrt(p(1-p)/n + z^2/(4n^2))
    """
    if base_n <= 0:
        return None
    if not (0 <= numerator <= base_n):
        raise ValueError("numerador fora do intervalo [0, base_n]")

    n = Decimal(base_n)
    p = Decimal(numerator) / n
    z = z_value(confidence_level)
    z2 = z * z

    denominator = ONE + z2 / n
    center = (p + z2 / (2 * n)) / denominator
    radicand = p * (ONE - p) / n + z2 / (4 * n * n)
    half_width = (z / denominator) * radicand.sqrt()

    low = center - half_width
    high = center + half_width
    # O intervalo e uma proporcao: recorta aos limites logicos [0, 1]. Nos
    # extremos p=0 e p=1 os limites de Wilson sao EXATOS (0 e 1); o atalho
    # evita residuo de arredondamento Decimal (0.999...9).
    low = ZERO if numerator == 0 else max(ZERO, low)
    high = ONE if numerator == base_n else min(ONE, high)
    return StatisticalInterval(
        method=WILSON_METHOD,
        confidence_level=Decimal(str(confidence_level)),
        low=low,
        high=high,
    )


def rate(numerator: int, base_n: int) -> Optional[Decimal]:
    """Fracao 0-1 em Decimal; base zero -> indisponivel (None), nunca zero."""
    if base_n <= 0:
        return None
    return Decimal(numerator) / Decimal(base_n)


def delta_pp(segment_rate: Decimal, reference_rate: Decimal) -> Decimal:
    """Diferenca em PONTOS PERCENTUAIS: (0.18 - 0.10) -> +8.0 pp."""
    return (segment_rate - reference_rate) * HUNDRED


def lift(segment_rate: Decimal, reference_rate: Decimal) -> Optional[Decimal]:
    """Razao adimensional; referencia zero -> None (nunca infinito)."""
    if reference_rate == ZERO:
        return None
    return segment_rate / reference_rate


def observed_direction(
    delta: Decimal, favorable: FavorableDirection
) -> ObservedDirection:
    """Direcao OBSERVADA descritiva — nao e significancia nem inferencia."""
    if delta == ZERO:
        return ObservedDirection.NEUTRAL
    if favorable == FavorableDirection.HIGHER_IS_FAVORABLE:
        return (
            ObservedDirection.FAVORABLE if delta > ZERO else ObservedDirection.UNFAVORABLE
        )
    return (
        ObservedDirection.FAVORABLE if delta < ZERO else ObservedDirection.UNFAVORABLE
    )
