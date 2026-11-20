package raster

import (
	"bytes"
	"context"
	"fmt"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"testing"
	"time"
)

func rectOps(x0, y0, x1, y1 float64) []geom.Op {
	return []geom.Op{
		{Kind: "M", Points: []geom.Point{{X: x0, Y: y0}}},
		{Kind: "L", Points: []geom.Point{{X: x1, Y: y0}}},
		{Kind: "L", Points: []geom.Point{{X: x1, Y: y1}}},
		{Kind: "L", Points: []geom.Point{{X: x0, Y: y1}}},
		{Kind: "Z"},
	}
}

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

func shape(id string, ops []geom.Op) scene.Shape {
	return scene.Shape{ID: id, Path: ops, FillRule: "nonzero",
		Brush:     scene.Brush{Kind: "solid", Color: scene.Color{R: 0.7, G: 0.3, B: 0.3, A: 1}},
		Transform: geom.Identity}
}

func doc(shapes ...scene.Shape) scene.Document {
	return scene.Document{Width: 96, Height: 96,
		Background: scene.Color{R: 0.04, G: 0.05, B: 0.09, A: 1},
		Layers:     []scene.Layer{{ID: "sheet", Opacity: 1, Blend: "normal", Shapes: shapes}}}
}

func render(t *testing.T, d scene.Document) []byte {
	t.Helper()
	c, err := validate.Compile(d)
	if err != nil {
		t.Fatalf("compile: %v", err)
	}
	png, err := Render(context.Background(), c)
	if err != nil {
		t.Fatalf("render: %v", err)
	}
	return png
}

func TestInvisibleContoursDoNotChangePixels(t *testing.T) {
	base := doc(shape("square", rectOps(20, 20, 70, 70)))
	withHidden := doc(
		shape("square", rectOps(20, 20, 70, 70)),
		shape("hidden-a", cubicRing(9000, 9000, 253)),
		shape("hidden-b", cubicRing(-9000, -9000, 128)),
	)
	a, b := render(t, base), render(t, withHidden)
	if !bytes.Equal(a, b) {
		t.Fatal("provably invisible contours must not alter a single pixel")
	}
}

func TestFullyInvisibleSceneRendersBackgroundOnly(t *testing.T) {
	background := doc(shape("far", cubicRing(8000, 8000, 200)))
	// Compare against a hand-compiled background render: a document with no
	// layers is itself structurally invalid, so it cannot go through Compile.
	bgCompiled := scene.Compiled{Document: scene.Document{Width: 96, Height: 96, Background: background.Background}}
	want, err := Render(context.Background(), bgCompiled)
	if err != nil {
		t.Fatalf("background render: %v", err)
	}
	if got := render(t, background); !bytes.Equal(got, want) {
		t.Fatal("fully invisible scene must produce the exact background image")
	}
}

func TestOffCanvasClipAndMaskYieldBackgroundAndStayDeterministic(t *testing.T) {
	d := doc(shape("square", rectOps(10, 10, 80, 80)))
	d.Layers[0].Clip = rectOps(400, 400, 500, 500)
	first := render(t, d)
	second := render(t, d)
	if !bytes.Equal(first, second) {
		t.Fatal("rendering must stay deterministic")
	}
	bgCompiled := scene.Compiled{Document: scene.Document{Width: 96, Height: 96, Background: d.Background}}
	want, err := Render(context.Background(), bgCompiled)
	if err != nil {
		t.Fatalf("background render: %v", err)
	}
	if !bytes.Equal(first, want) {
		t.Fatal("a layer hidden by an off-canvas clip must contribute nothing")
	}
}

func TestInvisibleGeometryKeepsBoundedTime(t *testing.T) {
	// 16 layers x 33 maximal off-canvas paths: millions of flattened edges,
	// zero visible work. The rasterizer must still finish well inside the
	// exposure deadline rather than traverse every hidden edge per sample.
	d := scene.Document{Width: 512, Height: 512, Background: scene.Color{A: 1}}
	for li := 0; li < 16; li++ {
		l := scene.Layer{ID: fmt.Sprintf("layer-%02d", li), Opacity: 1, Blend: "normal",
			Clip: cubicRing(9000, 9000, 253)}
		for si := 0; si < 32; si++ {
			l.Shapes = append(l.Shapes, shape(fmt.Sprintf("shape-%02d-%02d", li, si),
				cubicRing(9000, 9000, 253)))
		}
		d.Layers = append(d.Layers, l)
	}
	c, err := validate.Compile(d)
	if err != nil {
		t.Fatalf("compile: %v", err)
	}
	if c.VisibleSegments != 0 {
		t.Fatalf("want zero visible work, got %d", c.VisibleSegments)
	}
	start := time.Now()
	if _, err := Render(context.Background(), c); err != nil {
		t.Fatalf("render: %v", err)
	}
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Fatalf("fully invisible 512x512 scene took %v, exceeding the bounded budget", elapsed)
	}
}
