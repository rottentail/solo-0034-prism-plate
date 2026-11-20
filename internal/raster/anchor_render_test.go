package raster

import (
	"bytes"
	"context"
	"prismplate/internal/anchor"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"prismplate/internal/vector"
	"strings"
	"testing"
)

func gradientDoc() scene.Document {
	return scene.Document{
		Width: 80, Height: 60,
		Background: scene.Color{R: 0.1, G: 0.1, B: 0.1, A: 1},
		Layers: []scene.Layer{{
			ID: "l", Opacity: 1, Blend: "normal",
			Shapes: []scene.Shape{{
				ID: "s", FillRule: "nonzero",
				Path: []geom.Op{
					{Kind: "M", Points: []geom.Point{{X: 0, Y: 0}}},
					{Kind: "L", Points: []geom.Point{{X: 30, Y: 0}}},
					{Kind: "L", Points: []geom.Point{{X: 30, Y: 24}}},
					{Kind: "L", Points: []geom.Point{{X: 0, Y: 24}}},
					{Kind: "Z"},
				},
				Transform: geom.Matrix{2.2, 0.25, -0.15, 0.6, 6, 4},
				Brush: scene.Brush{
					Kind: "linear", Anchor: scene.AnchorObject,
					From: geom.Point{X: 0, Y: 0}, To: geom.Point{X: 30, Y: 24},
					Stops: []scene.Stop{
						{At: 0, Color: scene.Color{R: 1, A: 1}},
						{At: 1, Color: scene.Color{B: 1, A: 1}},
					},
				},
			}},
		}},
	}
}

func TestAnchorConversionRendersIdenticalPNG(t *testing.T) {
	doc := gradientDoc()
	compiled, err := validate.Compile(doc)
	if err != nil {
		t.Fatal(err)
	}
	pngObject, err := Render(context.Background(), compiled)
	if err != nil {
		t.Fatal(err)
	}
	svgObject := vector.Export(compiled)
	if strings.Contains(string(svgObject), "gradientTransform") {
		t.Fatal("object-anchored SVG must keep the historical markup without gradientTransform")
	}

	res, err := anchor.Convert(doc.Layers[0].Shapes[0].Brush, doc.Layers[0].Shapes[0].Transform,
		doc.Layers[0].Shapes[0].Path, "l", "s", scene.AnchorCanvas)
	if err != nil {
		t.Fatal(err)
	}
	doc.Layers[0].Shapes[0].Brush = res.Brush
	compiled2, err := validate.Compile(doc)
	if err != nil {
		t.Fatal(err)
	}
	pngCanvas, err := Render(context.Background(), compiled2)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(pngObject, pngCanvas) {
		t.Fatal("PNG pixels changed across an exact anchor conversion")
	}
	svgCanvas := vector.Export(compiled2)
	if !strings.Contains(string(svgCanvas), "gradientTransform=") {
		t.Fatal("canvas-anchored SVG must carry an inverse gradientTransform")
	}
	// Object and canvas SVGs necessarily differ in markup but share geometry.
	if bytes.Equal(svgObject, svgCanvas) {
		t.Fatal("SVG markup should distinguish the two anchorings")
	}
}
