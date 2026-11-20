// Package morph builds credible transition frames between two outline shapes.
//
// Correspondence contract: contours are classified into a containment tree so
// holes are matched with holes and never reconnected to an outer boundary;
// winding is normalized by role; each matched ring pair is resampled to a
// shared vertex count at uniform arc length and aligned by the cyclic shift
// with the least squared vertex distance. Interpolation is linear per vertex.
// Every frame must pass credibility gates (no self-intersection, no crossing
// between rings, holes stay inside their container) or the whole request fails
// with an explanation instead of emitting self-intersecting geometry.
package morph

import (
	"fmt"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

const (
	minVertices   = 24
	maxVertices   = 96
	floorVertices = 12
	maxOps        = 256 // scene contract: one shape path admits at most 256 operations
)

// RingReport describes one matched contour pair of the correspondence.
type RingReport struct {
	Role         string  `json:"role"`
	Parent       int     `json:"parent"`
	Vertices     int     `json:"vertices"`
	Shift        int     `json:"shift"`
	MeanDistance float64 `json:"mean_distance"`
	MaxDistance  float64 `json:"max_distance"`
}

// FrameReport carries per-frame quality metrics for designer review.
type FrameReport struct {
	Revision          int     `json:"revision"`
	T                 float64 `json:"t"`
	Area              float64 `json:"area"`
	Perimeter         float64 `json:"perimeter"`
	SelfIntersections int     `json:"self_intersections"`
	HolesContained    bool    `json:"holes_contained"`
}

// Report summarizes the established correspondence and per-frame quality.
type Report struct {
	Rings  []RingReport  `json:"rings"`
	Frames []FrameReport `json:"frames"`
}

type ring struct {
	pts    geom.Contour
	parent int // index of the containing ring, -1 for outer boundaries
	depth  int // containment depth: even is an outer boundary, odd is a hole
}

type ringPair struct{ a, b int }

func dist(a, b geom.Point) float64  { return math.Hypot(a.X-b.X, a.Y-b.Y) }
func dist2(a, b geom.Point) float64 { d := a.X - b.X; e := a.Y - b.Y; return d*d + e*e }
func round3(x float64) float64      { return math.Round(x*1000) / 1000 }

func signedArea(c geom.Contour) float64 {
	var a float64
	for i, p := range c {
		q := c[(i+1)%len(c)]
		a += p.X*q.Y - q.X*p.Y
	}
	return a / 2
}
func perimeter(c geom.Contour) float64 {
	var p float64
	for i := range c {
		p += dist(c[i], c[(i+1)%len(c)])
	}
	return p
}
func centroid(c geom.Contour) geom.Point {
	var x, y float64
	for _, p := range c {
		x += p.X
		y += p.Y
	}
	n := float64(len(c))
	return geom.Point{X: x / n, Y: y / n}
}

// flatten applies the shape transform so interpolation runs in plate coordinates.
func flatten(s scene.Shape) geom.Path {
	p := geom.Flatten(s.Path)
	for _, c := range p {
		for i := range c {
			c[i] = s.Transform.Apply(c[i])
		}
	}
	return p
}

// classify derives the containment tree from geometry alone; input winding is
// not trusted because the two drafts may use opposite conventions.
func classify(path geom.Path) ([]ring, error) {
	rings := make([]ring, len(path))
	areas := make([]float64, len(path))
	for i, c := range path {
		areas[i] = math.Abs(signedArea(c))
		if areas[i] < 1e-6 {
			return nil, fmt.Errorf("contour %d is degenerate (near-zero area)", i)
		}
	}
	for i, c := range path {
		depth, parent := 0, -1
		best := math.Inf(1)
		for j, other := range path {
			if i == j {
				continue
			}
			if (geom.Path{other}).Contains(c[0], "evenodd") {
				depth++
				if areas[j] < best {
					best = areas[j]
					parent = j
				}
			}
		}
		rings[i] = ring{pts: c, parent: parent, depth: depth}
	}
	return rings, nil
}

func children(rs []ring, parent int) []int {
	var out []int
	for i, r := range rs {
		if r.parent == parent {
			out = append(out, i)
		}
	}
	return out
}

// pairSiblings greedily matches sibling contours by nearest centroid; the
// common case of a single outer boundary per side is exact.
func pairSiblings(ra, rb []ring, aIdx, bIdx []int) []ringPair {
	used := make([]bool, len(bIdx))
	var out []ringPair
	for _, ai := range aIdx {
		ca := centroid(ra[ai].pts)
		best, bestD := -1, math.Inf(1)
		for k, bi := range bIdx {
			if used[k] {
				continue
			}
			if d := dist2(ca, centroid(rb[bi].pts)); d < bestD {
				bestD, best = d, k
			}
		}
		used[best] = true
		out = append(out, ringPair{ai, bIdx[best]})
	}
	return out
}

// match pairs contours level by level so a hole is only ever paired with a
// hole under the same matched outer boundary. Count mismatches are explained
// rather than bridged by invented geometry.
func match(ra, rb []ring) ([]ringPair, error) {
	var pairs []ringPair
	rootsA, rootsB := children(ra, -1), children(rb, -1)
	if len(rootsA) != len(rootsB) {
		return nil, fmt.Errorf("morph: outer contour count differs (%d vs %d); no credible correspondence", len(rootsA), len(rootsB))
	}
	queue := pairSiblings(ra, rb, rootsA, rootsB)
	for len(queue) > 0 {
		pr := queue[0]
		queue = queue[1:]
		pairs = append(pairs, pr)
		ca, cb := children(ra, pr.a), children(rb, pr.b)
		if len(ca) != len(cb) {
			return nil, fmt.Errorf("morph: hole count differs under matched outer %d (%d vs %d); holes are never reconnected to the outer boundary", len(pairs)-1, len(ca), len(cb))
		}
		queue = append(queue, pairSiblings(ra, rb, ca, cb)...)
	}
	return pairs, nil
}

// normalize winds outer boundaries positive and holes negative so the nonzero
// fill rule renders the same topology the containment tree describes.
func normalize(r *ring) {
	want := 1.0
	if r.depth%2 == 1 {
		want = -1
	}
	if signedArea(r.pts)*want < 0 {
		c := r.pts
		for i, j := 0, len(c)-1; i < j; i, j = i+1, j-1 {
			c[i], c[j] = c[j], c[i]
		}
	}
}

func dedup(c geom.Contour) geom.Contour {
	out := make(geom.Contour, 0, len(c))
	for _, p := range c {
		if len(out) == 0 || dist(out[len(out)-1], p) > 1e-9 {
			out = append(out, p)
		}
	}
	for len(out) > 1 && dist(out[0], out[len(out)-1]) <= 1e-9 {
		out = out[:len(out)-1]
	}
	return out
}

// resample places n vertices at uniform arc length, erasing differences in
// control point counts between the two drafts.
func resample(c geom.Contour, n int) geom.Contour {
	m := len(c)
	per := perimeter(c)
	if m == 0 || per <= 0 {
		out := make(geom.Contour, n)
		for i := range out {
			out[i] = c[0]
		}
		return out
	}
	step := per / float64(n)
	out := make(geom.Contour, 0, n)
	target, acc := 0.0, 0.0
	for i := 0; i < m && len(out) < n; i++ {
		a, b := c[i], c[(i+1)%m]
		L := dist(a, b)
		if L == 0 {
			continue
		}
		for acc+L >= target && len(out) < n {
			t := (target - acc) / L
			out = append(out, geom.Point{X: a.X + t*(b.X-a.X), Y: a.Y + t*(b.Y-a.Y)})
			target += step
		}
		acc += L
	}
	for len(out) < n {
		out = append(out, c[m-1])
	}
	return out
}

// bestShift finds the cyclic start-point alignment with the least squared
// vertex distance. Direction is not searched: winding is fixed by fill
// semantics, so only the shift absorbs different draft start points.
func bestShift(a, b geom.Contour) (shift int, mean, max float64) {
	n := len(a)
	best := math.Inf(1)
	for s := 0; s < n; s++ {
		var sum float64
		for i := 0; i < n; i++ {
			sum += dist2(a[i], b[(i+s)%n])
		}
		if sum < best {
			best, shift = sum, s
		}
	}
	for i := 0; i < n; i++ {
		d := math.Sqrt(dist2(a[i], b[(i+shift)%n]))
		mean += d
		if d > max {
			max = d
		}
	}
	return shift, mean / float64(n), max
}

func rotate(c geom.Contour, s int) geom.Contour {
	out := make(geom.Contour, len(c))
	for i := range c {
		out[i] = c[(i+s)%len(c)]
	}
	return out
}

func orient(a, b, c geom.Point) float64 {
	return (b.X-a.X)*(c.Y-a.Y) - (b.Y-a.Y)*(c.X-a.X)
}

// segmentsCross reports proper crossings only; shared vertices and collinear
// touches are not treated as intersections.
func segmentsCross(a, b, c, d geom.Point) bool {
	return orient(a, b, c)*orient(a, b, d) < 0 && orient(c, d, a)*orient(c, d, b) < 0
}

func selfIntersections(c geom.Contour) int {
	n, count := len(c), 0
	for i := 0; i < n; i++ {
		a1, a2 := c[i], c[(i+1)%n]
		for j := i + 1; j < n; j++ {
			if j == i || (j+1)%n == i || (i+1)%n == j {
				continue
			}
			if segmentsCross(a1, a2, c[j], c[(j+1)%n]) {
				count++
			}
		}
	}
	return count
}

func ringsCross(a, b geom.Contour) bool {
	for i := range a {
		for j := range b {
			if segmentsCross(a[i], a[(i+1)%len(a)], b[j], b[(j+1)%len(b)]) {
				return true
			}
		}
	}
	return false
}

func inside(parent, child geom.Contour) bool {
	p := geom.Path{parent}
	for _, v := range child {
		if !p.Contains(v, "evenodd") {
			return false
		}
	}
	return true
}

// violations is the credibility gate applied to source, target and every
// interpolated frame; the first problem found is returned as an explanation.
func violations(cs []geom.Contour, parents []int) string {
	for i, c := range cs {
		if n := selfIntersections(c); n > 0 {
			return fmt.Sprintf("ring %d self-intersects (%d crossings)", i, n)
		}
	}
	for i := 0; i < len(cs); i++ {
		for j := i + 1; j < len(cs); j++ {
			child, parent := -1, -1
			if parents[j] == i {
				child, parent = j, i
			}
			if parents[i] == j {
				child, parent = i, j
			}
			if child >= 0 {
				if ringsCross(cs[child], cs[parent]) || !inside(cs[parent], cs[child]) {
					return fmt.Sprintf("ring %d leaves its container ring %d; a hole must stay a hole", child, parent)
				}
			} else if ringsCross(cs[i], cs[j]) {
				return fmt.Sprintf("ring %d crosses ring %d", i, j)
			}
		}
	}
	return ""
}

func toOps(cs []geom.Contour) []geom.Op {
	var ops []geom.Op
	for _, c := range cs {
		ops = append(ops, geom.Op{Kind: "M", Points: []geom.Point{c[0]}})
		for _, p := range c[1:] {
			ops = append(ops, geom.Op{Kind: "L", Points: []geom.Point{p}})
		}
		ops = append(ops, geom.Op{Kind: "Z"})
	}
	return ops
}

func netArea(cs []geom.Contour) float64 {
	var a float64
	for _, c := range cs {
		a += signedArea(c)
	}
	return a
}

// Frames interpolates steps transition outlines between two shapes. The
// returned operations are in plate coordinates and keep holes as separate
// closed contours; any credibility failure aborts the whole sequence.
func Frames(from, to scene.Shape, steps int) ([][]geom.Op, Report, error) {
	var rep Report
	if steps < 1 {
		return nil, rep, fmt.Errorf("morph: steps must be positive")
	}
	ra, err := classify(flatten(from))
	if err != nil {
		return nil, rep, fmt.Errorf("morph: source outline rejected: %v", err)
	}
	rb, err := classify(flatten(to))
	if err != nil {
		return nil, rep, fmt.Errorf("morph: target outline rejected: %v", err)
	}
	pairs, err := match(ra, rb)
	if err != nil {
		return nil, rep, err
	}
	for i := range ra {
		normalize(&ra[i])
	}
	for i := range rb {
		normalize(&rb[i])
	}
	ns := make([]int, len(pairs))
	for k, pr := range pairs {
		n := len(ra[pr.a].pts)
		if len(rb[pr.b].pts) > n {
			n = len(rb[pr.b].pts)
		}
		if n < minVertices {
			n = minVertices
		}
		if n > maxVertices {
			n = maxVertices
		}
		ns[k] = n
	}
	for ops := 0; ; {
		ops = 0
		for _, n := range ns {
			ops += n + 1
		}
		if ops <= maxOps {
			break
		}
		widest := 0
		for k := range ns {
			if ns[k] > ns[widest] {
				widest = k
			}
		}
		if ns[widest] <= floorVertices {
			return nil, rep, fmt.Errorf("morph: %d matched contours exceed the %d operation path budget", len(pairs), maxOps)
		}
		ns[widest]--
	}
	type aligned struct {
		a, b   geom.Contour
		parent int
	}
	posOf := make(map[int]int, len(pairs))
	for k, pr := range pairs {
		posOf[pr.a] = k
	}
	rings := make([]aligned, len(pairs))
	for k, pr := range pairs {
		ca := resample(dedup(ra[pr.a].pts), ns[k])
		cb := resample(dedup(rb[pr.b].pts), ns[k])
		shift, mean, max := bestShift(ca, cb)
		parent := -1
		if ra[pr.a].parent >= 0 {
			parent = posOf[ra[pr.a].parent]
		}
		rings[k] = aligned{ca, rotate(cb, shift), parent}
		role := "outer"
		if ra[pr.a].depth%2 == 1 {
			role = "hole"
		}
		rep.Rings = append(rep.Rings, RingReport{Role: role, Parent: parent, Vertices: ns[k], Shift: shift, MeanDistance: round3(mean), MaxDistance: round3(max)})
	}
	parents := make([]int, len(rings))
	for k, r := range rings {
		parents[k] = r.parent
	}
	at := func(t float64) []geom.Contour {
		out := make([]geom.Contour, len(rings))
		for k, r := range rings {
			c := make(geom.Contour, len(r.a))
			for i := range c {
				c[i] = geom.Point{X: r.a[i].X + (r.b[i].X-r.a[i].X)*t, Y: r.a[i].Y + (r.b[i].Y-r.a[i].Y)*t}
			}
			out[k] = c
		}
		return out
	}
	if v := violations(at(0), parents); v != "" {
		return nil, rep, fmt.Errorf("morph: source outline not credible: %s", v)
	}
	if v := violations(at(1), parents); v != "" {
		return nil, rep, fmt.Errorf("morph: target outline not credible: %s", v)
	}
	var frames [][]geom.Op
	var per float64
	for _, c := range at(0) {
		per += perimeter(c)
	}
	for i := 1; i <= steps; i++ {
		t := float64(i) / float64(steps+1)
		cs := at(t)
		if v := violations(cs, parents); v != "" {
			return nil, rep, fmt.Errorf("morph: transition not credible at frame %d (t=%.2f): %s", i, t, v)
		}
		frames = append(frames, toOps(cs))
		per = 0
		for _, c := range cs {
			per += perimeter(c)
		}
		rep.Frames = append(rep.Frames, FrameReport{T: round3(t), Area: round3(netArea(cs)), Perimeter: round3(per), SelfIntersections: 0, HolesContained: true})
	}
	return frames, rep, nil
}
