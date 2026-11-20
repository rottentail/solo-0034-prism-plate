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

// SampleAt composites one canvas-space point through every layer. It is shared
// by whole-canvas and region rendering so both perform identical sampling.
func SampleAt(c scene.Compiled, p geom.Point) paint.Pixel {
	d := c.Document
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
	return dst
}

// Render samples the requested canvas rectangle. The region's x/y are canvas
// coordinates, so sub-pixel sample positions, gradient sampling and clip/mask
// membership are identical to the same rectangle inside a whole-canvas render.
// Only pixels inside the region are sampled; geometry outside the region is
// still evaluated for every sample point (coverage is not pre-culled).
func Render(ctx context.Context, c scene.Compiled, region scene.Region) ([]byte, error) {
	img := image.NewNRGBA(image.Rect(0, 0, region.Width, region.Height))
	for y := 0; y < region.Height; y++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		for x := 0; x < region.Width; x++ {
			canvasX, canvasY := region.X+x, region.Y+y
			sum := paint.Pixel{}
			for sy := 0; sy < 2; sy++ {
				for sx := 0; sx < 2; sx++ {
					p := geom.Point{X: float64(canvasX) + (float64(sx)+0.5)/2, Y: float64(canvasY) + (float64(sy)+0.5)/2}
					dst := SampleAt(c, p)
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
