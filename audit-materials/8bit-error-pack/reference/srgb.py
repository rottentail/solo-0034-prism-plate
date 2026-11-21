"""sRGB transfer function — independent derivation from IEC 61966-2-1.

This module deliberately shares no code with the production service
(prismplate/internal/paint).  The piecewise formula is the public sRGB
standard; two numerical realisations are provided:

* ``encode``/``decode``   — IEEE-754 binary64, the same floating-point
  precision class used by every stage under test;
* ``encode_exact``/``decode_exact`` — decimal arithmetic at 50 significant
  digits, used as a continuous, non-quantised reference when estimating how
  much error the transfer step itself contributes.

Domain convention: all straight (non-premultiplied) sRGB values live in
[0.0, 1.0].
"""

from decimal import Decimal, getcontext

import math

getcontext().prec = 50

_SRGB_K0 = 0.04045          # linear-section breakpoint (encode side)
_SRGB_PHI = 12.92          # linear-section slope
_SRGB_GAMMA = 2.4
_SRGB_A = 1.055
_SRGB_B = 0.055
_SRGB_LIN_T = 0.0031308    # linear-section breakpoint (decode side)

_D_K0 = Decimal(str(_SRGB_K0))
_D_PHI = Decimal(str(_SRGB_PHI))
_D_GAMMA = Decimal(str(_SRGB_GAMMA))
_D_A = Decimal(str(_SRGB_A))
_D_B = Decimal(str(_SRGB_B))
_D_LIN_T = Decimal(str(_SRGB_LIN_T))
_D_ONE = Decimal(1)


def decode(c_srgb: float) -> float:
    """sRGB straight value -> linear-light value, binary64."""
    if c_srgb <= _SRGB_K0:
        return c_srgb / _SRGB_PHI
    return ((c_srgb + _SRGB_B) / _SRGB_A) ** _SRGB_GAMMA


def encode(c_linear: float) -> float:
    """linear-light value -> sRGB straight value, binary64 (clamped)."""
    if c_linear < 0.0:
        c_linear = 0.0
    elif c_linear > 1.0:
        c_linear = 1.0
    if c_linear <= _SRGB_LIN_T:
        return c_linear * _SRGB_PHI
    return _SRGB_A * c_linear ** (1.0 / _SRGB_GAMMA) - _SRGB_B


def decode_exact(c_srgb: Decimal) -> Decimal:
    """Decimal-precision counterpart of :func:`decode`."""
    if c_srgb <= _D_K0:
        return c_srgb / _D_PHI
    return ((c_srgb + _D_B) / _D_A) ** _D_GAMMA


def encode_exact(c_linear: Decimal) -> Decimal:
    """Decimal-precision counterpart of :func:`encode`."""
    if c_linear <= _D_LIN_T:
        return c_linear * _D_PHI
    return _D_A * (c_linear ** (_D_ONE / _D_GAMMA)) - _D_B


def encode8(c_srgb: float) -> int:
    """Round an sRGB straight value to the nearest 8-bit code (half away).

    Mirrors the production ``math.Round(x*255)`` tie rule: halves round away
    from zero.  Inputs outside [0,1] are clamped first.
    """
    if c_srgb <= 0.0:
        return 0
    if c_srgb >= 1.0:
        return 255
    return int(math.floor(c_srgb * 255.0 + 0.5))


def decode8(code: int) -> float:
    """Inverse of :func:`encode8` on the code lattice."""
    return code / 255.0
