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
func Sample(b scene.Brush, p geom.Point) Pixel {
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

// MeshTol is the dimensionless barycentric membership tolerance. A sample
// point is owned by the first triangle (mesh order) whose weights all exceed
// -MeshTol, so points on shared edges resolve to exactly one triangle and no
// point inside the union falls between triangles.
const MeshTol = 1e-9

type meshTri struct {
	x0, y0     float64
	e1x, e1y   float64
	e2x, e2y   float64
	invDen     float64
	minX, minY float64
	maxX, maxY float64
	c0, c1, c2 Pixel
}

// Mesh is the compiled form of scene.Mesh: barycentric constants, per-triangle
// bounds and premultiplied linear vertex colors, all fixed at compile time.
type Mesh struct{ tris []meshTri }

// CompileMesh assumes the scene mesh passed admission; it is pure and
// deterministic, so PNG, SVG and any future renderer share one color function.
func CompileMesh(m *scene.Mesh) *Mesh {
	out := &Mesh{tris: make([]meshTri, 0, len(m.Triangles))}
	for _, t := range m.Triangles {
		a, b, c := m.Vertices[t.A], m.Vertices[t.B], m.Vertices[t.C]
		e1x, e1y := b.X-a.X, b.Y-a.Y
		e2x, e2y := c.X-a.X, c.Y-a.Y
		den := e1x*e2y - e1y*e2x
		if den == 0 {
			continue
		}
		tr := meshTri{x0: a.X, y0: a.Y, e1x: e1x, e1y: e1y, e2x: e2x, e2y: e2y, invDen: 1 / den,
			minX: math.Min(a.X, math.Min(b.X, c.X)), maxX: math.Max(a.X, math.Max(b.X, c.X)),
			minY: math.Min(a.Y, math.Min(b.Y, c.Y)), maxY: math.Max(a.Y, math.Max(b.Y, c.Y)),
			c0: From(a.Color), c1: From(b.Color), c2: From(c.Color)}
		out.tris = append(out.tris, tr)
	}
	return out
}

// Sample evaluates the mesh color function. Barycentric interpolation runs in
// premultiplied linear light, so translucent vertices and sliver triangles
// have stable, order-independent results; outside the union it is transparent.
func (m *Mesh) Sample(p geom.Point) Pixel {
	for i := range m.tris {
		t := &m.tris[i]
		if p.X < t.minX || p.X > t.maxX || p.Y < t.minY || p.Y > t.maxY {
			continue
		}
		dx, dy := p.X-t.x0, p.Y-t.y0
		w1 := (dx*t.e2y - dy*t.e2x) * t.invDen
		w2 := (t.e1x*dy - t.e1y*dx) * t.invDen
		w0 := 1 - w1 - w2
		if w0 < -MeshTol || w1 < -MeshTol || w2 < -MeshTol {
			continue
		}
		w0 = math.Max(0, math.Min(1, w0))
		w1 = math.Max(0, math.Min(1, w1))
		w2 = math.Max(0, math.Min(1, w2))
		return Pixel{
			R: w0*t.c0.R + w1*t.c1.R + w2*t.c2.R,
			G: w0*t.c0.G + w1*t.c1.G + w2*t.c2.G,
			B: w0*t.c0.B + w1*t.c1.B + w2*t.c2.B,
			A: w0*t.c0.A + w1*t.c1.A + w2*t.c2.A,
		}
	}
	return Pixel{}
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
