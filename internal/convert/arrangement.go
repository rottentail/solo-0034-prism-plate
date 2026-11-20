package convert

import (
	"fmt"
	"math"
	"prismplate/internal/geom"
	"sort"
)

// halfEdge is one directed side of a split segment of the planar arrangement.
type halfEdge struct {
	from, to int
	next     int // outgoing edge immediately clockwise (face on its right)
	twin     int
	face     int
}

type arrangement struct {
	eps    float64
	points []v2
	edges  []halfEdge
	faces  [][]int // half-edge indices forming each face boundary
	notes  []string
}

// splitRings introduces a shared vertex at every crossing or contact between
// rings. Fixed-point iteration handles intersections that create new contacts;
// splitIterations is enough because each pass only refines existing segments.
func splitRings(rings [][]v2, eps float64, notes *[]string) ([][]int, []v2) {
	idx := snapIndex{eps: eps}
	split := make([][]int, len(rings))
	for ri, ring := range rings {
		for _, p := range ring {
			split[ri] = append(split[ri], idx.add(p))
		}
	}
	for iter := 0; iter < splitIterations; iter++ {
		type hit struct {
			ring  int
			pos   int // insert between pos and pos+1
			point v2
			t     float64
		}
		var hits []hit
		for ri, r := range split {
			for i := 0; i < len(r); i++ {
				a, b := idx.pts[r[i]], idx.pts[r[(i+1)%len(r)]]
				if dist(a, b) <= eps {
					continue
				}
				for rj, s := range split {
					for j := 0; j < len(s); j++ {
						// Within one ring only compare distinct segments; the
						// reversed duplicate of the same pair is skipped too.
						if rj == ri && j <= i {
							continue
						}
						c, d := idx.pts[s[j]], idx.pts[s[(j+1)%len(s)]]
						if dist(c, d) <= eps {
							continue
						}
						// Adjacent segments of one ring always share a vertex;
						// that is ordinary topology, not an unreliable contact.
						if rj == ri && (j == i+1 || i == j+1 ||
							(i == 0 && j == len(s)-1) || (j == 0 && i == len(r)-1)) {
							continue
						}
						// A real contact means edges meet within the absolute
						// geometry tolerance (tangent points, near-coincident
						// runs). Edge-length scaling would flag well-separated
						// nested geometry, so it is deliberately not applied.
						if segmentDistance(a, b, c, d) <= eps {
							*notes = append(*notes, "coincident-or-touching geometry within tolerance")
						}
						p, u, v, ok := intersection(a, b, c, d)
						if !ok {
							continue
						}
						if u < -epsRel || u > 1+epsRel || v < -epsRel || v > 1+epsRel {
							continue
						}
						u = math.Max(0, math.Min(1, u))
						v = math.Max(0, math.Min(1, v))
						// Interior intersections only; endpoints already belong
						// to the ring and are handled by the snap index. A
						// self-intersecting ring crosses two of its own segments,
						// so both insertion points are recorded even when rj==ri.
						if u > epsRel && u < 1-epsRel {
							hits = append(hits, hit{ri, i, p, u})
						}
						if v > epsRel && v < 1-epsRel {
							hits = append(hits, hit{rj, j, p, v})
						}
					}
				}
			}
		}
		if len(hits) == 0 {
			break
		}
		// Insert from the largest parameter backwards per ring edge so indices
		// remain valid, and merge coincident hits through the snap index.
		sort.SliceStable(hits, func(i, j int) bool {
			if hits[i].ring != hits[j].ring {
				return hits[i].ring < hits[j].ring
			}
			if hits[i].pos != hits[j].pos {
				return hits[i].pos > hits[j].pos
			}
			return hits[i].t > hits[j].t
		})
		inserted := map[[2]int]bool{}
		for _, h := range hits {
			pi := idx.add(h.point)
			key := [2]int{h.ring, pi}
			if inserted[key] {
				continue
			}
			r := split[h.ring]
			if r[h.pos] == pi || r[(h.pos+1)%len(r)] == pi {
				continue
			}
			tail := append([]int{pi}, r[h.pos+1:]...)
			split[h.ring] = append(r[:h.pos+1], tail...)
			inserted[key] = true
		}
	}
	return split, idx.pts
}

// buildArrangement constructs the full planar subdivision of the input rings.
func buildArrangement(rings [][]v2, eps float64) (*arrangement, error) {
	var notes []string
	split, pts := splitRings(rings, eps, &notes)
	ar := &arrangement{eps: eps, points: pts, notes: dedupNotes(notes)}

	// One undirected segment per neighbouring vertex pair; identical geometric
	// segments (same endpoints, either direction) collapse to one twin pair.
	type edgeKey struct{ a, b int }
	seen := map[edgeKey]bool{}
	addEdge := func(a, b int) error {
		if a == b {
			return nil
		}
		if seen[edgeKey{a, b}] || seen[edgeKey{b, a}] {
			ar.notes = append(ar.notes, "duplicate overlapping edge removed")
			return nil
		}
		seen[edgeKey{a, b}] = true
		h := len(ar.edges)
		ar.edges = append(ar.edges, halfEdge{from: a, to: b}, halfEdge{from: b, to: a})
		ar.edges[h].twin = h + 1
		ar.edges[h+1].twin = h
		return nil
	}
	for _, r := range split {
		for i := 0; i < len(r); i++ {
			if err := addEdge(r[i], r[(i+1)%len(r)]); err != nil {
				return nil, err
			}
		}
	}

	// Outgoing edges per vertex, ordered by polar angle. With y down, ascending
	// atan2 walks clockwise; the next edge of a face is the predecessor of the
	// twin in this order, keeping the face on the right of each directed edge.
	out := map[int][]int{}
	for h := range ar.edges {
		out[ar.edges[h].from] = append(out[ar.edges[h].from], h)
	}
	for v := range out {
		sort.Slice(out[v], func(i, j int) bool {
			ai := math.Atan2(pts[ar.edges[out[v][i]].to].Y-pts[v].Y, pts[ar.edges[out[v][i]].to].X-pts[v].X)
			aj := math.Atan2(pts[ar.edges[out[v][j]].to].Y-pts[v].Y, pts[ar.edges[out[v][j]].to].X-pts[v].X)
			return ai < aj
		})
	}
	indexInOut := map[int]int{}
	for _, hs := range out {
		for i, h := range hs {
			indexInOut[h] = i
		}
	}
	for h := range ar.edges {
		twin := ar.edges[h].twin
		hs := out[ar.edges[h].to]
		pos := indexInOut[twin]
		ar.edges[h].next = hs[(pos-1+len(hs))%len(hs)]
	}

	// Face walk: every half-edge is visited exactly once.
	used := make([]bool, len(ar.edges))
	faceID := 0
	for h := range ar.edges {
		if used[h] {
			continue
		}
		var loop []int
		cur := h
		for !used[cur] {
			used[cur] = true
			ar.edges[cur].face = faceID
			loop = append(loop, cur)
			cur = ar.edges[cur].next
		}
		if cur != h {
			return nil, fmt.Errorf("non-manifold arrangement: face walk did not close (unreliable input)")
		}
		ar.faces = append(ar.faces, loop)
		faceID++
	}
	for _, u := range used {
		if !u {
			return nil, fmt.Errorf("arrangement left unvisited edges (unreliable input)")
		}
	}
	ar.notes = dedupNotes(ar.notes)
	return ar, nil
}

func dedupNotes(in []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, n := range in {
		if !seen[n] {
			seen[n] = true
			out = append(out, n)
		}
	}
	return out
}

// bbox returns the min/max vertices and a guaranteed-outside probe point.
func bbox(pts []v2) (min, max, outside v2) {
	min = pts[0]
	max = pts[0]
	for _, p := range pts[1:] {
		min.X = math.Min(min.X, p.X)
		min.Y = math.Min(min.Y, p.Y)
		max.X = math.Max(max.X, p.X)
		max.Y = math.Max(max.Y, p.Y)
	}
	span := math.Max(max.X-min.X, max.Y-min.Y)
	outside = v2{X: min.X - span - 10, Y: min.Y - span - 10}
	return min, max, outside
}

// facePoint returns an interior probe slightly to the left of the loop's
// longest edge; the face on the right of each directed edge is therefore on
// the edge's left normal side.
func facePoint(ar *arrangement, loop []int) (v2, bool) {
	best := -1
	bestLen := 0.0
	for _, h := range loop {
		e := ar.edges[h]
		if l := dist(ar.points[e.from], ar.points[e.to]); l > bestLen {
			bestLen, best = l, h
		}
	}
	if best < 0 || bestLen == 0 {
		return v2{}, false
	}
	e := ar.edges[best]
	a, b := ar.points[e.from], ar.points[e.to]
	dir := sub(b, a)
	unit := v2{X: dir.X / bestLen, Y: dir.Y / bestLen}
	leftNormal := v2{X: -unit.Y, Y: unit.X}
	mid := mul(add(a, b), 0.5)
	probe := add(mid, mul(leftNormal, ar.eps*4))
	return probe, true
}

// outsideEveryRing is true when p is not enclosed by any source ring on its own.
// Disconnected components can make several arrangement faces satisfy this; every
// such exterior face must classify as empty under the source rule.
func outsideEveryRing(source geom.Path, p v2) bool {
	for _, ring := range source {
		var crossings int
		for i, a := range ring {
			b := ring[(i+1)%len(ring)]
			if (a.Y <= p.Y && b.Y > p.Y) || (b.Y <= p.Y && a.Y > p.Y) {
				x := a.X + (p.Y-a.Y)*(b.X-a.X)/(b.Y-a.Y)
				if x > p.X {
					crossings++
				}
			}
		}
		if crossings%2 == 1 {
			return false
		}
	}
	return true
}

// arrangementToLoops emits one boundary loop per filled/unfilled transition in
// the arrangement. The face walk keeps each face on the right of its edges, so
// an emitted boundary with the filled face on its right has negative signed
// area (counter-clockwise outer) and positive signed area for holes.
func arrangementToLoops(ar *arrangement, source geom.Path, rule string) ([][]v2, error) {
	unbounded := map[int]bool{}
	filled := make([]bool, len(ar.faces))
	for fi, loop := range ar.faces {
		probe, ok := facePoint(ar, loop)
		if !ok {
			return nil, fmt.Errorf("degenerate face with no usable interior probe")
		}
		// Disconnected components can produce several arrangement faces that
		// are all exterior; every one of them must classify as empty.
		if outsideEveryRing(source, probe) {
			unbounded[fi] = true
		}
		filled[fi] = source.Contains(probe, rule)
	}
	if len(unbounded) == 0 {
		return nil, fmt.Errorf("cannot identify unbounded face (unreliable input)")
	}
	for fi := range unbounded {
		if filled[fi] {
			return nil, fmt.Errorf("exterior face classified as filled (unreliable input)")
		}
	}
	emitted := map[int]bool{}
	var loops [][]v2
	// Every boundary has two directed sides. We walk the side belonging to the
	// unfilled face (empty on the right, filled twin on the left). At each
	// vertex we continue around the unfilled face via next; if that next edge is
	// not itself a boundary edge, the unfilled face continues across its twin.
	isBoundary := func(h int) bool {
		return !filled[ar.edges[h].face] && filled[ar.edges[ar.edges[h].twin].face]
	}
	for h := range ar.edges {
		if !isBoundary(h) || emitted[h] {
			continue
		}
		var ring []v2
		var edgeTrail []int
		cur := h
		steps := 0
		for {
			if steps > len(ar.edges)+1 {
				return nil, fmt.Errorf("boundary walk did not terminate (unreliable input)")
			}
			steps++
			emitted[cur] = true
			edgeTrail = append(edgeTrail, cur)
			ring = append(ring, ar.points[ar.edges[cur].from])
			nxt := ar.edges[cur].next
			if !isBoundary(nxt) {
				nxt = ar.edges[nxt].twin
			}
			cur = nxt
			if cur == h {
				break
			}
		}
		if len(ring) < 3 {
			return nil, fmt.Errorf("emitted boundary collapsed below three vertices")
		}
		// The empty face is on the right of each boundary edge, so an outer
		// boundary runs clockwise (positive signed area in y-down) and a hole
		// boundary counter-clockwise (negative). The filled region is on the
		// left of every emitted edge in both cases. A zero-area ring is a spike
		// created by a self-contact and encloses no region, so it is dropped.
		if signedArea(ring) == 0 {
			for _, e := range edgeTrail {
				delete(emitted, e)
			}
			continue
		}
		loops = append(loops, ring)
	}
	if len(loops) == 0 {
		return nil, fmt.Errorf("source fill has no visible region; nothing to convert")
	}
	return loops, nil
}

// exactFilledArea measures the filled region of source under rule. It performs
// an exact horizontal-scanline integration on the y-slabs between every
// distinct vertex y-coordinate. Within one slab no edge endpoint occurs, so
// the crossing x-coordinates move linearly and the fill state of each interval
// between consecutive crossings is constant. The interval state is computed
// with the renderer's own winding (nonzero) or parity (evenodd) rule, which
// handles nesting, overlap, opposite winding and self-intersection correctly
// without relying on ring orientation.
func exactFilledArea(source geom.Path, rule string, eps float64) (float64, error) {
	var rings [][]v2
	for _, ring := range source {
		clean, err := dedupRing(ring, eps)
		if err != nil {
			return 0, err
		}
		rings = append(rings, clean)
	}
	// Critical y-coordinates include every vertex and every edge intersection.
	// Splitting slabs at intersection heights guarantees no scanline passes
	// through a crossing, which is what makes self-intersecting rings exact.
	yset := map[float64]bool{}
	for _, ring := range rings {
		for _, p := range ring {
			yset[p.Y] = true
		}
	}
	for ri := range rings {
		for rj := ri; rj < len(rings); rj++ {
			a := rings[ri]
			b := rings[rj]
			for i := 0; i < len(a); i++ {
				for j := 0; j < len(b); j++ {
					if ri == rj && j <= i {
						continue
					}
					p0, p1 := a[i], a[(i+1)%len(a)]
					q0, q1 := b[j], b[(j+1)%len(b)]
					if p, _, _, ok := intersection(p0, p1, q0, q1); ok {
						yset[p.Y] = true
					}
				}
			}
		}
	}
	var ys []float64
	for y := range yset {
		ys = append(ys, y)
	}
	sort.Float64s(ys)
	total := 0.0
	for k := 0; k+1 < len(ys); k++ {
		y0, y1 := ys[k], ys[k+1]
		if y1-y0 <= 0 {
			continue
		}
		ym := (y0 + y1) / 2
		type cross struct {
			x       float64
			winding int
		}
		var crs []cross
		for _, ring := range rings {
			for i, a := range ring {
				b := ring[(i+1)%len(ring)]
				if (a.Y <= ym && b.Y > ym) || (b.Y <= ym && a.Y > ym) {
					x := a.X + (ym-a.Y)*(b.X-a.X)/(b.Y-a.Y)
					w := 1
					if b.Y < a.Y {
						w = -1
					}
					crs = append(crs, cross{x, w})
				}
			}
		}
		sort.Slice(crs, func(i, j int) bool { return crs[i].x < crs[j].x })
		// State entering the leftmost interval is empty (winding 0, parity 0).
		// After each crossing, recompute the state and measure the interval to
		// the next crossing when that state is filled.
		winding, parity, slab := 0, 0, 0.0
		for i, c := range crs {
			winding += c.winding
			parity++
			filled := winding != 0
			if rule == "evenodd" {
				filled = parity%2 == 1
			}
			if i+1 < len(crs) && filled {
				slab += crs[i+1].x - c.x
			}
		}
		total += slab * (y1 - y0)
	}
	return total, nil
}
