// Package raster produces deterministic, supersampled PNG exposures.
package raster

import (
	"bytes"
	"context"
	"image"
	"image/color"
	"image/png"
	"prismplate/internal/geom"
	"prismplate/internal/paint"
	"prismplate/internal/scene"
)

// Render samples the compiled scene into output pixels. A nil target keeps the
// document size and the legacy sampling lattice. Otherwise the inverse target
// matrix maps each output sample back into document coordinates, so brushes,
// clips, masks and cutouts are evaluated under one shared layout; samples that
// land outside the document (contain margins) stay transparent.
func Render(ctx context.Context, c scene.Compiled, t *scene.Target) ([]byte, error) {
	d := c.Document
	w, h := d.Width, d.Height
	var inv geom.Matrix
	if t != nil {
		w, h = t.Width, t.Height
		inv, _ = t.Matrix(d.Width, d.Height).Inverse()
	}
	img := image.NewNRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		for x := 0; x < w; x++ {
			sum := paint.Pixel{}
			for sy := 0; sy < 2; sy++ {
				for sx := 0; sx < 2; sx++ {
					p := geom.Point{X: float64(x) + (float64(sx)+0.5)/2, Y: float64(y) + (float64(sy)+0.5)/2}
					if t != nil {
						p = inv.Apply(p)
						if p.X < 0 || p.Y < 0 || p.X >= float64(d.Width) || p.Y >= float64(d.Height) {
							continue
						}
					}
					dst := paint.From(d.Background)
					for _, l := range c.Layers {
						if len(l.Clip) > 0 && !l.Clip.Contains(p, "nonzero") {
							continue
						}
						if len(l.Mask) > 0 && !l.Mask.Contains(p, "nonzero") {
							continue
						}
						src := paint.Pixel{}
						for _, s := range l.Shapes {
							q := s.Inverse.Apply(p)
							if s.Path.Contains(q, s.Shape.FillRule) {
								src = paint.Over(src, paint.Sample(s.Shape.Brush, q), "normal")
							}
						}
						dst = paint.Over(dst, src.Scale(l.Layer.Opacity), l.Layer.Blend)
					}
					sum.R += dst.R / 4
					sum.G += dst.G / 4
					sum.B += dst.B / 4
					sum.A += dst.A / 4
				}
			}
			r, g, b, a := sum.RGBA8()
			img.SetNRGBA(x, y, color.NRGBA{R: r, G: g, B: b, A: a})
		}
	}
	var out bytes.Buffer
	err := png.Encode(&out, img)
	return out.Bytes(), err
}
