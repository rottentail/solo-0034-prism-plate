// Delivery region validation and normalization.
package sanitize

import (
	"fmt"
	"math"

	"prismplate/internal/geom"
)

// Region is the JSON contract for the externally deliverable area. The points
// form a strictly convex polygon in layout (canvas) coordinates, in either
// winding order; 3..32 vertices are accepted.
type Region struct {
	Points []geom.Point `json:"points"`
}

// Area is a validated, counter-clockwise convex delivery region. Coordinates
// are in one fixed space; shapes may use a local copy produced by MapPoints.
type Area struct {
	Pts   []geom.Point
	scale float64
}

func finite(x float64) bool {
	return !math.IsNaN(x) && !math.IsInf(x, 0) && math.Abs(x) <= 1e6
}

func signedArea(p []geom.Point) float64 {
	sum := 0.0
	for i, a := range p {
		b := p[(i+1)%len(p)]
		sum += a.X*b.Y - b.X*a.Y
	}
	return sum / 2
}

// NewPolygon validates strict convexity and normalizes winding. Relative
// tolerances are scaled by the largest absolute coordinate so that the same
// epsilon means the same distance in layout and in local shape space.
func NewPolygon(points []geom.Point) (Area, error) {
	if len(points) < 3 || len(points) > 32 {
		return Area{}, fmt.Errorf("交付区域需要 3..32 个顶点")
	}
	pts := make([]geom.Point, len(points))
	scale := 1.0
	for i, p := range points {
		if !finite(p.X) || !finite(p.Y) {
			return Area{}, fmt.Errorf("交付区域第 %d 个顶点不是有限坐标", i)
		}
		pts[i] = p
		scale = math.Max(scale, math.Max(math.Abs(p.X), math.Abs(p.Y)))
	}
	for i := range pts {
		a, b := pts[i], pts[(i+1)%len(pts)]
		if math.Hypot(b.X-a.X, b.Y-a.Y) <= 1e-9*scale {
			return Area{}, fmt.Errorf("交付区域存在退化边")
		}
	}
	if math.Abs(signedArea(pts)) <= 1e-6*scale*scale {
		return Area{}, fmt.Errorf("交付区域没有可测面积")
	}
	if signedArea(pts) < 0 {
		for i, j := 0, len(pts)-1; i < j; i, j = i+1, j-1 {
			pts[i], pts[j] = pts[j], pts[i]
		}
	}
	// Strict convexity: a reflex or collinear vertex would make the
	// half-plane cutter lose winding, so such regions are caller errors
	// rather than silently approximated.
	for i := range pts {
		a := pts[i]
		b := pts[(i+1)%len(pts)]
		c := pts[(i+2)%len(pts)]
		u := geom.Point{X: b.X - a.X, Y: b.Y - a.Y}
		v := geom.Point{X: c.X - b.X, Y: c.Y - b.Y}
		cross := u.X*v.Y - u.Y*v.X
		limit := 1e-9 * (1 + math.Hypot(u.X, u.Y)*math.Hypot(v.X, v.Y))
		if cross <= limit {
			return Area{}, fmt.Errorf("交付区域必须是严格凸多边形")
		}
	}
	return Area{Pts: pts, scale: scale}, nil
}

// NewArea validates a delivery region against the document canvas. A vertex
// outside the canvas would describe a deliverable area that the source draft
// cannot present, so it is a caller error.
func NewArea(points []geom.Point, width, height int) (Area, error) {
	for i, p := range points {
		if !finite(p.X) || !finite(p.Y) {
			return Area{}, fmt.Errorf("交付区域第 %d 个顶点不是有限坐标", i)
		}
		if p.X < 0 || p.Y < 0 || p.X > float64(width) || p.Y > float64(height) {
			return Area{}, fmt.Errorf("交付区域第 %d 个顶点位于画布之外", i)
		}
	}
	return NewPolygon(points)
}

// Distance returns the minimum signed distance to the region edges: positive
// strictly inside, negative outside a counter-clockwise convex polygon.
func (a Area) Distance(p geom.Point) float64 {
	best := math.Inf(1)
	for i, u := range a.Pts {
		v := a.Pts[(i+1)%len(a.Pts)]
		dx, dy := v.X-u.X, v.Y-u.Y
		l := math.Hypot(dx, dy)
		if l == 0 {
			continue
		}
		if d := (dx*(p.Y-u.Y) - dy*(p.X-u.X)) / l; d < best {
			best = d
		}
	}
	return best
}

// Epsilon is the geometric tolerance used for boundary decisions, scaled to
// the coordinate magnitude of the region.
func (a Area) Epsilon() float64 { return eps * a.scale }

// InsideOrOn reports whether p is in the closed region.
func (a Area) InsideOrOn(p geom.Point) bool { return a.Distance(p) >= -a.Epsilon() }

// MapPoints applies an invertible affine map to every region vertex. The image
// of a convex polygon under such a map is again a convex polygon, which is what
// lets shape-local clipping stay exact.
func (a Area) MapPoints(m geom.Matrix) []geom.Point {
	out := make([]geom.Point, len(a.Pts))
	for i, p := range a.Pts {
		out[i] = m.Apply(p)
	}
	return out
}

// AreaValue reports the polygon area.
func (a Area) AreaValue() float64 { return math.Abs(signedArea(a.Pts)) }
