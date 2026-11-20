package coverage

import (
	"bytes"
	"image"
	"image/color"
	"image/png"
	"prismplate/internal/geom"
	"prismplate/internal/paint"
	"prismplate/internal/scene"
)

// regionBlock matches the aggregation block used by FamilyStat.Regions.
const regionBlock = 8

// overlayPalette assigns each family index a deterministic, distinguishable
// marker hue (cycled). Colors are chosen to stay readable on dark and light
// proofs and never reuse the proof's own pixel values without an outline.
var overlayPalette = []color.NRGBA{
	{R: 255, G: 87, B: 87, A: 255},
	{R: 87, G: 187, B: 255, A: 255},
	{R: 138, G: 220, B: 96, A: 255},
	{R: 238, G: 196, B: 72, A: 255},
	{R: 206, G: 126, B: 255, A: 255},
	{R: 255, G: 152, B: 76, A: 255},
	{R: 96, G: 230, B: 214, A: 255},
	{R: 244, G: 144, B: 196, A: 255},
}

// Overlay renders the canonical PNG proof and draws, for every family, the
// outline of each reported 8x8 contribution block plus a filled translucent
// interior tint. A one-pixel white halo is drawn first so markers stay
// visible over both dark and light underlying pixels. The image is the same
// dimensions as the authoritative proof and is deterministic for a fixed
// report; it is a localization aid, not a second rendering contract.
func Overlay(c scene.Compiled, rep Report) ([]byte, error) {
	d := c.Document
	img := image.NewNRGBA(image.Rect(0, 0, d.Width, d.Height))
	// Reconstruct the exact proof pixels through the same evaluator and 2x2
	// coverage sampling raster.Render uses, so the base image is identical to
	// the authoritative PNG.
	for y := 0; y < d.Height; y++ {
		for x := 0; x < d.Width; x++ {
			sum := paint.Pixel{}
			for sy := 0; sy < 2; sy++ {
				for sx := 0; sx < 2; sx++ {
					p := geom.Point{X: float64(x) + (float64(sx)+0.5)/2, Y: float64(y) + (float64(sy)+0.5)/2}
					dst, _ := paint.Evaluate(d.Background, c.Layers, p)
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

	for fi, fam := range rep.Families {
		marker := overlayPalette[fi%len(overlayPalette)]
		tint := color.NRGBA{R: marker.R, G: marker.G, B: marker.B, A: 72}
		halo := color.NRGBA{R: 255, G: 255, B: 255, A: 220}
		for _, reg := range fam.Regions {
			w := minInt(regionBlock, d.Width-reg.X)
			h := minInt(regionBlock, d.Height-reg.Y)
			drawBlock(img, reg.X, reg.Y, w, h, tint, marker, halo)
		}
	}

	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// drawBlock paints a translucent fill, a white halo and then the marker
// outline on one rectangular region. Edge regions may be narrower or shorter
// than regionBlock when the canvas is not divisible by it.
func drawBlock(img *image.NRGBA, ox, oy, w, h int, tint, marker, halo color.NRGBA) {
	if w <= 0 || h <= 0 {
		return
	}
	for y := oy; y < oy+h; y++ {
		for x := ox; x < ox+w; x++ {
			alphaOver(img, x, y, tint)
		}
	}
	// Halo one pixel outside the block, then marker border inside it.
	for t := 0; t < w; t++ {
		plotHalo(img, ox+t, oy-1, halo)
		plotHalo(img, ox+t, oy+h, halo)
		img.SetNRGBA(ox+t, oy, marker)
		img.SetNRGBA(ox+t, oy+h-1, marker)
	}
	for t := 0; t < h; t++ {
		plotHalo(img, ox-1, oy+t, halo)
		plotHalo(img, ox+w, oy+t, halo)
		img.SetNRGBA(ox, oy+t, marker)
		img.SetNRGBA(ox+w-1, oy+t, marker)
	}
}

func plotHalo(img *image.NRGBA, x, y int, c color.NRGBA) {
	b := img.Bounds()
	if x < b.Min.X || y < b.Min.Y || x >= b.Max.X || y >= b.Max.Y {
		return
	}
	img.SetNRGBA(x, y, c)
}

// alphaOver blends one marker color over an existing pixel using straight
// alpha, matching how an annotation layer would composite in a viewer.
func alphaOver(img *image.NRGBA, x, y int, src color.NRGBA) {
	dst := img.NRGBAAt(x, y)
	sa := float64(src.A) / 255
	blend := func(d, s uint8) uint8 {
		return uint8(float64(s)*sa + float64(d)*(1-sa))
	}
	img.SetNRGBA(x, y, color.NRGBA{R: blend(dst.R, src.R), G: blend(dst.G, src.G), B: blend(dst.B, src.B), A: 255})
}
