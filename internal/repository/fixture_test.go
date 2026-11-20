package repository

import (
	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

// documentFixture builds a minimal valid document with one solid square shape.
func documentFixture() scene.Document {
	ops := []geom.Op{
		{Kind: "M", Points: []geom.Point{{X: 0, Y: 0}}},
		{Kind: "L", Points: []geom.Point{{X: 10, Y: 0}}},
		{Kind: "L", Points: []geom.Point{{X: 10, Y: 10}}},
		{Kind: "L", Points: []geom.Point{{X: 0, Y: 10}}},
		{Kind: "Z"},
	}
	return scene.Document{
		Width: 16, Height: 16,
		Background: scene.Color{R: 1, G: 1, B: 1, A: 1},
		Layers: []scene.Layer{{
			ID: "L1", Opacity: 1, Blend: "normal",
			Shapes: []scene.Shape{{
				ID:        "s1",
				Path:      scene.PathRef{Source: scene.PathSourceLegacy, Ops: ops},
				FillRule:  "nonzero",
				Brush:     scene.Brush{Kind: "solid", Color: scene.Color{R: 1, A: 1}},
				Transform: geom.Identity,
			}},
		}},
	}
}
