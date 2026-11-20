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

// Compose returns the affine that first applies a and then b: p -> b(a(p)).
func Compose(b, a Matrix) Matrix {
	return Matrix{
		b[0]*a[0] + b[2]*a[1],
		b[1]*a[0] + b[3]*a[1],
		b[0]*a[2] + b[2]*a[3],
		b[1]*a[2] + b[3]*a[3],
		b[0]*a[4] + b[2]*a[5] + b[4],
		b[1]*a[4] + b[3]*a[5] + b[5],
	}
}

// Bounds is an axis-aligned rectangle in plate coordinates.
type Bounds struct {
	MinX, MinY, MaxX, MaxY float64
	Empty                  bool
}

func (b Bounds) Width() float64  { return b.MaxX - b.MinX }
func (b Bounds) Height() float64 { return b.MaxY - b.MinY }
func (b Bounds) Contains(p Point) bool {
	return !b.Empty && p.X >= b.MinX && p.X <= b.MaxX && p.Y >= b.MinY && p.Y <= b.MaxY
}

// Intersect returns the overlap of two bounds; disjoint bounds become Empty.
func (b Bounds) Intersect(o Bounds) Bounds {
	if b.Empty || o.Empty {
		return Bounds{Empty: true}
	}
	out := Bounds{MinX: math.Max(b.MinX, o.MinX), MinY: math.Max(b.MinY, o.MinY), MaxX: math.Min(b.MaxX, o.MaxX), MaxY: math.Min(b.MaxY, o.MaxY)}
	if out.MaxX < out.MinX || out.MaxY < out.MinY {
		return Bounds{Empty: true}
	}
	return out
}

// PathBounds bounds a flattened contour path.
func PathBounds(p Path) Bounds {
	b := Bounds{Empty: true}
	for _, ring := range p {
		for _, v := range ring {
			if b.Empty {
				b = Bounds{MinX: v.X, MinY: v.Y, MaxX: v.X, MaxY: v.Y}
			} else {
				b.MinX = math.Min(b.MinX, v.X)
				b.MinY = math.Min(b.MinY, v.Y)
				b.MaxX = math.Max(b.MaxX, v.X)
				b.MaxY = math.Max(b.MaxY, v.Y)
			}
		}
	}
	return b
}

// TransformBounds applies an affine to an axis-aligned box.
func TransformBounds(m Matrix, b Bounds) Bounds {
	if b.Empty {
		return b
	}
	corners := [4]Point{
		m.Apply(Point{b.MinX, b.MinY}),
		m.Apply(Point{b.MaxX, b.MinY}),
		m.Apply(Point{b.MaxX, b.MaxY}),
		m.Apply(Point{b.MinX, b.MaxY}),
	}
	out := Bounds{MinX: corners[0].X, MinY: corners[0].Y, MaxX: corners[0].X, MaxY: corners[0].Y}
	for _, p := range corners[1:] {
		out.MinX = math.Min(out.MinX, p.X)
		out.MinY = math.Min(out.MinY, p.Y)
		out.MaxX = math.Max(out.MaxX, p.X)
		out.MaxY = math.Max(out.MaxY, p.Y)
	}
	return out
}

type Op struct {
	Kind   string  `json:"kind"`
	Points []Point `json:"points,omitempty"`
}
type Contour []Point
type Path []Contour

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
