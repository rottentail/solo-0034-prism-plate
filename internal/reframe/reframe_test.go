package reframe

import (
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"testing"
)

func square(id string, x, y, size float64) scene.Shape {
	ops := []geom.Op{
		{Kind: "M", Points: []geom.Point{{X: x, Y: y}}},
		{Kind: "L", Points: []geom.Point{{X: x + size, Y: y}}},
		{Kind: "L", Points: []geom.Point{{X: x + size, Y: y + size}}},
		{Kind: "L", Points: []geom.Point{{X: x, Y: y + size}}},
		{Kind: "Z"},
	}
	return scene.Shape{ID: id, Path: ops, FillRule: "nonzero", Transform: geom.Identity, Brush: scene.Brush{Kind: "solid", Color: scene.Color{R: 1, A: 1}}}
}

func sample() scene.Document {
	return scene.Document{
		Width: 200, Height: 100,
		Background: scene.Color{A: 1},
		Layers: []scene.Layer{{
			ID: "sheet", Opacity: 1, Blend: "normal",
			Clip: []geom.Op{
				{Kind: "M", Points: []geom.Point{{X: 0, Y: 0}}},
				{Kind: "L", Points: []geom.Point{{X: 200, Y: 0}}},
				{Kind: "L", Points: []geom.Point{{X: 200, Y: 100}}},
				{Kind: "L", Points: []geom.Point{{X: 0, Y: 100}}},
				{Kind: "Z"},
			},
			Shapes: []scene.Shape{
				square("centre", 60, 20, 60),
				square("edge", 190, 25, 8),
			},
		}},
	}
}

func TestPlacementModes(t *testing.T) {
	src := Size{200, 100}
	target := Size{100, 100}
	cases := []struct {
		mode   Mode
		scale  float64
		offset geom.Point
	}{
		{ModeFit, 0.5, geom.Point{X: 0, Y: 25}},
		{ModePreserve, 1, geom.Point{X: -50, Y: 0}},
		{ModeCrop, 1, geom.Point{X: -50, Y: 0}},
	}
	for _, c := range cases {
		p, err := Plan(c.mode, src, target)
		if err != nil {
			t.Fatalf("%s: %v", c.mode, err)
		}
		if math.Abs(p.Scale-c.scale) > 1e-9 || math.Abs(p.OffsetX-c.offset.X) > 1e-9 || math.Abs(p.OffsetY-c.offset.Y) > 1e-9 {
			t.Fatalf("%s: got %+v", c.mode, p)
		}
	}
}

// A source->target mapping must equal "apply the shape transform, then the
// canvas affine". Composing in the wrong order silently shifts artwork.
func TestComposeMatchesSequentialApply(t *testing.T) {
	canvas := geom.Matrix{0.5, 0, 0, 0.5, 10, 20}
	local := geom.Matrix{1, 0.2, -0.1, 1, 7, -3}
	p := geom.Point{X: 13, Y: 17}
	want := canvas.Apply(local.Apply(p))
	got := compose(canvas, local).Apply(p)
	if math.Abs(got.X-want.X) > 1e-9 || math.Abs(got.Y-want.Y) > 1e-9 {
		t.Fatalf("compose got %+v want %+v", got, want)
	}
}

// Canvas-space clip/mask move by the affine; local paths and brushes must not.
func TestCoordinatesRespectSpaces(t *testing.T) {
	src := sample()
	pv, err := Apply(src, Size{100, 100}, ModeFit)
	if err != nil {
		t.Fatal(err)
	}
	// centre spans 60..120 x, 20..80 y; after a 0.5 fit + 25 margin it lands
	// at 30..60, 35..65 and is entirely inside the target.
	if pv.Document.Layers[0].Clip[1].Points[0].X != 100 {
		t.Fatalf("clip point should scale to 100, got %v", pv.Document.Layers[0].Clip[1].Points[0].X)
	}
	if pv.Document.Layers[0].Clip[1].Points[0].Y != 25 {
		t.Fatalf("clip point should move to y=25, got %v", pv.Document.Layers[0].Clip[1].Points[0].Y)
	}
	shape := pv.Document.Layers[0].Shapes[0]
	if shape.Path[1].Points[0].X != 120 {
		t.Fatalf("shape path must stay shape-local, got %v", shape.Path[1].Points[0].X)
	}
	if shape.Transform[0] != 0.5 || shape.Transform[5] != 25 {
		t.Fatalf("shape transform must absorb the canvas affine, got %v", shape.Transform)
	}
	if pv.Document.Layers[0].Shapes[0].Brush.Kind != "solid" {
		t.Fatal("brush kind must be untouched")
	}
}

func TestDetailIsReported(t *testing.T) {
	src := sample()
	// Crop to a square keeps the centre square and trims the right-hand edge.
	pv, err := Apply(src, Size{100, 100}, ModeCrop)
	if err != nil {
		t.Fatal(err)
	}
	if pv.Document.Width != 100 || pv.Document.Height != 100 {
		t.Fatalf("target canvas not applied: %dx%d", pv.Document.Width, pv.Document.Height)
	}
	var centre, edge *ShapeImpact
	for i := range pv.Shapes {
		switch pv.Shapes[i].Shape {
		case "centre":
			centre = &pv.Shapes[i]
		case "edge":
			edge = &pv.Shapes[i]
		}
	}
	if centre == nil || edge == nil {
		t.Fatal("missing shape impacts")
	}
	if centre.Effect != EffectIntact {
		t.Fatalf("centre should survive, got %s", centre.Effect)
	}
	// edge spans x=190..198, after crop offset -50 => 140..148, fully outside.
	if edge.Effect != EffectRemoved || edge.Visible {
		t.Fatalf("edge should be removed, got %s visible=%v", edge.Effect, edge.Visible)
	}
	found := false
	for _, id := range pv.Detail.RemovedShapes {
		if id == "edge" {
			found = true
		}
	}
	if !found {
		t.Fatalf("removed detail not reported: %+v", pv.Detail)
	}
}

func TestSourceUnchanged(t *testing.T) {
	src := sample()
	before := DocumentDigest(src)
	if _, err := Apply(src, Size{100, 100}, ModeFit); err != nil {
		t.Fatal(err)
	}
	if DocumentDigest(src) != before {
		t.Fatal("Apply mutated its input")
	}
}

func TestPreviewDigestStable(t *testing.T) {
	src := sample()
	a, _ := Apply(src, Size{100, 100}, ModeFit)
	b, _ := Apply(src, Size{100, 100}, ModeFit)
	if a.Digest != b.Digest {
		t.Fatal("preview digest is not deterministic")
	}
	c, _ := Apply(src, Size{100, 100}, ModeCrop)
	if a.Digest == c.Digest {
		t.Fatal("different modes must not share a digest")
	}
}
