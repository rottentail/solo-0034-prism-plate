"""Premultiplied linear-light compositing, independently implemented.

Reference basis: Porter & Duff (1984) source-over, plus the W3C compositing
specification's separable blend equations for ``multiply`` and ``screen``
applied to unpremultiplied linear-light colour channels.  No symbol from the
production ``paint`` package is imported; formulas are re-derived here.
"""

from dataclasses import dataclass

from . import srgb


@dataclass(frozen=True)
class Straight:
    """Unpremremultiplied sRGB colour plus coverage alpha, all in [0,1]."""

    r: float
    g: float
    b: float
    a: float


@dataclass(frozen=True)
class Premul:
    """Premultiplied linear-light pixel (R*a, G*a, B*a, a)."""

    r: float
    g: float
    b: float
    a: float

    @classmethod
    def from_straight(cls, c: Straight) -> "Premul":
        if c.a <= 0.0:
            return cls(0.0, 0.0, 0.0, 0.0)
        return cls(
            srgb.decode(c.r) * c.a,
            srgb.decode(c.g) * c.a,
            srgb.decode(c.b) * c.a,
            c.a,
        )

    def scale_alpha(self, factor: float) -> "Premul":
        return Premul(self.r * factor, self.g * factor,
                      self.b * factor, self.a * factor)

    def unpremul(self) -> Straight:
        """Inverse to :meth:`from_straight` through the binary64 transfer."""
        if self.a <= 0.0:
            # Production convention: fully transparent pixels carry (0,0,0,0).
            return Straight(0.0, 0.0, 0.0, 0.0)
        return Straight(
            srgb.encode(self.r / self.a),
            srgb.encode(self.g / self.a),
            srgb.encode(self.b / self.a),
            min(1.0, self.a),
        )


def over(dst: Premul, src: Premul, mode: str = "normal") -> Premul:
    """Source-over with optional separable blend in linear light.

    For normal mode the premultiplied form collapses to
    ``out = src + dst*(1-src.a)``.  For multiply/screen the unpremultiplied
    backdrop/source colours are blended first, then recombined with the
    general coverage expression
    ``co = (1-as)*cs_under + (1-ad)*cd_under + as*ad*B(cd,cs)``.
    """
    as_, ad = src.a, dst.a
    out_a = as_ + ad * (1.0 - as_)

    def channel(d: float, s: float) -> float:
        if mode == "normal" or as_ == 0.0 or ad == 0.0:
            return s + d * (1.0 - as_)
        cd, cs = d / ad, s / as_
        if mode == "multiply":
            blended = cd * cs
        elif mode == "screen":
            blended = cd + cs - cd * cs
        else:
            raise ValueError(f"unsupported blend mode: {mode}")
        return (1.0 - as_) * d + (1.0 - ad) * s + as_ * ad * blended

    return Premul(channel(dst.r, src.r), channel(dst.g, src.g),
                  channel(dst.b, src.b), out_a)


def flatten_over_backdrop(pixel: Premul, backdrop: Straight) -> Straight:
    """Composite a semi-transparent pixel over an *opaque* backdrop.

    This is the only physically meaningful way to read the colour of a
    low-alpha pixel: the answer is a property of (pixel, backdrop), not of
    the pixel's straight RGB alone.
    """
    bd = Premul.from_straight(backdrop)
    out = over(bd, pixel, "normal")
    return out.unpremul()
