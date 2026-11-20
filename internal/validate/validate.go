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

// compileProjective builds the local -> display-plane homography for a shape
// with an explicit projective quad. Source defaults to the artwork's bounding
// quad in transformed space, so a plain illustration can be pasted onto a
// display face without restating its extent. The path passes through the
// shape's affine Transform first, then the source quad maps onto the display
// quad.
func compileProjective(s scene.Shape, p geom.Path, inv geom.Matrix) (scene.CompiledShape, error) {
	out := scene.CompiledShape{Shape: s}
	transform := geom.Homography{s.Transform[0], s.Transform[2], s.Transform[4], s.Transform[1], s.Transform[3], s.Transform[5], 0, 0, 1}
	// Work in local coordinates: a source corner is transformed before being
	// mapped, so pre-images are expressed in the same space as the path.
	toLocal := geom.Homography{inv[0], inv[2], inv[4], inv[1], inv[3], inv[5], 0, 0, 1}
	var src [4]geom.Point
	if n := len(s.Projective.Source); n > 0 {
		if n != 4 {
			return out, fmt.Errorf("projective source requires four corners")
		}
		copy(src[:], s.Projective.Source)
		for _, q := range src {
			if !finite(q.X) || !finite(q.Y) {
				return out, fmt.Errorf("invalid projective source")
			}
		}
	} else {
		var ok bool
		src, ok = transformedBoundsQuad(p, transform)
		if !ok {
			return out, fmt.Errorf("projective shape has no extent to map")
		}
	}
	srcLocal := [4]geom.Point{}
	for i, q := range src {
		v, ok := toLocal.Apply(q)
		if !ok {
			return out, fmt.Errorf("projective source is unreachable through transform")
		}
		srcLocal[i] = v
	}
	dst := s.Projective.Quad
	for _, q := range dst {
		if !finite(q.X) || !finite(q.Y) {
			return out, fmt.Errorf("invalid projective quad")
		}
	}
	if !geom.QuadUsable(src) {
		return out, fmt.Errorf("projective source quad is degenerate or self-intersecting")
	}
	// A convex, non-self-intersecting display face is what keeps the
	// homography finite: its preimage can never straddle the horizon line.
	if !geom.QuadUsable(dst) {
		return out, fmt.Errorf("projective quad is degenerate or self-intersecting")
	}
	h, ok := geom.QuadHomography(src, dst)
	if !ok {
		return out, fmt.Errorf("projective mapping is singular")
	}
	if !sameSide(h, src) {
		return out, fmt.Errorf("projective display face crosses the horizon")
	}
	h = geom.Compose(h, transform)
	invH, ok := h.Inverse()
	if !ok {
		return out, fmt.Errorf("projective mapping is singular")
	}
	projected, ok := geom.ProjectPath(p, h)
	if !ok {
		return out, fmt.Errorf("projective mapping produces unbounded coordinates")
	}
	approx, ok := geom.AffineApprox(h, srcLocal)
	if !ok {
		approx, ok = globalAffine(h)
		if !ok {
			return out, fmt.Errorf("projective mapping has no stable affine approximation")
		}
	}
	if s.Projective.Affine {
		// Comparison mode: coverage, sampling and exported geometry all use
		// the same best-fit affine map, so the difference against the true
		// perspective shape is purely geometric.
		ah := geom.Homography{approx[0], approx[2], approx[4], approx[1], approx[3], approx[5], 0, 0, 1}
		ainv, ok := ah.Inverse()
		if !ok {
			return out, fmt.Errorf("affine approximation is singular")
		}
		ap, ok := geom.ProjectPath(p, ah)
		if !ok {
			return out, fmt.Errorf("affine approximation produces unbounded coordinates")
		}
		out.PlaneToLocal = ainv
		out.LocalToPlane = ah
		out.ProjectedPath = ap
		// The approximation is not the display face: its corners drift off
		// the quad, so it must not be clipped to the perspective quad or the
		// comparison would hide exactly the difference it exists to show.
		out.FaceSet = false
	} else {
		out.PlaneToLocal = invH
		out.LocalToPlane = h
		out.ProjectedPath = projected
		out.Face = dst
		out.FaceSet = true
	}
	out.Path = p
	out.AffineApprox = approx
	out.Projective = true
	return out, nil
}

func sameSide(h geom.Homography, q [4]geom.Point) bool {
	first := 0.0
	for i, c := range q {
		w := h[6]*c.X + h[7]*c.Y + h[8]
		if !finite(w) || math.Abs(w) <= 1e-9 {
			return false
		}
		if i == 0 {
			first = math.Copysign(1, w)
		} else if math.Copysign(1, w) != first {
			return false
		}
	}
	return true
}

// globalAffine exposes the affine part of a homography in affine matrix form.
// Normalization keeps h[8] == 1 whenever that is numerically safe; when it is
// not, the rows are rescaled here so the returned matrix still means what it
// says rather than a scaled copy.
func globalAffine(h geom.Homography) (geom.Matrix, bool) {
	w := h[8]
	if w == 0 || math.Abs(w) < 1e-12 || !finite(w) {
		return geom.Matrix{}, false
	}
	m := geom.Matrix{h[0] / w, h[3] / w, h[1] / w, h[4] / w, h[2] / w, h[5] / w}
	for _, v := range m {
		if !finite(v) {
			return geom.Matrix{}, false
		}
	}
	if _, ok := m.Inverse(); !ok {
		return geom.Matrix{}, false
	}
	return m, true
}

func transformedBoundsQuad(p geom.Path, t geom.Homography) ([4]geom.Point, bool) {
	minX, minY := math.Inf(1), math.Inf(1)
	maxX, maxY := math.Inf(-1), math.Inf(-1)
	for _, r := range p {
		for _, v := range r {
			q, ok := t.Apply(v)
			if !ok {
				return [4]geom.Point{}, false
			}
			minX, minY = math.Min(minX, q.X), math.Min(minY, q.Y)
			maxX, maxY = math.Max(maxX, q.X), math.Max(maxY, q.Y)
		}
	}
	if !finite(minX) || !finite(maxX) || !finite(minY) || !finite(maxY) || maxX-minX < 1e-6 || maxY-minY < 1e-6 {
		return [4]geom.Point{}, false
	}
	return [4]geom.Point{{X: minX, Y: minY}, {X: maxX, Y: minY}, {X: maxX, Y: maxY}, {X: minX, Y: maxY}}, true
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
			p, e := path(s.Path)
			if e != nil {
				return c, e
			}
			if e = brush(s.Brush); e != nil {
				return c, e
			}
			count(p)
			inv, ok := s.Transform.Inverse()
			if !ok {
				return c, fmt.Errorf("singular transform")
			}
			for _, v := range inv {
				if !finite(v) {
					return c, fmt.Errorf("ill-conditioned transform")
				}
			}
			compiled := scene.CompiledShape{Shape: s, Path: p, AffineApprox: s.Transform}
			if s.Projective != nil {
				compiled, e = compileProjective(s, p, inv)
				if e != nil {
					return c, e
				}
				// Projected outlines are still bounded by the same segment
				// budget as their source contours.
				for _, r := range compiled.ProjectedPath {
					c.Segments += len(r)
				}
			} else {
				compiled.PlaneToLocal = geom.Homography{inv[0], inv[2], inv[4], inv[1], inv[3], inv[5], 0, 0, 1}
			}
			cl.Shapes = append(cl.Shapes, compiled)
		}
		c.Layers = append(c.Layers, cl)
	}
	// Four samples per pixel; every fill is bounded even under adversarial input.
	if int64(c.Segments)*int64(d.Width)*int64(d.Height)*4 > 160_000_000 {
		return c, fmt.Errorf("scene exceeds bounded rendering work")
	}
	return c, nil
}
