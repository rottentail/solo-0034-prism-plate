// Package geom compiles bounded vector contours and affine coordinate mappings.
package geom

import (
	"math"
	"sort"
)

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

// Bounds is the axis-aligned extent of a path.
type Bounds struct {
	MinX, MinY, MaxX, MaxY float64
}

func (p Path) Bounds() (Bounds, bool) {
	if len(p) == 0 || len(p[0]) == 0 {
		return Bounds{}, false
	}
	b := Bounds{MinX: p[0][0].X, MinY: p[0][0].Y, MaxX: p[0][0].X, MaxY: p[0][0].Y}
	for _, r := range p {
		for _, v := range r {
			b.MinX = math.Min(b.MinX, v.X)
			b.MinY = math.Min(b.MinY, v.Y)
			b.MaxX = math.Max(b.MaxX, v.X)
			b.MaxY = math.Max(b.MaxY, v.Y)
		}
	}
	return b, true
}

// TransformPath returns a copy of the path mapped through an affine matrix.
func TransformPath(p Path, m Matrix) Path {
	out := make(Path, len(p))
	for i, r := range p {
		nr := make(Contour, len(r))
		for j, v := range r {
			nr[j] = m.Apply(v)
		}
		out[i] = nr
	}
	return out
}

// SignedRingArea keeps contour orientation, which winding-fill area math needs.
func SignedRingArea(r Contour) float64 {
	var sum float64
	for i, a := range r {
		b := r[(i+1)%len(r)]
		sum += a.X*b.Y - b.X*a.Y
	}
	return sum / 2
}

// FillArea computes the filled area of a path under the same fill-rule
// contract used by sampling: nonzero nets the oriented ring areas, while
// evenodd counts each point by its ring parity. Coordinates are in whatever
// space the path already occupies; apply the shape transform first when the
// canvas-space area is required.
func (p Path) FillArea(rule string) float64 {
	if rule == "nonzero" {
		var sum float64
		for _, r := range p {
			sum += SignedRingArea(r)
		}
		return math.Abs(sum)
	}
	// evenodd: area of the symmetric difference of all ring interiors.
	// Collect oriented edges and sweep their vertical coverage directly on
	// the flattened contour set; paths are bounded (max 256 verbs on input).
	return evenOddArea(p)
}

func evenOddArea(p Path) float64 {
	type edge struct {
		x0, y0, x1, y1 float64
	}
	var edges []edge
	ys := map[float64]bool{}
	for _, r := range p {
		for i, a := range r {
			b := r[(i+1)%len(r)]
			if a.Y != b.Y {
				edges = append(edges, edge{a.X, a.Y, b.X, b.Y})
				ys[a.Y] = true
				ys[b.Y] = true
			}
		}
	}
	if len(edges) == 0 {
		return 0
	}
	yList := make([]float64, 0, len(ys))
	for y := range ys {
		yList = append(yList, y)
	}
	sort.Float64s(yList)
	var area float64
	for k := 0; k+1 < len(yList); k++ {
		lo, hi := yList[k], yList[k+1]
		if hi <= lo {
			continue
		}
		mid := (lo + hi) / 2
		var xs []float64
		for _, e := range edges {
			if (e.y0 <= mid && e.y1 > mid) || (e.y1 <= mid && e.y0 > mid) {
				t := (mid - e.y0) / (e.y1 - e.y0)
				xs = append(xs, e.x0+t*(e.x1-e.x0))
			}
		}
		sort.Float64s(xs)
		for i := 0; i+1 < len(xs); i += 2 {
			area += (xs[i+1] - xs[i]) * (hi - lo)
		}
	}
	return area
}
