// Package validate owns structural admission and the shared compilation budget.
package validate

import (
	"fmt"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"regexp"
)

var identifier = regexp.MustCompile(`^[a-zA-Z][a-zA-Z0-9_-]{0,63}$`)

func ID(s string) bool         { return identifier.MatchString(s) }
func finite(x float64) bool    { return !math.IsNaN(x) && !math.IsInf(x, 0) && math.Abs(x) <= 1e6 }
func Unit(x float64) bool      { return finite(x) && x >= 0 && x <= 1 }
func Color(c scene.Color) bool { return Unit(c.R) && Unit(c.G) && Unit(c.B) && Unit(c.A) }
func path(ops []geom.Op) (geom.Path, error) {
	if len(ops) == 0 || len(ops) > 256 {
		return nil, fmt.Errorf("contour operation count must be 1..256")
	}
	open := false
	vertices := 0
	for _, o := range ops {
		n, ok := map[string]int{"M": 1, "L": 1, "Q": 2, "C": 3, "Z": 0}[o.Kind]
		if !ok || len(o.Points) != n {
			return nil, fmt.Errorf("invalid contour verb or arity")
		}
		for _, p := range o.Points {
			if !finite(p.X) || !finite(p.Y) {
				return nil, fmt.Errorf("non-finite contour")
			}
		}
		if o.Kind == "M" {
			if open {
				return nil, fmt.Errorf("close each contour before another M")
			}
			open = true
			vertices = 1
		} else {
			if !open {
				return nil, fmt.Errorf("contour must begin with M")
			}
			if o.Kind == "Z" {
				if vertices < 3 {
					return nil, fmt.Errorf("contour requires three vertices")
				}
				open = false
			} else {
				vertices++
			}
		}
	}
	if open {
		return nil, fmt.Errorf("contour must terminate with Z")
	}
	return geom.Flatten(ops), nil
}

// MeshMinDoubleArea is the smallest admitted doubled triangle area in shape
// local units; below it barycentric interpolation is ill-conditioned.
const MeshMinDoubleArea = 1e-9

// MeshOverlapRatio bounds admitted triangle intersection area relative to the
// smaller triangle, with an absolute floor for degenerate-scale scenes.
const MeshOverlapRatio = 1e-9

func doubleArea(a, b, c geom.Point) float64 {
	return (b.X-a.X)*(c.Y-a.Y) - (b.Y-a.Y)*(c.X-a.X)
}

// clipHalfPlane keeps the part of poly on the interior side of edge a->b;
// sign orients the edge toward the clip triangle interior.
func clipHalfPlane(poly []geom.Point, a, b geom.Point, sign float64) []geom.Point {
	var out []geom.Point
	f := func(p geom.Point) float64 { return sign * ((b.X-a.X)*(p.Y-a.Y) - (b.Y-a.Y)*(p.X-a.X)) }
	for i, s := range poly {
		e := poly[(i+1)%len(poly)]
		fs, fe := f(s), f(e)
		if fe >= 0 {
			if fs < 0 {
				if d := fs - fe; d != 0 {
					u := fs / d
					out = append(out, geom.Point{X: s.X + u*(e.X-s.X), Y: s.Y + u*(e.Y-s.Y)})
				}
			}
			out = append(out, e)
		} else if fs >= 0 {
			if d := fs - fe; d != 0 {
				u := fs / d
				out = append(out, geom.Point{X: s.X + u*(e.X-s.X), Y: s.Y + u*(e.Y-s.Y)})
			}
		}
	}
	return out
}

// intersectionArea is the overlap area of two triangles via convex clipping;
// shared edges or vertices contribute zero area and stay legal.
func intersectionArea(t1, t2 [3]geom.Point) float64 {
	poly := []geom.Point{t2[0], t2[1], t2[2]}
	sign := 1.0
	if doubleArea(t1[0], t1[1], t1[2]) < 0 {
		sign = -1
	}
	for i := 0; i < 3 && len(poly) > 0; i++ {
		poly = clipHalfPlane(poly, t1[i], t1[(i+1)%3], sign)
	}
	if len(poly) < 3 {
		return 0
	}
	area := 0.0
	for i, p := range poly {
		q := poly[(i+1)%len(poly)]
		area += p.X*q.Y - p.Y*q.X
	}
	return math.Abs(area) / 2
}

// mesh admits a vertex-color triangle grid. Triangles must be non-degenerate
// and free of interior overlap, so the sampled color function is single
// valued: no shared-edge cracks and no double compositing are possible.
func mesh(m *scene.Mesh) error {
	if m == nil {
		return fmt.Errorf("mesh brush requires mesh data")
	}
	if len(m.Vertices) < 3 || len(m.Vertices) > 256 {
		return fmt.Errorf("mesh requires 3..256 vertices")
	}
	for _, v := range m.Vertices {
		if !finite(v.X) || !finite(v.Y) || !Color(v.Color) {
			return fmt.Errorf("invalid mesh vertex")
		}
	}
	if len(m.Triangles) < 1 || len(m.Triangles) > 256 {
		return fmt.Errorf("mesh requires 1..256 triangles")
	}
	n := len(m.Vertices)
	tris := make([][3]geom.Point, len(m.Triangles))
	areas := make([]float64, len(m.Triangles))
	for i, t := range m.Triangles {
		if t.A < 0 || t.B < 0 || t.C < 0 || t.A >= n || t.B >= n || t.C >= n {
			return fmt.Errorf("mesh triangle index out of range")
		}
		if t.A == t.B || t.B == t.C || t.A == t.C {
			return fmt.Errorf("mesh triangle indices must be distinct")
		}
		tris[i] = [3]geom.Point{
			{X: m.Vertices[t.A].X, Y: m.Vertices[t.A].Y},
			{X: m.Vertices[t.B].X, Y: m.Vertices[t.B].Y},
			{X: m.Vertices[t.C].X, Y: m.Vertices[t.C].Y},
		}
		da := doubleArea(tris[i][0], tris[i][1], tris[i][2])
		if math.Abs(da) < MeshMinDoubleArea {
			return fmt.Errorf("degenerate mesh triangle")
		}
		areas[i] = math.Abs(da) / 2
	}
	for i := 0; i < len(tris); i++ {
		for j := i + 1; j < len(tris); j++ {
			limit := MeshOverlapRatio * math.Min(areas[i], areas[j])
			if limit < 1e-12 {
				limit = 1e-12
			}
			if intersectionArea(tris[i], tris[j]) > limit {
				return fmt.Errorf("mesh triangles overlap")
			}
		}
	}
	return nil
}
func brush(b scene.Brush) error {
	if b.Kind == "solid" {
		if !Color(b.Color) {
			return fmt.Errorf("invalid solid color")
		}
		return nil
	}
	if b.Kind == "mesh" {
		return mesh(b.Mesh)
	}
	if b.Kind != "linear" {
		return fmt.Errorf("unknown brush")
	}
	for _, v := range []float64{b.From.X, b.From.Y, b.To.X, b.To.Y} {
		if !finite(v) {
			return fmt.Errorf("invalid gradient axis")
		}
	}
	if math.Hypot(b.To.X-b.From.X, b.To.Y-b.From.Y) < 1e-6 {
		return fmt.Errorf("degenerate gradient axis")
	}
	if len(b.Stops) < 2 || len(b.Stops) > 16 {
		return fmt.Errorf("gradient requires 2..16 stops")
	}
	prev := -1.0
	for _, s := range b.Stops {
		if !Unit(s.At) || s.At <= prev || !Color(s.Color) {
			return fmt.Errorf("invalid gradient stops")
		}
		prev = s.At
	}
	if b.Stops[0].At != 0 || prev != 1 {
		return fmt.Errorf("gradient endpoints must be 0 and 1")
	}
	return nil
}
func Compile(d scene.Document) (scene.Compiled, error) {
	c := scene.Compiled{Document: d}
	if d.Width < 1 || d.Height < 1 || d.Width > 512 || d.Height > 512 || !Color(d.Background) {
		return c, fmt.Errorf("invalid canvas (maximum 512 square)")
	}
	if len(d.Layers) < 1 || len(d.Layers) > 16 {
		return c, fmt.Errorf("requires 1..16 layers")
	}
	ids := map[string]bool{}
	add := func(id string) error {
		if !ID(id) || ids[id] {
			return fmt.Errorf("invalid or duplicate node id %q", id)
		}
		ids[id] = true
		return nil
	}
	count := func(p geom.Path) {
		for _, r := range p {
			c.Segments += len(r)
		}
	}
	for _, l := range d.Layers {
		if err := add(l.ID); err != nil {
			return c, err
		}
		if !Unit(l.Opacity) || (l.Blend != "normal" && l.Blend != "multiply" && l.Blend != "screen") {
			return c, fmt.Errorf("invalid layer compositing")
		}
		if len(l.Shapes) < 1 || len(l.Shapes) > 32 {
			return c, fmt.Errorf("requires 1..32 shapes per layer")
		}
		cl := scene.CompiledLayer{Layer: l}
		if len(l.Clip) > 0 {
			p, e := path(l.Clip)
			if e != nil {
				return c, e
			}
			cl.Clip = p
			count(p)
		}
		if len(l.Mask) > 0 {
			p, e := path(l.Mask)
			if e != nil {
				return c, e
			}
			cl.Mask = p
			count(p)
		}
		for _, s := range l.Shapes {
			if err := add(s.ID); err != nil {
				return c, err
			}
			if s.FillRule != "nonzero" && s.FillRule != "evenodd" {
				return c, fmt.Errorf("invalid fill rule")
			}
			for _, v := range s.Transform {
				if !finite(v) {
					return c, fmt.Errorf("invalid transform")
				}
			}
			inv, ok := s.Transform.Inverse()
			if !ok {
				return c, fmt.Errorf("singular transform")
			}
			for _, v := range inv {
				if !finite(v) {
					return c, fmt.Errorf("ill-conditioned transform")
				}
			}
			p, e := path(s.Path)
			if e != nil {
				return c, e
			}
			if e = brush(s.Brush); e != nil {
				return c, e
			}
			count(p)
			// A mesh sample tests each triangle like a short contour; three
			// edge-equivalents per triangle keeps the work budget honest.
			if s.Brush.Kind == "mesh" && s.Brush.Mesh != nil {
				c.Segments += 3 * len(s.Brush.Mesh.Triangles)
			}
			cl.Shapes = append(cl.Shapes, scene.CompiledShape{Shape: s, Path: p, Inverse: inv})
		}
		c.Layers = append(c.Layers, cl)
	}
	// Four samples per pixel; every fill is bounded even under adversarial input.
	if int64(c.Segments)*int64(d.Width)*int64(d.Height)*4 > 160_000_000 {
		return c, fmt.Errorf("scene exceeds bounded rendering work")
	}
	return c, nil
}
