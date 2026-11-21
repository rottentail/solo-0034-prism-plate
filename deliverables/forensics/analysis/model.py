"""Forward compositing model, transcribed 1:1 from internal/paint/paint.go.

The production renderer works in linear light with premultiplied alpha and only
quantizes to 8-bit sRGB at PNG export. Every inversion in this kit is written in
the same linear space, so the equations here are the ground truth used both to
predict observations and to interpret measured PNG pixels.
"""


def linear(v: float) -> float:
    if v <= 0.04045:
        return v / 12.92
    return ((v + 0.055) / 1.055) ** 2.4


def srgb(v: float) -> float:
    v = max(0.0, min(1.0, v))
    if v <= 0.0031308:
        return v * 12.92
    return 1.055 * v ** (1 / 2.4) - 0.055


def from_color(r, g, b, a):
    """paint.From: premultiplied linear-light pixel from straight sRGB color."""
    return (linear(r) * a, linear(g) * a, linear(b) * a, a)


def scale(p, k):
    return (p[0] * k, p[1] * k, p[2] * k, p[3] * k)


def over(dst, src, mode):
    """paint.Over, including the src.A==0 / dst.A==0 normal fall-back."""
    sa, da = src[3], dst[3]

    def blend(d, s):
        if mode == "normal" or sa == 0 or da == 0:
            return s + d * (1 - sa)
        a, b = d / da, s / sa
        if mode == "multiply":
            v = a * b
        elif mode == "screen":
            v = a + b - a * b
        else:  # pragma: no cover - mirrors the validate admission set
            raise ValueError(f"unsupported blend mode {mode!r}")
        return (1 - sa) * d + (1 - da) * s + sa * da * v

    return (
        blend(dst[0], src[0]),
        blend(dst[1], src[1]),
        blend(dst[2], src[2]),
        sa + da * (1 - sa),
    )


def rgba8(p):
    """paint.Pixel.RGBA8: deterministic rounding used by the PNG encoder."""
    if p[3] <= 0:
        return (0, 0, 0, 0)
    return (
        round8(srgb(p[0] / p[3])),
        round8(srgb(p[1] / p[3])),
        round8(srgb(p[2] / p[3])),
        round8(min(1.0, p[3])),
    )


def round8(v: float) -> int:
    # Python round() is banker's rounding; Go math.Round rounds half away from 0.
    import math

    return int(math.floor(v * 255 + 0.5))


def composite(srgb_color, brush_alpha, opacity, mode, bg_srgb, bg_alpha=1.0):
    """Full interior-pixel pipeline for one solid full-coverage layer.

    The effective source coverage is brush_alpha * layer opacity (raster.go
    scales the premultiplied layer pixel by Layer.Opacity). Backgrounds default
    to opaque; bg_alpha < 1 models the translucent-alpha probe.
    """
    r, g, b = srgb_color
    src = from_color(r, g, b, brush_alpha)
    src = scale(src, opacity)
    dst = from_color(*bg_srgb, bg_alpha)
    out = over(dst, src, mode)
    return rgba8(out), out


def decode8(code: int) -> float:
    """Exact sRGB value represented by an 8-bit channel code."""
    return code / 255.0


def lin8(code: int) -> float:
    return linear(decode8(code))


def code_interval(measured: int):
    """All sRGB values that quantize to the measured 8-bit code (closed bounds)."""
    if measured == 0:
        lo = 0.0
    else:
        lo = (measured - 0.5) / 255.0
    if measured == 255:
        hi = 1.0
    else:
        hi = (measured + 0.5) / 255.0
    return max(0.0, lo), min(1.0, hi)
