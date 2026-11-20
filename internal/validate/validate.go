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

// minPeriod keeps the tile fold well-conditioned; evaluation cost itself is
// independent of period, so small tiles cannot expand rendering work.
const minPeriod = 0.5

func brush(b scene.Brush) ([]scene.CompiledCell, error) {
	if b.Kind == "solid" || b.Kind == "linear" {
		if b.Pattern != nil {
			return nil, fmt.Errorf("pattern definition requires a pattern brush")
		}
	}
	if b.Kind == "solid" {
		if !Color(b.Color) {
			return nil, fmt.Errorf("invalid solid color")
		}
		return nil, nil
	}
	if b.Kind == "pattern" {
		return pattern(b.Pattern)
	}
	if b.Kind != "linear" {
		return nil, fmt.Errorf("unknown brush")
	}
	for _, v := range []float64{b.From.X, b.From.Y, b.To.X, b.To.Y} {
		if !finite(v) {
			return nil, fmt.Errorf("invalid gradient axis")
		}
	}
	if math.Hypot(b.To.X-b.From.X, b.To.Y-b.From.Y) < 1e-6 {
		return nil, fmt.Errorf("degenerate gradient axis")
	}
	if len(b.Stops) < 2 || len(b.Stops) > 16 {
		return nil, fmt.Errorf("gradient requires 2..16 stops")
	}
	prev := -1.0
	for _, s := range b.Stops {
		if !Unit(s.At) || s.At <= prev || !Color(s.Color) {
			return nil, fmt.Errorf("invalid gradient stops")
		}
		prev = s.At
	}
	if b.Stops[0].At != 0 || prev != 1 {
		return nil, fmt.Errorf("gradient endpoints must be 0 and 1")
	}
	return nil, nil
}

// pattern compiles a vector tile. Every cell must fit within one period per
// axis and bleed past the tile by at most one period, so each point is covered
// by at most one periodic image of a cell and wrapped copies never overlap.
func pattern(p *scene.Pattern) ([]scene.CompiledCell, error) {
	if p == nil {
		return nil, fmt.Errorf("pattern brush requires a tile definition")
	}
	if p.Space != "object" && p.Space != "canvas" {
		return nil, fmt.Errorf("pattern space must be object or canvas")
	}
	if !finite(p.Period.X) || !finite(p.Period.Y) || p.Period.X < minPeriod || p.Period.Y < minPeriod {
		return nil, fmt.Errorf("pattern period must be finite and at least 0.5")
	}
	if !finite(p.Phase.X) || !finite(p.Phase.Y) {
		return nil, fmt.Errorf("invalid pattern phase")
	}
	if len(p.Cells) < 1 || len(p.Cells) > 16 {
		return nil, fmt.Errorf("pattern requires 1..16 cells")
	}
	var out []scene.CompiledCell
	for _, cell := range p.Cells {
		if cell.FillRule != "nonzero" && cell.FillRule != "evenodd" {
			return nil, fmt.Errorf("invalid cell fill rule")
		}
		if !Color(cell.Color) {
			return nil, fmt.Errorf("invalid cell color")
		}
		flat, err := path(cell.Path)
		if err != nil {
			return nil, err
		}
		if len(flat) == 0 {
			return nil, fmt.Errorf("cell contour is empty")
		}
		min, max := flat[0][0], flat[0][0]
		for _, ring := range flat {
			for _, v := range ring {
				min.X, min.Y = math.Min(min.X, v.X), math.Min(min.Y, v.Y)
				max.X, max.Y = math.Max(max.X, v.X), math.Max(max.Y, v.Y)
			}
		}
		if min.X < -p.Period.X || min.Y < -p.Period.Y || max.X > 2*p.Period.X || max.Y > 2*p.Period.Y {
			return nil, fmt.Errorf("cell bleed exceeds one period")
		}
		if max.X-min.X > p.Period.X || max.Y-min.Y > p.Period.Y {
			return nil, fmt.Errorf("cell extent exceeds one period")
		}
		xs := []float64{0}
		if min.X < 0 {
			xs = append(xs, p.Period.X)
		}
		if max.X > p.Period.X {
			xs = append(xs, -p.Period.X)
		}
		ys := []float64{0}
		if min.Y < 0 {
			ys = append(ys, p.Period.Y)
		}
		if max.Y > p.Period.Y {
			ys = append(ys, -p.Period.Y)
		}
		cc := scene.CompiledCell{Cell: cell, Path: flat}
		for _, sx := range xs {
			for _, sy := range ys {
				cc.Shifts = append(cc.Shifts, geom.Point{X: sx, Y: sy})
			}
		}
		out = append(out, cc)
	}
	return out, nil
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
			cells, e := brush(s.Brush)
			if e != nil {
				return c, e
			}
			count(p)
			for _, cell := range cells {
				segments := 0
				for _, ring := range cell.Path {
					segments += len(ring)
				}
				c.Segments += segments * len(cell.Shifts)
			}
			cl.Shapes = append(cl.Shapes, scene.CompiledShape{Shape: s, Path: p, Inverse: inv, Cells: cells})
		}
		c.Layers = append(c.Layers, cl)
	}
	// Four samples per pixel; every fill is bounded even under adversarial input.
	if int64(c.Segments)*int64(d.Width)*int64(d.Height)*4 > 160_000_000 {
		return c, fmt.Errorf("scene exceeds bounded rendering work")
	}
	return c, nil
}
