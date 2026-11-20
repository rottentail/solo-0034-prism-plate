package sanitize

import (
	"fmt"
	"math"

	"prismplate/internal/geom"
)

// eps is a relative geometric tolerance in working units. Scene coordinates
// are bounded by 1e6, so 1e-9 separates distinct authored geometry while
// absorbing the rounding introduced by affine mapping and curve flattening.
const eps = 1e-9

func tolFor(scale float64) float64 { return eps * math.Max(1, scale) }

// dedupe removes repeated vertices (including last==first) under a scaled
// tolerance and drops rings that have collapsed to a point.
func dedupe(ring []geom.Point, scale float64) []geom.Point {
	tol := tolFor(scale)
	out := make([]geom.Point, 0, len(ring))
	for _, p := range ring {
		if len(out) > 0 {
			q := out[len(out)-1]
			if math.Abs(p.X-q.X) <= tol && math.Abs(p.Y-q.Y) <= tol {
				continue
			}
		}
		out = append(out, p)
	}
	for len(out) > 1 {
		a, b := out[0], out[len(out)-1]
		if math.Abs(a.X-b.X) <= tol && math.Abs(a.Y-b.Y) <= tol {
			out = out[:len(out)-1]
			continue
		}
		break
	}
	return out
}

// polygonArea returns the signed shoelace area.
func polygonArea(ring []geom.Point) float64 {
	sum := 0.0
	for i, a := range ring {
		b := ring[(i+1)%len(ring)]
		sum += a.X*b.Y - b.X*a.Y
	}
	return sum / 2
}

// pointInConvex reports whether p is inside or on a counter-clockwise convex
// polygon, within tol.
func pointInConvex(p geom.Point, clip []geom.Point, tol float64) bool {
	for i, u := range clip {
		v := clip[(i+1)%len(clip)]
		if (v.X-u.X)*(p.Y-u.Y)-(v.Y-u.Y)*(p.X-u.X) < -tol {
			return false
		}
	}
	return true
}

// fragment is a chain of ring vertices lying inside one half-plane. Its open
// ends lie on the clip line and are rejoined during stitching.
type fragment struct {
	pts    []geom.Point
	entry  geom.Point
	exit   geom.Point
	entryT float64
	exitT  float64
}

// splitHalfPlane cuts each ring against the half-plane left of edge->edge+dir.
// It works for simple, possibly concave rings and can split one ring into
// several, which is why a plain Sutherland-Hodgman pass is not sufficient.
func splitHalfPlane(rings [][]geom.Point, edge, dir geom.Point, scale float64) ([][]geom.Point, error) {
	tol := tolFor(scale) * math.Hypot(dir.X, dir.Y)
	dist := func(p geom.Point) float64 { return dir.X*(p.Y-edge.Y) - dir.Y*(p.X-edge.X) }
	param := func(p geom.Point) float64 { return dir.X*p.X + dir.Y*p.Y }

	var out [][]geom.Point
	for _, ring := range rings {
		n := len(ring)
		if n < 3 {
			continue
		}
		inside := make([]bool, n)
		allIn, noneIn := true, true
		for i, p := range ring {
			inside[i] = dist(p) >= -tol
			if inside[i] {
				noneIn = false
			} else {
				allIn = false
			}
		}
		if allIn {
			out = append(out, ring)
			continue
		}
		if noneIn {
			continue
		}
		frags, err := ringFragments(ring, inside, dist, param, tol)
		if err != nil {
			return nil, err
		}
		joined, err := stitch(frags)
		if err != nil {
			return nil, err
		}
		for _, r := range joined {
			r = dedupe(r, scale)
			if len(r) >= 3 && math.Abs(polygonArea(r)) > tolFor(scale)*tolFor(scale)*4 {
				out = append(out, r)
			}
		}
	}
	return out, nil
}

// ringFragments walks one ring and returns its inside chains.
func ringFragments(ring []geom.Point, inside []bool, dist, param func(geom.Point) float64, tol float64) ([]fragment, error) {
	n := len(ring)
	cross := func(a, b geom.Point) geom.Point {
		da, db := dist(a), dist(b)
		d := da - db
		if math.Abs(d) <= 1e-300 {
			return geom.Point{X: (a.X + b.X) / 2, Y: (a.Y + b.Y) / 2}
		}
		t := da / d
		t = math.Max(0, math.Min(1, t))
		return geom.Point{X: a.X + (b.X-a.X)*t, Y: a.Y + (b.Y-a.Y)*t}
	}
	start := -1
	for i := 0; i < n; i++ {
		if inside[i] && !inside[(i-1+n)%n] {
			start = i
			break
		}
	}
	if start < 0 {
		// No clean entering edge (for example a single vertex touching the
		// line). Such a touch has no area and is dropped.
		return nil, nil
	}
	var out []fragment
	var cur fragment
	curActive := false
	for k := 0; k < n; k++ {
		i := (start + k) % n
		prev := (i - 1 + n) % n
		switch {
		case inside[i] && !inside[prev]:
			if curActive {
				return nil, fmt.Errorf("轮廓与交付边界的相交结构无法安全缝合")
			}
			entry := cross(ring[prev], ring[i])
			cur = fragment{pts: []geom.Point{entry, ring[i]}, entry: entry, entryT: param(entry)}
			curActive = true
		case inside[i] && inside[prev]:
			if !curActive {
				return nil, fmt.Errorf("轮廓与交付边界的相交结构无法安全缝合")
			}
			cur.pts = append(cur.pts, ring[i])
		case !inside[i] && inside[prev]:
			if !curActive {
				return nil, fmt.Errorf("轮廓与交付边界的相交结构无法安全缝合")
			}
			exit := cross(ring[prev], ring[i])
			cur.pts = append(cur.pts, exit)
			cur.exit, cur.exitT = exit, param(exit)
			out = append(out, cur)
			curActive = false
		}
	}
	if curActive {
		return nil, fmt.Errorf("轮廓与交付边界的相交结构无法安全缝合")
	}
	return out, nil
}

// stitch reconnects fragment ends along the clip line. Each exit continues to
// the next entry in the +dir direction; fragments that belong to one connected
// piece of the intersection are concatenated, and pieces that the cut opened up
// become separate rings. This is the step that keeps concave shapes and
// cut-outs exact; pairing failures are reported instead of guessed.
func stitch(frags []fragment) ([][]geom.Point, error) {
	if len(frags) == 0 {
		return nil, nil
	}
	next := make([]int, len(frags))
	used := make([]bool, len(frags))
	for i := range frags {
		best := -1
		bestT := math.Inf(1)
		for j := range frags {
			if frags[j].entryT >= frags[i].exitT-1e-12 && frags[j].entryT < bestT {
				best, bestT = j, frags[j].entryT
			}
		}
		if best < 0 {
			for j := range frags {
				if frags[j].entryT < bestT {
					best, bestT = j, frags[j].entryT
				}
			}
		}
		if best < 0 {
			return nil, fmt.Errorf("轮廓与交付边界的相交结构无法安全缝合")
		}
		next[i] = best
	}
	// A valid pairing is a permutation: two exits may not claim one entry.
	claimed := make([]bool, len(frags))
	for _, j := range next {
		if claimed[j] {
			return nil, fmt.Errorf("轮廓与交付边界的相交结构无法安全配对")
		}
		claimed[j] = true
	}
	var out [][]geom.Point
	for i := range frags {
		if used[i] {
			continue
		}
		var ring []geom.Point
		cur := i
		for steps := 0; steps <= len(frags); steps++ {
			if used[cur] {
				break
			}
			used[cur] = true
			ring = append(ring, frags[cur].pts...)
			cur = next[cur]
			if cur == i {
				break
			}
			ring = append(ring, frags[cur].pts[0])
		}
		if len(ring) >= 3 {
			out = append(out, ring)
		}
	}
	for _, u := range used {
		if !u {
			return nil, fmt.Errorf("轮廓与交付边界的相交结构无法安全闭合")
		}
	}
	return out, nil
}

// clipToConvex intersects simple rings with a counter-clockwise convex polygon
// by successive half-plane cuts. Rings keep their orientation, so nonzero
// winding and even-odd holes stay meaningful in the delivery.
func clipToConvex(subject [][]geom.Point, clip []geom.Point, scale float64) ([][]geom.Point, error) {
	rings := make([][]geom.Point, 0, len(subject))
	for _, r := range subject {
		if rr := dedupe(r, scale); len(rr) >= 3 {
			rings = append(rings, rr)
		}
	}
	for i := range clip {
		edge := clip[i]
		next := clip[(i+1)%len(clip)]
		dir := geom.Point{X: next.X - edge.X, Y: next.Y - edge.Y}
		var err error
		rings, err = splitHalfPlane(rings, edge, dir, scale)
		if err != nil {
			return nil, err
		}
		if len(rings) == 0 {
			return nil, nil
		}
	}
	return rings, nil
}
