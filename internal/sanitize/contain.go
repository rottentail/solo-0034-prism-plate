package sanitize

import (
	"math"

	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

// splitContours returns the authored operations of each closed contour in the
// same order geom.Flatten yields rings. The admission contract guarantees every
// contour starts with M and ends with Z, so the split is unambiguous.
func splitContours(ops []geom.Op) [][]geom.Op {
	var out [][]geom.Op
	var cur []geom.Op
	for _, o := range ops {
		if o.Kind == "M" {
			cur = []geom.Op{o}
			continue
		}
		if cur == nil {
			continue
		}
		cur = append(cur, o)
		if o.Kind == "Z" {
			out = append(out, cur)
			cur = nil
		}
	}
	return out
}

// curveTolerance reports the largest layout-space deviation between an authored
// quadratic/cubic segment and the 16/24-segment flattening that both backends
// already use. It is reported with an export so a receiver knows the error
// inherent in the retained curves; conversion itself adds none inside the
// region because the cut boundary is polygon-exact against that same
// flattening.
func curveTolerance(d scene.Document) float64 {
	worst := 0.0
	for _, l := range d.Layers {
		for _, s := range l.Shapes {
			var at geom.Point
			for _, o := range s.Path {
				n := 0
				switch o.Kind {
				case "Q":
					n = 16
				case "C":
					n = 24
				}
				if n > 0 {
					prev := s.Transform.Apply(evalCurve(o, at, 0))
					for i := 1; i <= n; i++ {
						cur := s.Transform.Apply(evalCurve(o, at, float64(i)/float64(n)))
						for k := 1; k < 4; k++ {
							t := (float64(i-1) + float64(k)/4) / float64(n)
							q := s.Transform.Apply(evalCurve(o, at, t))
							if dev := pointSegmentDistance(q, prev, cur); dev > worst {
								worst = dev
							}
						}
						prev = cur
					}
				}
				if len(o.Points) > 0 {
					at = o.Points[len(o.Points)-1]
				}
			}
		}
	}
	if math.IsNaN(worst) || math.IsInf(worst, 0) {
		return 0
	}
	return worst
}

func evalCurve(o geom.Op, start geom.Point, t float64) geom.Point {
	u := 1 - t
	if o.Kind == "Q" {
		c, e := o.Points[0], o.Points[1]
		return geom.Point{
			X: u*u*start.X + 2*u*t*c.X + t*t*e.X,
			Y: u*u*start.Y + 2*u*t*c.Y + t*t*e.Y,
		}
	}
	a, b, e := o.Points[0], o.Points[1], o.Points[2]
	return geom.Point{
		X: u*u*u*start.X + 3*u*u*t*a.X + 3*u*t*t*b.X + t*t*t*e.X,
		Y: u*u*u*start.Y + 3*u*u*t*a.Y + 3*u*t*t*b.Y + t*t*t*e.Y,
	}
}

func pointSegmentDistance(p, a, b geom.Point) float64 {
	dx, dy := b.X-a.X, b.Y-a.Y
	if dx == 0 && dy == 0 {
		return math.Hypot(p.X-a.X, p.Y-a.Y)
	}
	t := ((p.X-a.X)*dx + (p.Y-a.Y)*dy) / (dx*dx + dy*dy)
	t = math.Max(0, math.Min(1, t))
	return math.Hypot(p.X-(a.X+t*dx), p.Y-(a.Y+t*dy))
}
