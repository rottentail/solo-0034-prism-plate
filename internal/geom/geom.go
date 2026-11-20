// Package geom compiles bounded vector contours and affine coordinate mappings.
package geom

import "math"

type Point struct {
	X float64 `json:"x"`
	Y float64 `json:"y"`
}
type Matrix [6]float64

var Identity = Matrix{1, 0, 0, 1, 0, 0}

func (m Matrix) Apply(p Point) Point {
	return Point{m[0]*p.X + m[2]*p.Y + m[4], m[1]*p.X + m[3]*p.Y + m[5]}
}
func (m Matrix) Inverse() (Matrix, bool) {
	d := m[0]*m[3] - m[1]*m[2]
	if math.Abs(d) < 1e-10 {
		return Matrix{}, false
	}
	return Matrix{m[3] / d, -m[1] / d, -m[2] / d, m[0] / d, (m[2]*m[5] - m[3]*m[4]) / d, (m[1]*m[4] - m[0]*m[5]) / d}, true
}

type Op struct {
	Kind   string  `json:"kind"`
	Points []Point `json:"points,omitempty"`
}
type Contour []Point
type Path []Contour

// BBox is an axis-aligned bound in document coordinates. Filled contours never
// leave the union bbox of their flattened vertices, so a bbox disjoint from the
// sampled canvas proves the geometry contributes no visible coverage.
type BBox struct {
	MinX, MinY, MaxX, MaxY float64
}

// Bounds is the union box over every flattened vertex, including inner (hole)
// rings. Holes therefore never shrink the box and can never be rough-clipped.
func (path Path) Bounds() (BBox, bool) {
	if len(path) == 0 {
		return BBox{}, false
	}
	b := BBox{MinX: math.Inf(1), MinY: math.Inf(1), MaxX: math.Inf(-1), MaxY: math.Inf(-1)}
	for _, ring := range path {
		for _, p := range ring {
			b.MinX = math.Min(b.MinX, p.X)
			b.MinY = math.Min(b.MinY, p.Y)
			b.MaxX = math.Max(b.MaxX, p.X)
			b.MaxY = math.Max(b.MaxY, p.Y)
		}
	}
	return b, true
}

// TransformBounds maps a box through the affine matrix. Convex corners map to
// convex corners under an affine map, so transforming the four corners and
// taking their envelope is exact, including shear and rotation.
func (m Matrix) TransformBounds(b BBox) BBox {
	corners := [4]Point{
		m.Apply(Point{b.MinX, b.MinY}),
		m.Apply(Point{b.MaxX, b.MinY}),
		m.Apply(Point{b.MaxX, b.MaxY}),
		m.Apply(Point{b.MinX, b.MaxY}),
	}
	out := BBox{MinX: corners[0].X, MinY: corners[0].Y, MaxX: corners[0].X, MaxY: corners[0].Y}
	for _, p := range corners[1:] {
		out.MinX = math.Min(out.MinX, p.X)
		out.MinY = math.Min(out.MinY, p.Y)
		out.MaxX = math.Max(out.MaxX, p.X)
		out.MaxY = math.Max(out.MaxY, p.Y)
	}
	return out
}

// OutsideView reports a provably invisible box. The box is enlarged by pad and
// the canvas is enlarged by the same pad, so geometry touching or merely
// grazing the frame is never culled; only strict separation on one axis wins.
// The padding absorbs finite-precision rounding of the affine mapping.
func (b BBox) OutsideView(width, height int, pad float64) bool {
	return b.MaxX+pad < -pad ||
		b.MinX-pad > float64(width)+pad ||
		b.MaxY+pad < -pad ||
		b.MinY-pad > float64(height)+pad
}

// Flatten uses a fixed subdivision contract shared by raster and SVG export.
func Flatten(ops []Op) Path {
	var out Path
	var ring Contour
	var at Point
	flush := func() {
		if len(ring) >= 3 {
			out = append(out, ring)
		}
		ring = nil
	}
	for _, o := range ops {
		switch o.Kind {
		case "M":
			flush()
			at = o.Points[0]
			ring = append(ring, at)
		case "L":
			at = o.Points[0]
			ring = append(ring, at)
		case "Q":
			start := at
			control, end := o.Points[0], o.Points[1]
			for i := 1; i <= 16; i++ {
				t := float64(i) / 16
				u := 1 - t
				ring = append(ring, Point{u*u*start.X + 2*u*t*control.X + t*t*end.X, u*u*start.Y + 2*u*t*control.Y + t*t*end.Y})
			}
			at = end
		case "C":
			start := at
			a, b, end := o.Points[0], o.Points[1], o.Points[2]
			for i := 1; i <= 24; i++ {
				t := float64(i) / 24
				u := 1 - t
				ring = append(ring, Point{u*u*u*start.X + 3*u*u*t*a.X + 3*u*t*t*b.X + t*t*t*end.X, u*u*u*start.Y + 3*u*u*t*a.Y + 3*u*t*t*b.Y + t*t*t*end.Y})
			}
			at = end
		case "Z":
			flush()
		}
	}
	flush()
	return out
}
func (path Path) Contains(p Point, rule string) bool {
	winding := 0
	crossings := 0
	for _, ring := range path {
		for i, a := range ring {
			b := ring[(i+1)%len(ring)]
			if (a.Y <= p.Y && b.Y > p.Y) || (b.Y <= p.Y && a.Y > p.Y) {
				x := a.X + (p.Y-a.Y)*(b.X-a.X)/(b.Y-a.Y)
				if x > p.X {
					crossings++
					if b.Y > a.Y {
						winding++
					} else {
						winding--
					}
				}
			}
		}
	}
	if rule == "evenodd" {
		return crossings%2 == 1
	}
	return winding != 0
}
