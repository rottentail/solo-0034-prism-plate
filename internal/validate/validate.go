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
func brush(b scene.Brush) error {
	if b.Kind == "solid" {
		if !Color(b.Color) {
			return fmt.Errorf("invalid solid color")
		}
		return nil
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

// maxRenderEdgeTests keeps the per-pixel-sample edge traversal bounded: four
// coverage samples per pixel, only over edges that can reach a sample. The
// limit is unchanged, so any admitted scene still has the same worst-case
// rendering work as before.
const maxRenderEdgeTests = 160_000_000

// maxFlattenedSegments bounds one-time geometry work (flatten, SVG export and
// persistence) that provably invisible contours still incur. Structural limits
// allow at most 16 layers x (32 shapes + clip + mask) = 544 paths, each built
// from at most 256 verbs where a cubic contributes 24 edges; the cap sits above
// the structural maximum so it never rejects an otherwise admissible scene.
const maxFlattenedSegments = 3_500_000

// viewPad is the conservative separation margin for a canvas-vs-bbox test.
// Coordinates are bounded by finite() at 1e6; at that scale a 1e-12 relative
// margin still dwarfs float64 rounding of the affine map, while remaining
// sub-pixel at normal canvas coordinates. Geometry touching the frame can
// therefore never be mistaken for off-canvas geometry.
func viewPad(b geom.BBox) float64 {
	s := math.Max(math.Abs(b.MinX), math.Max(math.Abs(b.MinY), math.Max(math.Abs(b.MaxX), math.Abs(b.MaxY))))
	return 1e-9 + 1e-12*s
}
func pathLen(p geom.Path) int {
	n := 0
	for _, r := range p {
		n += len(r)
	}
	return n
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
	// Segments counts all flattened edges (one-time work); visible counting is
	// layered on top once each bbox can be tested against the canvas.
	count := func(p geom.Path) {
		c.Segments += pathLen(p)
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
		cl := scene.CompiledLayer{Layer: l, Visible: true}
		// Clip and mask live in document coordinates and gate every sample. A
		// bbox strictly outside the canvas makes every per-sample containment
		// test false, so the whole layer is provably invisible.
		gatesVisible := true
		if len(l.Clip) > 0 {
			p, e := path(l.Clip)
			if e != nil {
				return c, e
			}
			cl.Clip = p
			count(p)
			if b, ok := p.Bounds(); ok && b.OutsideView(d.Width, d.Height, viewPad(b)) {
				gatesVisible = false
			}
		}
		if len(l.Mask) > 0 {
			p, e := path(l.Mask)
			if e != nil {
				return c, e
			}
			cl.Mask = p
			count(p)
			if b, ok := p.Bounds(); ok && b.OutsideView(d.Width, d.Height, viewPad(b)) {
				gatesVisible = false
			}
		}
		anyShapeVisible := false
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
			// The fill is contained in the local vertex bbox; mapping its four
			// corners through the declared affine transform gives an exact
			// transformed bound. A layer already hidden by its gates hides
			// every shape regardless of its own box.
			visible := false
			if gatesVisible {
				if b, ok := p.Bounds(); ok {
					tb := s.Transform.TransformBounds(b)
					visible = !tb.OutsideView(d.Width, d.Height, viewPad(tb))
				}
			}
			if visible {
				anyShapeVisible = true
				c.VisibleSegments += pathLen(p)
			}
			cl.Shapes = append(cl.Shapes, scene.CompiledShape{Shape: s, Path: p, Inverse: inv, Visible: visible})
		}
		// The rasterizer only traverses gate edges when a layer can actually show.
		cl.Visible = gatesVisible && anyShapeVisible
		if cl.Visible {
			c.VisibleSegments += pathLen(cl.Clip) + pathLen(cl.Mask)
		}
		c.Layers = append(c.Layers, cl)
	}
	// Four samples per pixel; only provably visible fills are traversed, so
	// off-canvas geometry no longer consumes the same budget as visible
	// contours while a single canvas-spanning complex curve still pays in full.
	if int64(c.VisibleSegments)*int64(d.Width)*int64(d.Height)*4 > maxRenderEdgeTests {
		return c, fmt.Errorf("scene exceeds bounded rendering work")
	}
	// One-time flatten/export work stays bounded even when nothing is visible.
	if c.Segments > maxFlattenedSegments {
		return c, fmt.Errorf("scene exceeds bounded geometry work")
	}
	return c, nil
}
