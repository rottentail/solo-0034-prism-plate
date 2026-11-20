package validate

import (
	"fmt"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"strings"
	"testing"
)

func solidBrush() scene.Brush {
	return scene.Brush{Kind: "solid", Color: scene.Color{R: 0.8, G: 0.2, B: 0.2, A: 1}}
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

// cubicRing builds one closed contour of k cubic verbs (24 flattened edges
// each) whose vertices all sit around (ox, oy), far outside a small canvas when
// ox/oy are large.
func cubicRing(ox, oy float64, k int) []geom.Op {
	ops := []geom.Op{{Kind: "M", Points: []geom.Point{{X: ox, Y: oy}}}}
	for i := 0; i < k; i++ {
		ops = append(ops, geom.Op{Kind: "C", Points: []geom.Point{
			{X: ox + 5, Y: oy + 7}, {X: ox + 9, Y: oy + 3}, {X: ox + float64(i%7), Y: oy + 2},
		}})
	}
	ops = append(ops,
		geom.Op{Kind: "L", Points: []geom.Point{{X: ox, Y: oy}}},
		geom.Op{Kind: "Z"})
	return ops
}

func shape(id string, ops []geom.Op, m geom.Matrix) scene.Shape {
	return scene.Shape{ID: id, Path: ops, FillRule: "nonzero", Brush: solidBrush(), Transform: m}
}

func docAt(w, h int, shapes ...scene.Shape) scene.Document {
	return scene.Document{
		Width:      w,
		Height:     h,
		Background: scene.Color{R: 0.05, G: 0.05, B: 0.08, A: 1},
		Layers:     []scene.Layer{{ID: "sheet", Opacity: 1, Blend: "normal", Shapes: shapes}},
	}
}

// maxCubicsPerPath is the most cubic verbs one path may carry under the
// 256-verb structural limit (M + k C + L + Z <= 256).
const maxCubicsPerPath = 253

func TestInvisibleContoursNoLongerConsumeRenderBudget(t *testing.T) {
	// 6074 edges sitting entirely off the 512x512 canvas. Under the old rule
	// this exceeded 160M edge tests and was rejected; now its visible cost is 0.
	d := docAt(512, 512, shape("off", cubicRing(9000, 9000, maxCubicsPerPath), geom.Identity))
	c, err := Compile(d)
	if err != nil {
		t.Fatalf("off-canvas scene must be admitted, got: %v", err)
	}
	if c.Segments == 0 || c.VisibleSegments != 0 {
		t.Fatalf("segments=%d visible=%d, want full count but zero visible", c.Segments, c.VisibleSegments)
	}
	if c.Layers[0].Visible || c.Layers[0].Shapes[0].Visible {
		t.Fatal("off-canvas shape and its layer must compile as invisible")
	}
}

func TestCanvasSpanningComplexCurveStillRejected(t *testing.T) {
	// The same edge count expressed on-canvas crosses the whole frame and must
	// still be bounded: a few complex curves cannot escape the render budget.
	d := docAt(512, 512, shape("spanner", cubicRing(1, 1, maxCubicsPerPath), geom.Identity))
	c, err := Compile(d)
	if err == nil {
		t.Fatalf("on-canvas complex curve must be rejected, visible=%d", c.VisibleSegments)
	}
	if !strings.Contains(err.Error(), "bounded rendering work") {
		t.Fatalf("unexpected rejection reason: %v", err)
	}
}

func TestMixedSceneChargesOnlyVisibleContours(t *testing.T) {
	visible := shape("visible", rectOps(2, 2, 12, 12), geom.Identity)
	invisible := shape("invisible", cubicRing(700, 700, 40), geom.Identity)
	c, err := Compile(docAt(64, 64, visible, invisible))
	if err != nil {
		t.Fatalf("mixed scene must be admitted: %v", err)
	}
	sh := c.Layers[0].Shapes
	if !sh[0].Visible || sh[1].Visible {
		t.Fatalf("visibility flags wrong: %+v", sh)
	}
	if !c.Layers[0].Visible {
		t.Fatal("layer with one visible shape must stay visible")
	}
	if c.VisibleSegments != 4 {
		t.Fatalf("only the 4-edge rectangle is visible work, got %d", c.VisibleSegments)
	}
	if c.Segments <= c.VisibleSegments {
		t.Fatal("total segments must still include the invisible contour")
	}
}

func TestAllInvisibleLayerIsSkippable(t *testing.T) {
	d := docAt(64, 64, shape("gone", rectOps(200, 200, 240, 240), geom.Identity))
	c, err := Compile(d)
	if err != nil {
		t.Fatalf("admission failed: %v", err)
	}
	if c.Layers[0].Visible || c.VisibleSegments != 0 {
		t.Fatalf("layer must compile invisible: layer=%v visibleSeg=%d", c.Layers[0].Visible, c.VisibleSegments)
	}
}

func TestOffCanvasClipHidesLayerButGatesNeverRoughCulled(t *testing.T) {
	shapeOK := shape("inside", rectOps(10, 10, 30, 30), geom.Identity)

	hidden := docAt(64, 64, shapeOK)
	hidden.Layers[0].Clip = rectOps(200, 200, 260, 260) // entirely outside
	c, err := Compile(hidden)
	if err != nil {
		t.Fatalf("admission failed: %v", err)
	}
	if c.Layers[0].Visible || c.Layers[0].Shapes[0].Visible || c.VisibleSegments != 0 {
		t.Fatal("an off-canvas clip hides the whole layer")
	}

	// A clip merely grazing the frame keeps the layer visible.
	grazing := docAt(64, 64, shapeOK)
	grazing.Layers[0].Clip = rectOps(-40, -40, 0, 64) // edge lies exactly on x=0
	c2, err := Compile(grazing)
	if err != nil {
		t.Fatalf("admission failed: %v", err)
	}
	if !c2.Layers[0].Visible {
		t.Fatal("a clip touching the canvas edge must not be rough-clipped")
	}
}

func TestHolesAndNearBoundaryTransformsStayVisible(t *testing.T) {
	// Even-odd fill: outer ring overlaps the canvas while the hole ring sits
	// off-canvas. The union bound keeps the shape visible.
	holed := []geom.Op{}
	holed = append(holed, rectOps(-30, -30, 40, 40)...)
	holed = append(holed, rectOps(500, 500, 540, 540)...)
	d := docAt(64, 64, shape("holed", holed, geom.Identity))
	d.Layers[0].Shapes[0].FillRule = "evenodd"
	c, err := Compile(d)
	if err != nil {
		t.Fatalf("admission failed: %v", err)
	}
	if !c.Layers[0].Shapes[0].Visible {
		t.Fatal("a shape whose outer ring crosses the frame must stay visible despite an off-canvas hole")
	}

	// Translate a small shape to within a fraction of a pixel outside the edge:
	// the conservative margin keeps it admissible and visible.
	near := docAt(64, 64, shape("near", rectOps(0, 10, 8, 20),
		geom.Matrix{1, 0, 0, 1, -7.999999999, 0}))
	if c, err = Compile(near); err != nil || !c.Layers[0].Shapes[0].Visible {
		t.Fatalf("near-boundary transform must stay visible: c=%+v err=%v", c.Layers, err)
	}

	// A clearly separated translation is provably invisible, not clipped.
	far := docAt(64, 64, shape("far", rectOps(0, 10, 8, 20),
		geom.Matrix{1, 0, 0, 1, -200, 0}))
	if c, err = Compile(far); err != nil || c.Layers[0].Shapes[0].Visible {
		t.Fatalf("far translation must compile invisible: c=%+v err=%v", c.Layers, err)
	}
}

func TestStructuralMaximumStaysUnderFlattenCap(t *testing.T) {
	// Fill every structural slot (16 layers x (32 shapes + clip + mask)) with
	// maximally complex off-canvas paths. The flatten cap must sit above the
	// structural ceiling so geometry-only work, not an arbitrary number, bounds
	// admission of fully invisible scenes.
	d := scene.Document{
		Width:      512,
		Height:     512,
		Background: scene.Color{A: 1},
	}
	for li := 0; li < 16; li++ {
		l := scene.Layer{ID: fmt.Sprintf("layer-%02d", li), Opacity: 1, Blend: "normal"}
		l.Clip = cubicRing(9000, 9000, maxCubicsPerPath)
		l.Mask = cubicRing(9000, 9000, maxCubicsPerPath)
		for si := 0; si < 32; si++ {
			l.Shapes = append(l.Shapes, shape(fmt.Sprintf("shape-%02d-%02d", li, si),
				cubicRing(9000, 9000, maxCubicsPerPath), geom.Identity))
		}
		d.Layers = append(d.Layers, l)
	}
	c, err := Compile(d)
	if err != nil {
		t.Fatalf("structural maximum must remain admissible: %v", err)
	}
	if c.VisibleSegments != 0 {
		t.Fatalf("fully off-canvas scene has zero visible work, got %d", c.VisibleSegments)
	}
	if c.Segments >= maxFlattenedSegments {
		t.Fatalf("structural ceiling %d must stay below flatten cap %d", c.Segments, maxFlattenedSegments)
	}
}
