package restructure

import (
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"sort"
)

// budget bounds structural geometry work per evaluation. Exhaustion is
// conservative: callers treat coverage as overlapping and the deterministic
// re-render remains the deciding check.
type budget struct{ left int }

func newBudget() *budget { return &budget{left: 4_000_000} }

func (b *budget) spend() bool { b.left--; return b.left >= 0 }

// shapeGeom is a shape's flattened coverage in plate coordinates.
type shapeGeom struct {
	id   string
	path geom.Path
	rule string
}

func compileShapes(shapes []scene.Shape) []shapeGeom {
	out := make([]shapeGeom, 0, len(shapes))
	for _, s := range shapes {
		flat := geom.Flatten(s.Path)
		for _, ring := range flat {
			for i, v := range ring {
				ring[i] = s.Transform.Apply(v)
			}
		}
		out = append(out, shapeGeom{id: s.ID, path: flat, rule: s.FillRule})
	}
	return out
}

func pathBBox(p geom.Path) (min, max geom.Point) {
	min = geom.Point{X: math.Inf(1), Y: math.Inf(1)}
	max = geom.Point{X: math.Inf(-1), Y: math.Inf(-1)}
	for _, ring := range p {
		for _, v := range ring {
			min.X, min.Y = math.Min(min.X, v.X), math.Min(min.Y, v.Y)
			max.X, max.Y = math.Max(max.X, v.X), math.Max(max.Y, v.Y)
		}
	}
	return min, max
}

func epsFor(a, b geom.Path) float64 {
	m := 1.0
	for _, p := range []geom.Path{a, b} {
		for _, ring := range p {
			for _, v := range ring {
				m = math.Max(m, math.Max(math.Abs(v.X), math.Abs(v.Y)))
			}
		}
	}
	return m * 1e-9
}

func orient(a, b, c geom.Point) float64 {
	return (b.X-a.X)*(c.Y-a.Y) - (b.Y-a.Y)*(c.X-a.X)
}

// segmentsTouch reports any intersection including endpoint contact; contact
// counts as overlap so borderline coverage falls back to the render check.
func segmentsTouch(p1, p2, p3, p4 geom.Point, eps float64) bool {
	d1 := orient(p3, p4, p1)
	d2 := orient(p3, p4, p2)
	d3 := orient(p1, p2, p3)
	d4 := orient(p1, p2, p4)
	if ((d1 > eps && d2 < -eps) || (d1 < -eps && d2 > eps)) && ((d3 > eps && d4 < -eps) || (d3 < -eps && d4 > eps)) {
		return true
	}
	onSeg := func(a, b, p geom.Point, d float64) bool {
		return math.Abs(d) <= eps &&
			p.X >= math.Min(a.X, b.X)-eps && p.X <= math.Max(a.X, b.X)+eps &&
			p.Y >= math.Min(a.Y, b.Y)-eps && p.Y <= math.Max(a.Y, b.Y)+eps
	}
	return onSeg(p3, p4, p1, d1) || onSeg(p3, p4, p2, d2) || onSeg(p1, p2, p3, d3) || onSeg(p1, p2, p4, d4)
}

func pointSegDist(p, a, b geom.Point) float64 {
	dx, dy := b.X-a.X, b.Y-a.Y
	l2 := dx*dx + dy*dy
	if l2 == 0 {
		return math.Hypot(p.X-a.X, p.Y-a.Y)
	}
	t := ((p.X-a.X)*dx + (p.Y-a.Y)*dy) / l2
	t = math.Max(0, math.Min(1, t))
	return math.Hypot(p.X-(a.X+t*dx), p.Y-(a.Y+t*dy))
}

func nearPath(v geom.Point, p geom.Path, eps float64, bud *budget) bool {
	for _, ring := range p {
		for i, a := range ring {
			if !bud.spend() {
				return true
			}
			if pointSegDist(v, a, ring[(i+1)%len(ring)]) <= eps {
				return true
			}
		}
	}
	return false
}

// pathsOverlap reports whether two flattened coverages share any point. It is
// conservative: uncertain or budget-exhausted cases answer true, which only
// downgrades a candidate from exact to the render-verified bounded class.
func pathsOverlap(a geom.Path, ruleA string, b geom.Path, ruleB string, bud *budget) bool {
	minA, maxA := pathBBox(a)
	minB, maxB := pathBBox(b)
	if maxA.X < minB.X || maxB.X < minA.X || maxA.Y < minB.Y || maxB.Y < minA.Y {
		return false
	}
	eps := epsFor(a, b)
	for _, ra := range a {
		for _, rb := range b {
			for i, a1 := range ra {
				a2 := ra[(i+1)%len(ra)]
				loX, hiX := math.Min(a1.X, a2.X)-eps, math.Max(a1.X, a2.X)+eps
				loY, hiY := math.Min(a1.Y, a2.Y)-eps, math.Max(a1.Y, a2.Y)+eps
				for j, b1 := range rb {
					if !bud.spend() {
						return true
					}
					b2 := rb[(j+1)%len(rb)]
					if math.Max(b1.X, b2.X) < loX || math.Min(b1.X, b2.X) > hiX || math.Max(b1.Y, b2.Y) < loY || math.Min(b1.Y, b2.Y) > hiY {
						continue
					}
					if segmentsTouch(a1, a2, b1, b2, eps) {
						return true
					}
				}
			}
		}
	}
	// Without edge crossings each ring lies wholly inside or outside the other
	// coverage, so one vertex per ring decides containment under the fill rule.
	for _, ra := range a {
		v := ra[0]
		if nearPath(v, b, eps, bud) || b.Contains(v, ruleB) {
			return true
		}
	}
	for _, rb := range b {
		v := rb[0]
		if nearPath(v, a, eps, bud) || a.Contains(v, ruleA) {
			return true
		}
	}
	return false
}

func geomsOverlap(a, b shapeGeom, bud *budget) bool {
	return pathsOverlap(a.path, a.rule, b.path, b.rule, bud)
}

// crossLayerDisjoint reports whether no shape of one layer covers a point that
// a shape of another layer covers.
func crossLayerDisjoint(group []scene.Layer) bool {
	geoms := make([][]shapeGeom, len(group))
	for i, l := range group {
		geoms[i] = compileShapes(l.Shapes)
	}
	bud := newBudget()
	for i := 0; i < len(group); i++ {
		for j := i + 1; j < len(group); j++ {
			for _, a := range geoms[i] {
				for _, b := range geoms[j] {
					if geomsOverlap(a, b, bud) {
						return false
					}
				}
			}
		}
	}
	return true
}

// foldedExact reports whether folding each layer's group opacity into its
// brush alpha is a compositing identity: every overlapping pair of shapes must
// come from layers that both run at opacity 1.
func foldedExact(group []scene.Layer) bool {
	type item struct {
		g shapeGeom
		o float64
	}
	var items []item
	for _, l := range group {
		for _, g := range compileShapes(l.Shapes) {
			items = append(items, item{g, l.Opacity})
		}
	}
	bud := newBudget()
	for i := 0; i < len(items); i++ {
		for j := i + 1; j < len(items); j++ {
			if items[i].o == 1 && items[j].o == 1 {
				continue
			}
			if geomsOverlap(items[i].g, items[j].g, bud) {
				return false
			}
		}
	}
	return true
}

// groupsDisjoint reports whether two shape sets share no covered point.
func groupsDisjoint(a, b []scene.Shape) bool {
	ga, gb := compileShapes(a), compileShapes(b)
	bud := newBudget()
	for _, x := range ga {
		for _, y := range gb {
			if geomsOverlap(x, y, bud) {
				return false
			}
		}
	}
	return true
}

// crossedDisjoint checks only the pairs whose stacking order changes when the
// selected shapes move to a layer above or below the remainder.
func crossedDisjoint(l scene.Layer, sel map[int]bool, position string) bool {
	geoms := compileShapes(l.Shapes)
	bud := newBudget()
	for i := range l.Shapes {
		if !sel[i] {
			continue
		}
		for j := range l.Shapes {
			if sel[j] {
				continue
			}
			crossed := (position == "above" && i < j) || (position == "below" && i > j)
			if crossed && geomsOverlap(geoms[i], geoms[j], bud) {
				return false
			}
		}
	}
	return true
}

// components groups shape indices by coverage connectivity; disjoint groups
// are natural split candidates. Groups are ordered by their top-most shape.
func components(l scene.Layer) [][]int {
	n := len(l.Shapes)
	parent := make([]int, n)
	for i := range parent {
		parent[i] = i
	}
	var find func(int) int
	find = func(x int) int {
		if parent[x] != x {
			parent[x] = find(parent[x])
		}
		return parent[x]
	}
	geoms := compileShapes(l.Shapes)
	bud := newBudget()
	for i := 0; i < n; i++ {
		for j := i + 1; j < n; j++ {
			if geomsOverlap(geoms[i], geoms[j], bud) {
				parent[find(i)] = find(j)
			}
		}
	}
	byRoot := map[int][]int{}
	for i := 0; i < n; i++ {
		r := find(i)
		byRoot[r] = append(byRoot[r], i)
	}
	out := make([][]int, 0, len(byRoot))
	for _, g := range byRoot {
		out = append(out, g)
	}
	sort.Slice(out, func(a, b int) bool { return out[a][len(out[a])-1] < out[b][len(out[b])-1] })
	return out
}
