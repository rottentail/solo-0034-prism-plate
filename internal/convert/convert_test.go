package convert

import (
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"reflect"
	"testing"
)

func rect(x0, y0, x1, y1 float64) geom.Contour {
	return geom.Contour{{X: x0, Y: y0}, {X: x1, Y: y0}, {X: x1, Y: y1}, {X: x0, Y: y1}}
}

func rectCW(x0, y0, x1, y1 float64) geom.Contour {
	// Explicit clockwise ring in y-down coordinates (positive signed area).
	return rect(x0, y0, x1, y1)
}

func rectCCW(x0, y0, x1, y1 float64) geom.Contour {
	return geom.Contour{{X: x0, Y: y0}, {X: x0, Y: y1}, {X: x1, Y: y1}, {X: x1, Y: y0}}
}

func pathOps(path geom.Path) []geom.Op {
	var ops []geom.Op
	for _, ring := range path {
		ops = append(ops, geom.Op{Kind: "M", Points: []geom.Point{ring[0]}})
		for _, p := range ring[1:] {
			ops = append(ops, geom.Op{Kind: "L", Points: []geom.Point{p}})
		}
		ops = append(ops, geom.Op{Kind: "Z"})
	}
	return ops
}

func shape(rule string, path geom.Path) scene.Shape {
	return scene.Shape{ID: "s1", FillRule: rule, Path: pathOps(path), Transform: geom.Identity,
		Brush: scene.Brush{Kind: "solid", Color: scene.Color{R: 1, A: 1}}}
}

// equivalence scans a fine grid and requires identical filled classification.
func equivalence(t *testing.T, srcPath geom.Path, srcRule string, candOps []geom.Op, dstRule string) (mismatches int) {
	t.Helper()
	candPath := geom.Flatten(candOps)
	for x := -2.0; x <= 12.0; x += 0.137 {
		for y := -2.0; y <= 12.0; y += 0.151 {
			p := geom.Point{X: x, Y: y}
			if srcPath.Contains(p, srcRule) != candPath.Contains(p, dstRule) {
				t.Errorf("membership mismatch at %v under %s->%s", p, srcRule, dstRule)
				mismatches++
				if mismatches > 5 {
					return mismatches
				}
			}
		}
	}
	return mismatches
}

func runConvert(t *testing.T, rule string, path geom.Path, target string) Result {
	t.Helper()
	doc := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{shape(rule, path)}}}}
	res, err := Document("p1", "l1", "s1", target, doc)
	if err != nil {
		t.Fatalf("conversion %s->%s failed: %v", rule, target, err)
	}
	if res.Report.Reliability == ReliabilityReject {
		t.Fatalf("conversion rejected: %v", res.Report.Warnings)
	}
	if res.Report.GridMismatches != 0 {
		t.Fatalf("report records %d grid mismatches", res.Report.GridMismatches)
	}
	if !res.Report.CandidateCompiles {
		t.Fatalf("candidate does not compile")
	}
	return res
}

// Nested square with a hole under evenodd must become an oriented outer+hole
// pair under nonzero, and the hole must stay empty.
func TestNestedHoleEvenOddToNonZero(t *testing.T) {
	p := geom.Path{rectCW(1, 1, 9, 9), rectCW(3, 3, 7, 7)}
	res := runConvert(t, "evenodd", p, "nonzero")
	cand := res.Document.Layers[0].Shapes[0]
	if cand.FillRule != "nonzero" {
		t.Fatalf("target rule not applied")
	}
	cp := geom.Flatten(cand.Path)
	if len(cp) != 2 {
		t.Fatalf("expected outer + hole contours, got %d", len(cp))
	}
	if signedArea(cp[0]) >= 0 || signedArea(cp[1]) <= 0 {
		t.Fatalf("outer must be negative-area and hole positive-area: %+v",
			[]float64{signedArea(cp[0]), signedArea(cp[1])})
	}
	// Hole interior stays empty under the new rule.
	if cp.Contains(geom.Point{X: 5, Y: 5}, "nonzero") {
		t.Fatalf("hole was filled by conversion")
	}
	if !cp.Contains(geom.Point{X: 2, Y: 2}, "nonzero") {
		t.Fatalf("outer region became empty")
	}
	equivalence(t, p, "evenodd", cand.Path, "nonzero")
}

// Two same-direction overlapping squares under nonzero fill the overlap once;
// the nonzero->evenodd rewrite must keep that overlap filled (parity 1).
func TestOverlapNonZeroToEvenOdd(t *testing.T) {
	p := geom.Path{rectCW(1, 1, 6, 6), rectCW(4, 1, 9, 6)}
	res := runConvert(t, "nonzero", p, "evenodd")
	cand := res.Document.Layers[0].Shapes[0]
	cp := geom.Flatten(cand.Path)
	if cp.Contains(geom.Point{X: 5, Y: 3}, "evenodd") != true {
		t.Fatalf("nonzero overlap must remain filled")
	}
	equivalence(t, p, "nonzero", cand.Path, "evenodd")
}

// Opposite winding: outer clockwise plus inner counter-clockwise already forms
// a nonzero hole; converting to evenodd must preserve the hole.
func TestOppositeWindingNonZeroToEvenOdd(t *testing.T) {
	p := geom.Path{rectCW(1, 1, 9, 9), rectCCW(3, 3, 7, 7)}
	res := runConvert(t, "nonzero", p, "evenodd")
	cand := res.Document.Layers[0].Shapes[0]
	cp := geom.Flatten(cand.Path)
	if cp.Contains(geom.Point{X: 5, Y: 5}, "evenodd") {
		t.Fatalf("opposite-winding hole filled after conversion")
	}
	equivalence(t, p, "nonzero", cand.Path, "evenodd")
}

// A bow-tie self-intersecting ring under nonzero keeps both lobes filled and
// only the crossing region behaviour per winding; both rules must round-trip.
func TestSelfIntersectingBowtie(t *testing.T) {
	bow := geom.Contour{{X: 1, Y: 1}, {X: 9, Y: 9}, {X: 9, Y: 1}, {X: 1, Y: 9}}
	p := geom.Path{bow}
	for _, target := range []string{"nonzero", "evenodd"} {
		sourceRule := map[string]string{"nonzero": "evenodd", "evenodd": "nonzero"}[target]
		res := runConvert(t, sourceRule, p, target)
		cand := res.Document.Layers[0].Shapes[0]
		equivalence(t, p, sourceRule, cand.Path, target)
		if res.Report.Intersections == 0 {
			t.Fatalf("expected an intersection vertex for bow-tie, got report %+v", res.Report)
		}
	}
}

// Tangent rings share exactly one point; the topology must stay manifold and
// neither fill region may leak through the contact.
func TestTangentRings(t *testing.T) {
	p := geom.Path{rectCW(1, 1, 5, 5), rectCW(5, 3, 9, 7)}
	for _, pair := range [][2]string{{"nonzero", "evenodd"}, {"evenodd", "nonzero"}} {
		res := runConvert(t, pair[0], p, pair[1])
		cand := res.Document.Layers[0].Shapes[0]
		equivalence(t, p, pair[0], cand.Path, pair[1])
		if res.Report.CoincidentContacts == 0 {
			t.Fatalf("tangent contact not flagged in report")
		}
	}
}

// Three-level nesting alternates fill/hole/fill under evenodd; a nonzero
// candidate needs correct orientation on every level.
func TestTripleNesting(t *testing.T) {
	p := geom.Path{rectCW(0, 0, 10, 10), rectCW(2, 2, 8, 8), rectCW(4, 4, 6, 6)}
	res := runConvert(t, "evenodd", p, "nonzero")
	cp := geom.Flatten(res.Document.Layers[0].Shapes[0].Path)
	if len(cp) != 3 {
		t.Fatalf("expected 3 oriented contours, got %d", len(cp))
	}
	want := []bool{false, true, false} // outer/hole/island signed-area signs
	for i, positive := range want {
		got := signedArea(cp[i]) > 0
		if got != positive {
			t.Fatalf("contour %d orientation %v want positive=%v", i, got, positive)
		}
	}
	if !cp.Contains(geom.Point{X: 5, Y: 5}, "nonzero") {
		t.Fatalf("innermost island must stay filled")
	}
	if cp.Contains(geom.Point{X: 3, Y: 3}, "nonzero") {
		t.Fatalf("middle hole must stay empty")
	}
	equivalence(t, p, "evenodd", res.Document.Layers[0].Shapes[0].Path, "nonzero")
}

// Round-trip evenodd -> nonzero -> evenodd must preserve the region and the
// original document must remain untouched.
func TestRoundTripPreservesRegionAndSource(t *testing.T) {
	p := geom.Path{rectCW(1, 1, 9, 9), rectCW(3, 3, 7, 7)}
	doc := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{shape("evenodd", p)}}}}
	first, err := Document("p1", "l1", "s1", "nonzero", doc)
	if err != nil {
		t.Fatal(err)
	}
	if doc.Layers[0].Shapes[0].FillRule != "evenodd" {
		t.Fatalf("source document was mutated")
	}
	second, err := Document("p1", "l1", "s1", "evenodd", first.Document)
	if err != nil {
		t.Fatal(err)
	}
	equivalence(t, p, "evenodd", second.Document.Layers[0].Shapes[0].Path, "evenodd")
	// Brush, transform and layer clip survive untouched.
	orig := doc.Layers[0].Shapes[0]
	got := second.Document.Layers[0].Shapes[0]
	if !reflect.DeepEqual(got.Brush, orig.Brush) || got.Transform != orig.Transform {
		t.Fatalf("brush or transform changed during conversion")
	}
}

// A ring collapsed to fewer than three distinct vertices is unreliable input.
func TestDegenerateRingRejected(t *testing.T) {
	bad := geom.Path{{{X: 1, Y: 1}, {X: 1 + 1e-12, Y: 1}, {X: 2, Y: 1}}}
	doc := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{shape("evenodd", bad)}}}}
	if _, err := Document("p1", "l1", "s1", "nonzero", doc); err == nil {
		t.Fatalf("degenerate ring must be rejected")
	}
}

// A candidate exceeding the 256-op admission limit is refused with an explicit
// node-budget warning rather than truncated. A dense rotated grid of crossing
// rectangles forces one arrangement vertex per crossing pair.
func TestNodeBudgetRefusal(t *testing.T) {
	var p geom.Path
	// 12 thin rectangles rotated about a common centre; each pair crosses twice,
	// producing hundreds of arrangement vertices regardless of snap merging.
	n := 12
	for i := 0; i < n; i++ {
		a := math.Pi * float64(i) / float64(n)
		c, s := math.Cos(a), math.Sin(a)
		rot := func(x, y float64) geom.Point {
			return geom.Point{X: 5 + c*x - s*y, Y: 5 + s*x + c*y}
		}
		p = append(p, geom.Contour{rot(-4.5, -0.35), rot(4.5, -0.35), rot(4.5, 0.35), rot(-4.5, 0.35)})
	}
	doc := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{shape("evenodd", p)}}}}
	_, err := Document("p1", "l1", "s1", "nonzero", doc)
	if err == nil {
		t.Fatalf("budget-exceeding candidate must be refused")
	}
}

// Mirror (negative-determinant) transforms flip winding; conversion happens in
// local coordinates so the visible region must still be preserved.
func TestMirroredTransformInterpretation(t *testing.T) {
	p := geom.Path{rectCW(1, 1, 9, 9), rectCW(3, 3, 7, 7)}
	s := shape("evenodd", p)
	s.Transform = geom.Matrix{-1, 0, 0, 1, 10, 0} // horizontal mirror
	doc := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{s}}}}
	res, err := Document("p1", "l1", "s1", "nonzero", doc)
	if err != nil {
		t.Fatal(err)
	}
	cand := res.Document.Layers[0].Shapes[0]
	if cand.Transform != s.Transform {
		t.Fatalf("mirror transform must be retained")
	}
	// Verify in device coordinates through the actual transform.
	inv, _ := s.Transform.Inverse()
	cp := geom.Flatten(cand.Path)
	for x := 0.5; x < 10; x += 0.4 {
		for y := 0.5; y < 10; y += 0.4 {
			device := geom.Point{X: x, Y: y}
			local := inv.Apply(device)
			if p.Contains(local, "evenodd") != cp.Contains(local, "nonzero") {
				t.Fatalf("mirrored membership differs at device %v local %v", device, local)
			}
		}
	}
}

func TestNarrowChannelFlagsApproximation(t *testing.T) {
	// Two rings separated by a 0.05 unit sliver: well above the snap tolerance
	// but below the 0.25 device-unit reliable channel threshold.
	p := geom.Path{rectCW(1, 1, 5, 9), rectCW(5.05, 1, 9, 9)}
	res := runConvert(t, "nonzero", p, "evenodd")
	if !res.Report.Approximation {
		t.Fatalf("narrow channel must set approximation flag, report=%+v", res.Report)
	}
	if res.Report.Reliability != ReliabilityApprox {
		t.Fatalf("narrow channel reliability must be approx, got %s", res.Report.Reliability)
	}
	if res.Report.MinChannelDevice >= 0.25 {
		t.Fatalf("min channel measurement wrong: %f", res.Report.MinChannelDevice)
	}
}

func TestCurveFlattenWarning(t *testing.T) {
	// A quadratic outer ring plus an inner hole, expressed with Q/C verbs. The
	// candidate must remain editable M/L/Z geometry and disclose flattening.
	ops := []geom.Op{
		{Kind: "M", Points: []geom.Point{{X: 2, Y: 32}}},
		{Kind: "Q", Points: []geom.Point{{X: 2, Y: 2}, {X: 62, Y: 32}}},
		{Kind: "L", Points: []geom.Point{{X: 2, Y: 32}}},
		{Kind: "Z"},
		{Kind: "M", Points: []geom.Point{{X: 20, Y: 30}}},
		{Kind: "C", Points: []geom.Point{{X: 20, Y: 20}, {X: 44, Y: 20}, {X: 44, Y: 30}}},
		{Kind: "L", Points: []geom.Point{{X: 20, Y: 30}}},
		{Kind: "Z"},
	}
	doc := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{
			{ID: "s1", FillRule: "evenodd", Path: ops, Transform: geom.Identity,
				Brush: scene.Brush{Kind: "solid", Color: scene.Color{A: 1}}}}}}}
	res, err := Document("p1", "l1", "s1", "nonzero", doc)
	if err != nil {
		t.Fatal(err)
	}
	if !res.Report.Approximation || res.Report.Reliability != ReliabilityApprox {
		t.Fatalf("curve flattening must be disclosed: %+v", res.Report)
	}
	for _, o := range res.Document.Layers[0].Shapes[0].Path {
		if o.Kind == "Q" || o.Kind == "C" {
			t.Fatalf("candidate must only use editable M/L/Z verbs")
		}
	}
	if res.Report.GridMismatches != 0 {
		t.Fatalf("flattened candidate must remain region-equivalent")
	}
}

func TestSameRuleAndUnknownRuleRejected(t *testing.T) {
	p := geom.Path{rectCW(1, 1, 9, 9)}
	doc := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{shape("nonzero", p)}}}}
	if _, err := Document("p1", "l1", "s1", "nonzero", doc); err == nil {
		t.Fatalf("same-rule conversion must be rejected")
	}
	if _, err := Document("p1", "l1", "s1", "lumpy", doc); err == nil {
		t.Fatalf("unknown rule must be rejected")
	}
	if _, err := Document("p1", "missing", "s1", "evenodd", doc); err == nil {
		t.Fatalf("missing layer must be rejected")
	}
}

func TestAreaEquivalenceMetric(t *testing.T) {
	p := geom.Path{rectCW(1, 1, 9, 9), rectCW(3, 3, 7, 7)}
	res := runConvert(t, "evenodd", p, "nonzero")
	// Visible area under evenodd = outer 64 minus hole 16 = 48 local units.
	if math.Abs(res.Report.SourceArea-48) > 1e-6 || math.Abs(res.Report.CandidateArea-48) > 1e-6 {
		t.Fatalf("area metric wrong: source=%f candidate=%f", res.Report.SourceArea, res.Report.CandidateArea)
	}
	if res.Report.AreaDelta > res.Report.AreaTolerance {
		t.Fatalf("area delta beyond tolerance")
	}
}
