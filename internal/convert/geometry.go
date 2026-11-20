// Package convert rewrites a shape's closed contours from one fill rule to the
// other while preserving the visible (filled) region. It works on the same
// flattened polyline geometry used by raster and vector proofing, so the
// candidate remains a set of editable M/L/Z closed paths with the original
// brush, transform and layer clip untouched.
package convert

import (
	"fmt"
	"math"
	"prismplate/internal/geom"
)

// epsRel is the geometry resolution relative to the shape's coordinate span.
// Closer features are treated as coincident and reported as unreliable.
const (
	epsRel          = 1e-9
	epsFloor        = 1e-7
	splitIterations = 8
)

type v2 = geom.Point

func sub(a, b v2) v2         { return v2{X: a.X - b.X, Y: a.Y - b.Y} }
func add(a, b v2) v2         { return v2{X: a.X + b.X, Y: a.Y + b.Y} }
func mul(a v2, k float64) v2 { return v2{X: a.X * k, Y: a.Y * k} }
func cross(a, b v2) float64  { return a.X*b.Y - a.Y*b.X }
func dot(a, b v2) float64    { return a.X*b.X + a.Y*b.Y }
func length(a v2) float64    { return math.Hypot(a.X, a.Y) }
func dist(a, b v2) float64   { return length(sub(a, b)) }

func tolerance(min, max v2) float64 {
	span := math.Max(max.X-min.X, max.Y-min.Y)
	return math.Max(span*epsRel, epsFloor)
}

// signedArea is positive for a clockwise ring in the y-down scene coordinate
// system, matching the renderer whose upward edge crossings increment winding.
func signedArea(ring []v2) float64 {
	s := 0.0
	for i, a := range ring {
		b := ring[(i+1)%len(ring)]
		s += a.X*b.Y - b.X*a.Y
	}
	return s / 2
}

// intersection solves a+s*(b-a) = c+t*(d-c). The ok result is false when the
// lines are parallel. Touching endpoints and T-junctions produce t or s of 0/1.
func intersection(a, b, c, d v2) (p v2, s, t float64, ok bool) {
	r := sub(b, a)
	q := sub(d, c)
	den := cross(r, q)
	if math.Abs(den) < epsFloor {
		return v2{}, 0, 0, false
	}
	cq := sub(c, a)
	s = cross(cq, q) / den
	t = cross(cq, r) / den
	p = add(a, mul(r, s))
	return p, s, t, true
}

// pointSegmentDistance measures the Euclidean distance from p to segment ab.
func pointSegmentDistance(p, a, b v2) float64 {
	ab := sub(b, a)
	l2 := dot(ab, ab)
	if l2 == 0 {
		return dist(p, a)
	}
	t := dot(sub(p, a), ab) / l2
	t = math.Max(0, math.Min(1, t))
	return dist(p, add(a, mul(ab, t)))
}

// segmentDistance is the minimum distance between two open segments.
func segmentDistance(a, b, c, d v2) float64 {
	if _, s, t, ok := intersection(a, b, c, d); ok && s >= 0 && s <= 1 && t >= 0 && t <= 1 {
		return 0
	}
	return math.Min(math.Min(pointSegmentDistance(a, c, d), pointSegmentDistance(b, c, d)),
		math.Min(pointSegmentDistance(c, a, b), pointSegmentDistance(d, a, b)))
}

// snapIndex merges vertices closer than eps so the arrangement is exact even
// when intersections land within floating point noise of existing vertices.
type snapIndex struct {
	pts []v2
	eps float64
}

func (idx *snapIndex) find(p v2) (int, bool) {
	for i, q := range idx.pts {
		if dist(p, q) <= idx.eps {
			return i, true
		}
	}
	return -1, false
}

func (idx *snapIndex) add(p v2) int {
	if i, ok := idx.find(p); ok {
		return i
	}
	idx.pts = append(idx.pts, p)
	return len(idx.pts) - 1
}

// dedupRing collapses consecutive duplicate vertices (zero-length edges) while
// preserving vertices that legitimately recur at separate positions, such as
// the shared crossing of a figure-eight contour. It also snaps every vertex to
// the arrangement's shared point set so near-coincident endpoints become exact.
func dedupRing(ring geom.Contour, eps float64) ([]v2, error) {
	idx := snapIndex{eps: eps}
	var ids []int
	for _, p := range ring {
		pi := idx.add(p)
		if len(ids) > 0 && ids[len(ids)-1] == pi {
			continue // consecutive duplicate: a zero-length edge
		}
		ids = append(ids, pi)
	}
	// A ring whose first and last points coincide stores the closing vertex only
	// once (geom contours are implicit-closed via Z), so drop the tail repeat.
	if len(ids) > 1 && ids[0] == ids[len(ids)-1] {
		ids = ids[:len(ids)-1]
	}
	out := make([]v2, 0, len(ids))
	for _, pi := range ids {
		out = append(out, idx.pts[pi])
	}
	if len(out) < 3 {
		return nil, fmt.Errorf("contour collapses under geometry tolerance")
	}
	return out, nil
}
