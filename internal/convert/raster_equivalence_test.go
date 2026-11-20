package convert

import (
	"bytes"
	"context"
	"image/png"
	"prismplate/internal/geom"
	"prismplate/internal/raster"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"testing"
)

// renderPNG compiles and renders a document through the production raster
// pipeline, returning decoded RGBA bytes for pixel-level image comparison.
func renderPNG(t *testing.T, doc scene.Document) []byte {
	t.Helper()
	compiled, err := validate.Compile(doc)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := raster.Render(context.Background(), compiled)
	if err != nil {
		t.Fatal(err)
	}
	img, err := png.Decode(bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	bounds := img.Bounds()
	var out []byte
	for y := bounds.Min.Y; y < bounds.Max.Y; y++ {
		for x := bounds.Min.X; x < bounds.Max.X; x++ {
			r, g, b, a := img.At(x, y).RGBA()
			out = append(out, byte(r>>8), byte(g>>8), byte(b>>8), byte(a>>8))
		}
	}
	return out
}

func pixelDiff(a, b []byte) int {
	n := 0
	for i := range a {
		if a[i] != b[i] {
			n++
		}
	}
	return n
}

// RasterEquivalence is the image-level counterpart of the geometry grid check:
// source and converted documents must render byte-identical PNGs through the
// real four-sample renderer when geometry is exactly preserved.
func assertRasterEquivalent(t *testing.T, doc scene.Document, layerID, shapeID, target string) {
	t.Helper()
	before := renderPNG(t, doc)
	res, err := Document("p1", layerID, shapeID, target, doc)
	if err != nil {
		t.Fatal(err)
	}
	after := renderPNG(t, res.Document)
	if d := pixelDiff(before, after); d != 0 {
		t.Fatalf("PNG pixels changed after %s->%s: %d differing channels", res.Report.SourceRule, target, d)
	}
}

func TestRasterEquivalenceNestedHole(t *testing.T) {
	p := geom.Path{rectCW(2, 2, 62, 62), rectCW(14, 14, 50, 50)}
	doc := scene.Document{Width: 64, Height: 64, Background: scene.Color{R: 0.05, G: 0.06, B: 0.1, A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{
			{ID: "s1", FillRule: "evenodd", Path: pathOps(p), Transform: geom.Identity,
				Brush: scene.Brush{Kind: "solid", Color: scene.Color{R: 0.9, G: 0.4, B: 0.2, A: 1}}}}}}}
	assertRasterEquivalent(t, doc, "l1", "s1", "nonzero")
}

func TestRasterEquivalenceOverlapAndBowtie(t *testing.T) {
	overlap := geom.Path{rectCW(4, 8, 36, 56), rectCW(28, 8, 60, 56)}
	doc1 := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{
			{ID: "s1", FillRule: "nonzero", Path: pathOps(overlap), Transform: geom.Identity,
				Brush: scene.Brush{Kind: "solid", Color: scene.Color{R: 0.2, G: 0.7, B: 0.9, A: 1}}}}}}}
	assertRasterEquivalent(t, doc1, "l1", "s1", "evenodd")

	bow := geom.Contour{{X: 6, Y: 6}, {X: 58, Y: 58}, {X: 58, Y: 6}, {X: 6, Y: 58}}
	doc2 := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{
			{ID: "s1", FillRule: "evenodd", Path: pathOps(geom.Path{bow}), Transform: geom.Identity,
				Brush: scene.Brush{Kind: "solid", Color: scene.Color{R: 0.8, G: 0.2, B: 0.5, A: 1}}}}}}}
	assertRasterEquivalent(t, doc2, "l1", "s1", "nonzero")
}

// A mirror transform must still produce identical rendered output after the
// rule swap because conversion is performed in local coordinates.
func TestRasterEquivalenceUnderMirror(t *testing.T) {
	p := geom.Path{rectCW(2, 2, 62, 62), rectCW(14, 14, 50, 50)}
	doc := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{
			{ID: "s1", FillRule: "evenodd", Path: pathOps(p),
				Transform: geom.Matrix{-1, 0, 0, 1, 64, 0},
				Brush:     scene.Brush{Kind: "solid", Color: scene.Color{R: 0.6, G: 0.8, B: 0.3, A: 1}}}}}}}
	assertRasterEquivalent(t, doc, "l1", "s1", "nonzero")
}

// Layer clip and a second shape must remain untouched and still render.
func TestRasterPreservesClipAndSiblingShapes(t *testing.T) {
	p := geom.Path{rectCW(2, 2, 62, 62), rectCW(14, 14, 50, 50)}
	clip := geom.Path{rectCW(0, 0, 40, 64)}
	doc := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Clip: pathOps(clip), Shapes: []scene.Shape{
			{ID: "target", FillRule: "evenodd", Path: pathOps(p), Transform: geom.Identity,
				Brush: scene.Brush{Kind: "solid", Color: scene.Color{R: 0.9, G: 0.3, B: 0.2, A: 1}}},
			{ID: "sibling", FillRule: "nonzero", Path: pathOps(geom.Path{rectCW(48, 48, 60, 60)}), Transform: geom.Identity,
				Brush: scene.Brush{Kind: "solid", Color: scene.Color{R: 0.2, G: 0.9, B: 0.4, A: 1}}},
		}}}}
	res, err := Document("p1", "l1", "target", "nonzero", doc)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Document.Layers[0].Clip) != len(doc.Layers[0].Clip) {
		t.Fatalf("layer clip changed during conversion")
	}
	if res.Document.Layers[0].Shapes[1].ID != "sibling" ||
		res.Document.Layers[0].Shapes[1].FillRule != "nonzero" {
		t.Fatalf("sibling shape mutated")
	}
	assertRasterEquivalent(t, doc, "l1", "target", "nonzero")
}
