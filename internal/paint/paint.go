// Package paint implements linear-light, premultiplied-alpha compositing.
package paint

import (
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

type Pixel struct{ R, G, B, A float64 }

func linear(v float64) float64 {
	if v <= 0.04045 {
		return v / 12.92
	}
	return math.Pow((v+0.055)/1.055, 2.4)
}
func srgb(v float64) float64 {
	v = math.Max(0, math.Min(1, v))
	if v <= 0.0031308 {
		return v * 12.92
	}
	return 1.055*math.Pow(v, 1/2.4) - 0.055
}
func From(c scene.Color) Pixel {
	return Pixel{linear(c.R) * c.A, linear(c.G) * c.A, linear(c.B) * c.A, c.A}
}
func (p Pixel) Scale(a float64) Pixel { return Pixel{p.R * a, p.G * a, p.B * a, p.A * a} }
func (p Pixel) RGBA8() (uint8, uint8, uint8, uint8) {
	if p.A <= 0 {
		return 0, 0, 0, 0
	}
	return uint8(math.Round(srgb(p.R/p.A) * 255)), uint8(math.Round(srgb(p.G/p.A) * 255)), uint8(math.Round(srgb(p.B/p.A) * 255)), uint8(math.Round(math.Min(1, p.A) * 255))
}
func mix(a, b, t float64) float64 { return a + (b-a)*t }

// SpotAlpha returns only the geometric/brush alpha of a spot brush sample; the
// ink hue lives in the shared spot declaration, not in the coverage plate.
func SpotAlpha(b scene.Brush, p geom.Point) float64 {
	if b.Kind == "solid" {
		return b.Color.A
	}
	dx, dy := b.To.X-b.From.X, b.To.Y-b.From.Y
	t := ((p.X-b.From.X)*dx + (p.Y-b.From.Y)*dy) / (dx*dx + dy*dy)
	t = math.Max(0, math.Min(1, t))
	for i := 1; i < len(b.Stops); i++ {
		a, z := b.Stops[i-1], b.Stops[i]
		if t <= z.At {
			u := (t - a.At) / (z.At - a.At)
			return mix(a.Color.A, z.Color.A, u)
		}
	}
	return b.Stops[len(b.Stops)-1].Color.A
}

// Sample renders a brush for the composite RGB proof. Spot brushes resolve their
// hue from the document-level shared spot table so every use stays identical;
// only the brush alpha (solid alpha or gradient-stop alphas) modulates it.
func Sample(b scene.Brush, p geom.Point, spots map[string]scene.Color) Pixel {
	if b.Spot != "" {
		return From(spots[b.Spot]).Scale(SpotAlpha(b, p))
	}
	return sampleRGB(b, p)
}

func sampleRGB(b scene.Brush, p geom.Point) Pixel {
	if b.Kind == "solid" {
		return From(b.Color)
	}
	dx, dy := b.To.X-b.From.X, b.To.Y-b.From.Y
	t := ((p.X-b.From.X)*dx + (p.Y-b.From.Y)*dy) / (dx*dx + dy*dy)
	t = math.Max(0, math.Min(1, t))
	for i := 1; i < len(b.Stops); i++ {
		a, z := b.Stops[i-1], b.Stops[i]
		if t <= z.At {
			u := (t - a.At) / (z.At - a.At)
			x, y := From(a.Color), From(z.Color)
			return Pixel{mix(x.R, y.R, u), mix(x.G, y.G, u), mix(x.B, y.B, u), mix(x.A, y.A, u)}
		}
	}
	return From(b.Stops[len(b.Stops)-1].Color)
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

// InkOver stacks one physical ink film over another. Knockout regions use
// source-over (the top ink replaces the substrate); overlap regions where both
// films print use a premultiplied multiply, the linear-light approximation of
// transparent ink overprint documented in the separation manifest.
func InkOver(dst, src Pixel, overprint bool) Pixel {
	if !overprint {
		return Over(dst, src, "normal")
	}
	if src.A == 0 {
		return dst
	}
	if dst.A == 0 {
		return src
	}
	dR, dG, dB := dst.R/dst.A, dst.G/dst.A, dst.B/dst.A
	sR, sG, sB := src.R/src.A, src.G/src.A, src.B/src.A
	mR, mG, mB := dR*sR, dG*sG, dB*sB
	outA := src.A + dst.A*(1-src.A)
	return Pixel{
		(1-src.A)*dst.R + (1-dst.A)*src.R + src.A*dst.A*mR,
		(1-src.A)*dst.G + (1-dst.A)*src.G + src.A*dst.A*mG,
		(1-src.A)*dst.B + (1-dst.A)*src.B + src.A*dst.A*mB,
		outA,
	}
}
