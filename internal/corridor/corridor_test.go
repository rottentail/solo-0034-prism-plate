package corridor

import (
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/repository"
	"prismplate/internal/scene"
	"testing"
)

func solidShape(id string, ops []geom.Op) scene.Shape {
	return scene.Shape{
		ID:        id,
		Path:      ops,
		FillRule:  "nonzero",
		Brush:     scene.Brush{Kind: "solid", Color: scene.Color{R: 0.2, G: 0.2, B: 0.2, A: 1}},
		Transform: geom.Identity,
	}
}

func rectOps(x0, y0, x1, y1 float64) []geom.Op {
	return []geom.Op{
		{Kind: "M", Points: []geom.Point{{X: x0, Y: y0}}},
		{Kind: "L", Points: []geom.Point{{X: x1, Y: y0}}},
		{Kind: "L", Points: []geom.Point{{X: x1, Y: y1}}},
		{Kind: "L", Points: []geom.Point{{X: x0, Y: y1}}},
		{Kind: "Z"},
	}
}

func docWith(shapes ...scene.Shape) scene.Document {
	return scene.Document{
		Width: 100, Height: 100,
		Background: scene.Color{R: 1, G: 1, B: 1, A: 1},
		Layers:     []scene.Layer{{ID: "layer-1", Opacity: 1, Blend: "normal", Shapes: shapes}},
	}
}

// Two vertical bars leave a straight negative-space gap from x=40..60 (width 20).
func barsDocument() scene.Document {
	left := solidShape("left-bar", rectOps(10, 10, 40, 90))
	right := solidShape("right-bar", rectOps(60, 10, 90, 90))
	return docWith(left, right)
}

func straightChannel() Channel {
	return Channel{
		ID:         "gap",
		Centerline: []geom.Point{{X: 50, Y: 15}, {X: 50, Y: 85}},
		MinWidth:   10,
	}
}

func TestCheckPassesOnOriginalGap(t *testing.T) {
	rep, err := Check(barsDocument(), []Channel{straightChannel()})
	if err != nil {
		t.Fatal(err)
	}
	if !rep.Passes {
		t.Fatalf("original gap must pass: %+v", rep)
	}
	if math.Abs(rep.Channels[0].MinClearance-10) > 1e-9 {
		t.Fatalf("clearance = %v, want 10", rep.Channels[0].MinClearance)
	}
}

// Marker distance alone would still hold when the channel middle is pinched by
// a third contour; the swept assertion must detect the pinch.
func TestCheckDetectsMidChannelPinch(t *testing.T) {
	d := barsDocument()
	// A narrow tooth intrudes into the middle of the gap from the left without
	// crossing the centreline: local gap narrows from 20 to 8.
	tooth := solidShape("tooth", rectOps(30, 45, 48, 55))
	d.Layers[0].Shapes = append(d.Layers[0].Shapes, tooth)
	rep, err := Check(d, []Channel{straightChannel()})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Passes {
		t.Fatal("pinched channel must fail")
	}
	cr := rep.Channels[0]
	// Narrowest distance from centreline x=50 to the tooth edge x=48 is 2; the
	// two marker endpoints still see the full 20-wide gap, so marker distance
	// alone would miss this.
	if math.Abs(cr.MinClearance-2) > 1e-9 {
		t.Fatalf("clearance = %v, want 2", cr.MinClearance)
	}
	if cr.Narrowest.Wall != "layer[0].shape[tooth]" {
		t.Fatalf("limiting wall = %q", cr.Narrowest.Wall)
	}
}

func TestSolveStopsBeforeClosingStraightChannel(t *testing.T) {
	d := barsDocument()
	// Drag the whole right edge of the left bar toward the right bar; full
	// intent closes the gap (edge moves x:40 -> 60), so the solver must stop
	// around alpha .75. Both edge vertices move to keep the bar rigid.
	moves := []Move{
		{ShapeID: "left-bar", From: geom.Point{X: 40, Y: 10}, To: geom.Point{X: 60, Y: 10}},
		{ShapeID: "left-bar", From: geom.Point{X: 40, Y: 90}, To: geom.Point{X: 60, Y: 90}},
	}
	proposal, err := Solve(d, moves, []Channel{straightChannel()}, repository.SceneHash)
	if err != nil {
		t.Fatal(err)
	}
	if proposal.Feasible {
		t.Fatal("full drag intent must be infeasible")
	}
	// At alpha the left edge moves from x=40 by 20a, so the centreline wall
	// distance is 10-20a. Passing needs 5+Tolerance, i.e. the true boundary is
	// a = (5-Tolerance)/20 = 0.2475. The solver converges to just below it.
	want := (5 - Tolerance) / 20
	if proposal.BoundaryAlpha > want || proposal.BoundaryAlpha < want-0.005 {
		t.Fatalf("boundary alpha = %v, want ~%v", proposal.BoundaryAlpha, want)
	}
	if len(proposal.Candidates) != maxCandidates {
		t.Fatalf("candidate count = %d", len(proposal.Candidates))
	}
	for _, c := range proposal.Candidates {
		if !c.Report.Passes {
			t.Fatalf("returned candidate alpha %v fails protection", c.Alpha)
		}
		for _, dev := range c.NodeDeviations {
			if math.Abs(dev.Ratio-c.Alpha) > 1e-9 {
				t.Fatalf("deviation ratio %v mismatches alpha %v", dev.Ratio, c.Alpha)
			}
		}
	}
	if proposal.Limit == nil || proposal.Limit.Passes {
		t.Fatal("limit report must describe the failing full intent")
	}
}

func TestSolveReturnsFullIntentWhenFeasible(t *testing.T) {
	d := barsDocument()
	// Small move that never threatens the width-10 channel.
	move := Move{ShapeID: "left-bar", From: geom.Point{X: 10, Y: 10}, To: geom.Point{X: 8, Y: 10}}
	proposal, err := Solve(d, []Move{move}, []Channel{straightChannel()}, repository.SceneHash)
	if err != nil {
		t.Fatal(err)
	}
	if !proposal.Feasible || proposal.BoundaryAlpha != 1 {
		t.Fatalf("feasible=%v boundary=%v", proposal.Feasible, proposal.BoundaryAlpha)
	}
	last := proposal.Candidates[len(proposal.Candidates)-1]
	if last.Alpha != 1 {
		t.Fatalf("last candidate alpha = %v, want 1", last.Alpha)
	}
	if last.SceneDigest != repository.SceneHash(last.Document) {
		t.Fatal("candidate digest must match its document")
	}
}

// A curved (bent) channel between two rigid bars. The centreline bends and the
// limiting point is at the bend, away from both marker endpoints.
func TestSolveProtectsCurvedChannel(t *testing.T) {
	top := solidShape("top", rectOps(5, 5, 95, 38))
	bottom := solidShape("bottom", rectOps(5, 62, 95, 95))
	d := docWith(top, bottom)
	ch := Channel{
		ID:         "curve-gap",
		Centerline: []geom.Point{{X: 15, Y: 50}, {X: 50, Y: 52}, {X: 85, Y: 50}},
		MinWidth:   12,
	}
	rep, err := Check(d, []Channel{ch})
	if err != nil {
		t.Fatal(err)
	}
	if !rep.Passes {
		t.Fatalf("curved channel baseline must pass: %+v", rep.Channels[0])
	}
	// Lift the whole bottom bar toward the top bar; the bend sample (y=52)
	// pinches first even though both endpoint markers keep more room.
	moves := []Move{
		{ShapeID: "bottom", From: geom.Point{X: 5, Y: 62}, To: geom.Point{X: 5, Y: 50}},
		{ShapeID: "bottom", From: geom.Point{X: 95, Y: 62}, To: geom.Point{X: 95, Y: 50}},
		{ShapeID: "bottom", From: geom.Point{X: 95, Y: 95}, To: geom.Point{X: 95, Y: 83}},
		{ShapeID: "bottom", From: geom.Point{X: 5, Y: 95}, To: geom.Point{X: 5, Y: 83}},
	}
	proposal, err := Solve(d, moves, []Channel{ch}, repository.SceneHash)
	if err != nil {
		t.Fatal(err)
	}
	if proposal.Feasible {
		t.Fatal("lifting the lower bar into the channel must eventually pinch it")
	}
	if proposal.BoundaryAlpha >= 1 {
		t.Fatal("boundary must stay below full intent")
	}
	for _, c := range proposal.Candidates {
		if !c.Report.Passes {
			t.Fatalf("candidate %v failed its own re-verification", c.Alpha)
		}
	}
}

// Local transforms enlarge a shape; the wall must be measured after the affine
// mapping, so channel clearance reflects canvas units rather than local units.
func TestCheckHonoursAffineTransform(t *testing.T) {
	d := barsDocument()
	// Map the right bar through x -> 0.5x + 35: local edge 60 -> canvas 65 and
	// local edge 90 -> canvas 80. A centreline biased toward the right side
	// measures the transformed wall directly: its canvas distance is 7, whereas
	// reading local coordinates naively would report 60-58=2.
	d.Layers[0].Shapes[1].Transform = geom.Matrix{0.5, 0, 0, 1, 35, 0}
	ch := Channel{
		ID:         "biased-gap",
		Centerline: []geom.Point{{X: 58, Y: 20}, {X: 58, Y: 80}},
		MinWidth:   8,
	}
	rep, err := Check(d, []Channel{ch})
	if err != nil {
		t.Fatal(err)
	}
	if math.Abs(rep.Channels[0].MinClearance-7) > 1e-9 {
		t.Fatalf("transformed clearance = %v, want 7", rep.Channels[0].MinClearance)
	}
	if rep.Channels[0].Narrowest.Wall != "layer[0].shape[right-bar]" {
		t.Fatalf("limiting wall = %q", rep.Channels[0].Narrowest.Wall)
	}
}

// A channel enclosed by true Bezier contours (C verbs with control points).
// Both boundary bands are curved; the centreline bends and its middle, not the
// markers, is the narrowest part.
func TestCheckCurvedBezierEnvelope(t *testing.T) {
	upper := []geom.Op{
		{Kind: "M", Points: []geom.Point{{X: 5, Y: 34}}},
		{Kind: "C", Points: []geom.Point{{X: 35, Y: 26}, {X: 65, Y: 26}, {X: 95, Y: 34}}},
		{Kind: "L", Points: []geom.Point{{X: 95, Y: 5}}},
		{Kind: "L", Points: []geom.Point{{X: 5, Y: 5}}},
		{Kind: "Z"},
	}
	lower := []geom.Op{
		{Kind: "M", Points: []geom.Point{{X: 5, Y: 66}}},
		{Kind: "C", Points: []geom.Point{{X: 35, Y: 74}, {X: 65, Y: 74}, {X: 95, Y: 66}}},
		{Kind: "L", Points: []geom.Point{{X: 95, Y: 95}}},
		{Kind: "L", Points: []geom.Point{{X: 5, Y: 95}}},
		{Kind: "Z"},
	}
	d := docWith(solidShape("upper", upper), solidShape("lower", lower))
	ch := Channel{
		ID:         "bezier-gap",
		Centerline: []geom.Point{{X: 12, Y: 50}, {X: 50, Y: 50}, {X: 88, Y: 50}},
		MinWidth:   20,
	}
	rep, err := Check(d, []Channel{ch})
	if err != nil {
		t.Fatal(err)
	}
	if !rep.Passes {
		t.Fatalf("curved baseline must pass: %+v", rep.Channels[0])
	}
	// Tighten the demand so the curved middle fails while the straight end
	// regions would still be wide enough: marker distance alone would pass.
	strict := ch
	strict.MinWidth = 36
	rep2, err := Check(d, []Channel{strict})
	if err != nil {
		t.Fatal(err)
	}
	if rep2.Passes {
		t.Fatal("stricter width must fail at the curved middle")
	}
	if rep2.Channels[0].Narrowest.Wall == "" {
		t.Fatal("narrowest point must name a limiting wall")
	}
}

// Canvas bounds are walls: a centreline whose required disk crosses the canvas
// edge must fail even though no shape stands near it.
func TestCheckTreatsCanvasAsWall(t *testing.T) {
	ch := Channel{
		ID:         "edge",
		Centerline: []geom.Point{{X: 3, Y: 50}, {X: 3, Y: 60}},
		MinWidth:   10,
	}
	rep, err := Check(barsDocument(), []Channel{ch})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Passes {
		t.Fatal("channel crossing the canvas edge must fail")
	}
	if rep.Channels[0].Narrowest.Wall != "canvas" {
		t.Fatalf("wall = %q, want canvas", rep.Channels[0].Narrowest.Wall)
	}
}

// A channel jointly enclosed by another same-layer contour and a layer clip
// ring: the clip boundary itself counts as a wall, not only filled shapes.
func TestCheckIncludesLayerClipAsWall(t *testing.T) {
	d := barsDocument()
	// Clip the layer to a box whose right side stands 5 canvas units from the
	// centreline; the required disk radius (5 plus the fixed tolerance) crosses
	// the clip edge.
	d.Layers[0].Clip = rectOps(0, 0, 55, 100)
	ch := Channel{
		ID:         "clip-gap",
		Centerline: []geom.Point{{X: 50, Y: 20}, {X: 50, Y: 80}},
		MinWidth:   10,
	}
	rep, err := Check(d, []Channel{ch})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Passes {
		t.Fatal("clip ring must bound the channel like a contour")
	}
	if rep.Channels[0].Narrowest.Wall != "layer[layer-1].clip" {
		t.Fatalf("wall = %q, want layer clip", rep.Channels[0].Narrowest.Wall)
	}
}

func TestBaselineFailureRejectsAndKeepsOriginal(t *testing.T) {
	d := barsDocument()
	bad := Channel{
		ID:         "too-wide",
		Centerline: []geom.Point{{X: 50, Y: 20}, {X: 50, Y: 80}},
		MinWidth:   40, // gap is only 20 wide
	}
	move := Move{ShapeID: "left-bar", From: geom.Point{X: 10, Y: 10}, To: geom.Point{X: 5, Y: 10}}
	_, err := Solve(d, []Move{move}, []Channel{bad}, repository.SceneHash)
	if err == nil {
		t.Fatal("baseline violation must error")
	}
	be, ok := err.(*BaselineError)
	if !ok {
		t.Fatalf("want *BaselineError, got %T", err)
	}
	if be.Report.Passes {
		t.Fatal("baseline report must show failure")
	}
}

func TestUnknownAndUnmatchedMovesRejected(t *testing.T) {
	d := barsDocument()
	if _, err := Solve(d,
		[]Move{{ShapeID: "ghost", From: geom.Point{X: 1, Y: 1}, To: geom.Point{X: 2, Y: 2}}},
		[]Channel{straightChannel()}, repository.SceneHash); err == nil {
		t.Fatal("unknown shape must be rejected")
	}
	if _, err := Solve(d,
		[]Move{{ShapeID: "left-bar", From: geom.Point{X: 1, Y: 1}, To: geom.Point{X: 2, Y: 2}}},
		[]Channel{straightChannel()}, repository.SceneHash); err == nil {
		t.Fatal("unmatched node must be rejected")
	}
}

// Fill membership follows the shape's fill rule: an even-odd ring with a hole
// leaves the hole negative space, while a swept point inside solid fill scores 0.
func TestCheckHonoursEvenOddHole(t *testing.T) {
	// Outer square plus an inner square wound in the same direction: under
	// even-odd the inner region is a hole (negative space).
	outer := rectOps(10, 10, 90, 90)
	inner := rectOps(40, 40, 60, 60)
	shape := solidShape("ring", append(outer, inner...))
	shape.FillRule = "evenodd"
	d := docWith(shape)
	ch := Channel{
		ID:         "hole",
		Centerline: []geom.Point{{X: 50, Y: 45}, {X: 50, Y: 55}},
		MinWidth:   8,
	}
	rep, err := Check(d, []Channel{ch})
	if err != nil {
		t.Fatal(err)
	}
	// Hole half-width is 10 (centreline x=50 to inner edge x=40/60); the
	// channel stays inside the hole, so it must pass.
	if !rep.Passes {
		t.Fatalf("even-odd hole must be negative space: %+v", rep.Channels[0])
	}
}

// Multiple channels are all enforced: satisfying one but pinching another fails
// the whole document.
func TestCheckMultipleChannelsAllEnforced(t *testing.T) {
	d := barsDocument()
	first := straightChannel()
	second := Channel{
		ID:         "edge-lane",
		Centerline: []geom.Point{{X: 25, Y: 20}, {X: 25, Y: 80}},
		MinWidth:   30, // impossible: only 15 units from x=25 to the left-bar edge x=10
	}
	rep, err := Check(d, []Channel{first, second})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Passes || len(rep.Channels) != 2 {
		t.Fatal("one failing channel must fail the whole report")
	}
	if !rep.Channels[0].Passes || rep.Channels[1].Passes {
		t.Fatal("only the second channel should fail")
	}
}

func TestValidateRequestBounds(t *testing.T) {
	base := Move{ShapeID: "left-bar", From: geom.Point{X: 40, Y: 10}, To: geom.Point{X: 41, Y: 10}}
	ch := straightChannel()
	if err := validateRequest(nil, []Channel{ch}); err == nil {
		t.Fatal("empty moves rejected")
	}
	badCh := ch
	badCh.MinWidth = 0
	if err := validateRequest([]Move{base}, []Channel{badCh}); err == nil {
		t.Fatal("non-positive width rejected")
	}
	badCh2 := ch
	badCh2.Centerline = []geom.Point{{X: 1, Y: 1}}
	if err := validateRequest([]Move{base}, []Channel{badCh2}); err == nil {
		t.Fatal("single-point centreline rejected")
	}
}

func TestCentrelineSamplingIncludesVerticesAndBounds(t *testing.T) {
	line := []geom.Point{{X: 0, Y: 0}, {X: 10, Y: 0}, {X: 10, Y: 10}}
	samples := centrelineSamples(line, 4) // step = min(1, 2) = 1
	if samples[0] != line[0] || samples[len(samples)-1] != line[2] {
		t.Fatal("endpoints must be sampled")
	}
	foundVertex := false
	for _, p := range samples {
		if p == line[1] {
			foundVertex = true
		}
	}
	if !foundVertex {
		t.Fatal("polyline vertex must be sampled")
	}
	if len(samples) < 18 {
		t.Fatalf("sample count %d too coarse", len(samples))
	}
}
