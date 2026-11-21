// Package paint implements linear-light, premultiplied-alpha compositing.
//
// Two color models are supported and are never mixed inside one document:
//
//   - scene.StraightSRGB (legacy prism-plate-v1, ColorModel omitted): color
//     components are sRGB-encoded and straight. They are linearized and
//     premultiplied at sample time, then interpolated in premultiplied linear
//     light.
//   - scene.LinearPremul (prism-plate-v2): color components are already
//     linear-light and premultiplied, so they are consumed as-is.
//
// The compositor, and therefore all blending math, only ever sees Pixel, which
// is linear-light and premultiplied in both models.
package paint

import (
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

// Pixel is a linear-light, premultiplied RGBA value.
type Pixel struct{ R, G, B, A float64 }

// Linear decodes one sRGB-encoded component to linear light.
func Linear(v float64) float64 {
	if v <= 0.04045 {
		return v / 12.92
	}
	return math.Pow((v+0.055)/1.055, 2.4)
}

// SRGB encodes one linear-light component to sRGB.
func SRGB(v float64) float64 {
	v = math.Max(0, math.Min(1, v))
	if v <= 0.0031308 {
		return v * 12.92
	}
	return 1.055*math.Pow(v, 1/2.4) - 0.055
}

// Premultiplied converts a legacy straight sRGB color to the linear-light,
// premultiplied components of prism-plate-v2. A fully transparent color has
// no premultiplied light, so its stored RGB becomes zero and any hidden RGB is
// not representable in the target model.
func Premultiplied(c scene.Color) scene.Color {
	a := math.Max(0, math.Min(1, c.A))
	return scene.Color{R: Linear(math.Max(0, math.Min(1, c.R))) * a, G: Linear(math.Max(0, math.Min(1, c.G))) * a, B: Linear(math.Max(0, math.Min(1, c.B))) * a, A: a}
}

// Straight recovers a straight sRGB color from a stored color under model.
// For scene.StraightSRGB this is the identity. For scene.LinearPremul it
// de-premultiplies and re-encodes, which is defined only for A>0: at A==0 no
// color is carried, so zero is returned instead of inventing one.
func Straight(c scene.Color, m scene.Model) scene.Color {
	if m == scene.StraightSRGB {
		return c
	}
	if c.A <= 0 {
		return scene.Color{}
	}
	return scene.Color{R: SRGB(c.R / c.A), G: SRGB(c.G / c.A), B: SRGB(c.B / c.A), A: c.A}
}

// From converts a scene color under the given model into a premultiplied
// linear Pixel. StraightSRGB input is clamped to [0,1] before linearization;
// LinearPremul input is already premultiplied, so each component must not
// exceed its alpha for the color to be representable.
func From(c scene.Color, m scene.Model) Pixel {
	if m == scene.LinearPremul {
		return Pixel{c.R, c.G, c.B, c.A}
	}
	p := Premultiplied(c)
	return Pixel{p.R, p.G, p.B, p.A}
}
func (p Pixel) Scale(a float64) Pixel { return Pixel{p.R * a, p.G * a, p.B * a, p.A * a} }
func (p Pixel) RGBA8() (uint8, uint8, uint8, uint8) {
	if p.A <= 0 {
		return 0, 0, 0, 0
	}
	return uint8(math.Round(SRGB(p.R/p.A) * 255)), uint8(math.Round(SRGB(p.G/p.A) * 255)), uint8(math.Round(SRGB(p.B/p.A) * 255)), uint8(math.Round(math.Min(1, p.A) * 255))
}
func mix(a, b, t float64) float64 { return a + (b-a)*t }

// Sample interpolates a brush at a point in the shape's local coordinates.
// Stops are interpolated in premultiplied linear light under both models;
// only the conversion from stored components differs.
func Sample(b scene.Brush, m scene.Model, p geom.Point) Pixel {
	if b.Kind == "solid" {
		return From(b.Color, m)
	}
	dx, dy := b.To.X-b.From.X, b.To.Y-b.From.Y
	den := dx*dx + dy*dy
	if den == 0 {
		return From(b.Stops[0].Color, m)
	}
	t := ((p.X-b.From.X)*dx + (p.Y-b.From.Y)*dy) / den
	t = math.Max(0, math.Min(1, t))
	for i := 1; i < len(b.Stops); i++ {
		a, z := b.Stops[i-1], b.Stops[i]
		if t <= z.At {
			u := 0.0
			if z.At > a.At {
				u = (t - a.At) / (z.At - a.At)
			}
			x, y := From(a.Color, m), From(z.Color, m)
			return Pixel{mix(x.R, y.R, u), mix(x.G, y.G, u), mix(x.B, y.B, u), mix(x.A, y.A, u)}
		}
	}
	return From(b.Stops[len(b.Stops)-1].Color, m)
}
func Over(dst, src Pixel, mode string) Pixel {
	blend := func(d, s float64) float64 {
		if mode == "normal" || src.A == 0 || dst.A == 0 {
			return s + d*(1-src.A)
		}
		a, b := d/dst.A, s/src.A
		v := a * b
		if mode == "screen" {
			v = a + b - a*b
		}
		return (1-src.A)*d + (1-dst.A)*s + src.A*dst.A*v
	}
	return Pixel{blend(dst.R, src.R), blend(dst.G, src.G), blend(dst.B, src.B), src.A + dst.A*(1-src.A)}
}
