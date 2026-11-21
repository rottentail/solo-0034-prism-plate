// Package migrate converts a legacy straight-sRGB document to the explicit
// linear-premultiplied color model.
//
// Conversion is render-equivalent: the compositor already linearizes and
// premultiplies straight sRGB input before blending, so both models produce the
// same premultiplied Pixel for every stored color with alpha > 0. At alpha == 0
// the stored RGB is multiplied by zero in both models, so it also renders
// identically; the only information that the new representation drops is the
// hidden RGB value itself, which no pixel and no legacy artifact ever exposed.
package migrate

import (
	"prismplate/internal/paint"
	"prismplate/internal/scene"
)

// toLinearPremul converts every stored color of a document. It does not touch
// geometry, transforms, stop offsets, clip, mask, blend or opacity.
func toLinearPremul(d scene.Document) scene.Document {
	out := d
	out.Background = paint.Premultiplied(d.Background)
	out.ColorModel = &scene.ColorModel{Version: 2, Space: "linear-srgb", Alpha: "premultiplied"}
	out.Layers = make([]scene.Layer, len(d.Layers))
	for i, l := range d.Layers {
		nl := l
		nl.Shapes = make([]scene.Shape, len(l.Shapes))
		for j, s := range l.Shapes {
			ns := s
			ns.Brush = s.Brush
			if s.Brush.Kind == "linear" {
				ns.Brush.Stops = make([]scene.Stop, len(s.Brush.Stops))
				for k, st := range s.Brush.Stops {
					ns.Brush.Stops[k] = scene.Stop{At: st.At, Color: paint.Premultiplied(st.Color)}
				}
			} else {
				ns.Brush.Color = paint.Premultiplied(s.Brush.Color)
			}
			nl.Shapes[j] = ns
		}
		out.Layers[i] = nl
	}
	return out
}

// Upgrade returns the versioned document equivalent to a legacy document. A
// document that already carries a color model is returned unchanged along with
// false, so callers can treat it as a no-op.
func Upgrade(d scene.Document) (scene.Document, bool) {
	if d.ColorModel != nil {
		return d, false
	}
	return toLinearPremul(d), true
}
