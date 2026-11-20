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

// Sample is the canonical composition for one layout-space point. It is the
// single definition shared by PNG exposure and delivery verification, so a
// reported error bound always describes the actual output backend.
func Sample(c scene.Compiled, p geom.Point) paint.Pixel {
	dst := paint.From(c.Document.Background)
	for _, l := range c.Layers {
		if len(l.Clip) > 0 && !l.Clip.Contains(p, "nonzero") {
			continue
		}
		if len(l.Mask) > 0 && !l.Mask.Contains(p, "nonzero") {
			continue
		}
		dst = paint.Composite(dst, func() paint.Pixel {
			src := paint.Pixel{}
			for _, s := range l.Shapes {
				q := s.Inverse.Apply(p)
				if s.Path.Contains(q, s.Shape.FillRule) {
					src = paint.Over(src, paint.Sample(s.Shape.Brush, q), "normal")
				}
			}
			return src
		}, l.Layer.Opacity, l.Layer.Blend)
	}
	return dst
}

// RenderImage produces the canonical NRGBA exposure. Delivery verification
// compares these pixels directly, so the published error bound describes the
// same bytes a receiver's PNG would contain.
func RenderImage(ctx context.Context, c scene.Compiled) (*image.NRGBA, error) {
	d := c.Document
	img := image.NewNRGBA(image.Rect(0, 0, d.Width, d.Height))
	for y := 0; y < d.Height; y++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		for x := 0; x < d.Width; x++ {
			sum := paint.Pixel{}
			for sy := 0; sy < 2; sy++ {
				for sx := 0; sx < 2; sx++ {
					p := geom.Point{X: float64(x) + (float64(sx)+0.5)/2, Y: float64(y) + (float64(sy)+0.5)/2}
					dst := Sample(c, p)
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
	return img, nil
}

func Render(ctx context.Context, c scene.Compiled) ([]byte, error) {
	img, err := RenderImage(ctx, c)
	if err != nil {
		return nil, err
	}
	var out bytes.Buffer
	err = png.Encode(&out, img)
	return out.Bytes(), err
}
