package sep

import (
	"bytes"
	"image"
	"image/color"
	"image/png"
	"prismplate/internal/paint"
	"prismplate/internal/scene"
)

// recomposite folds the per-ink coverage maps back into one RGB image using the
// declared press order and overprint semantics, then compares it pixel by pixel
// with the canonical reference PNG of the same revision. Every difference must
// be explainable: an overprinted overlap, a process RGB shape, or nothing.
func recomposite(d scene.Document, order []string, cov []float64, mark []bool, referencePNG []byte) ([]byte, int, int, bool, error) {
	n := len(order)
	inkColors := make([]paint.Pixel, n)
	for k, id := range order {
		inkColors[k] = paint.From(spotByID(d, id).ProofColor)
	}
	bg := paint.From(d.Background)

	img := image.NewNRGBA(image.Rect(0, 0, d.Width, d.Height))
	diffPixels := 0
	overprintPixels := 0
	anyOverprint := false

	refImg, err := png.Decode(bytes.NewReader(referencePNG))
	if err != nil {
		return nil, 0, 0, false, err
	}
	refBounds := refImg.Bounds()

	for y := 0; y < d.Height; y++ {
		for x := 0; x < d.Width; x++ {
			sum := paint.Pixel{}
			pixelOverprint := false
			for s := 0; s < 4; s++ {
				sample := (y*d.Width+x)*4 + s
				dst := bg
				for k := 0; k < n; k++ {
					a := cov[sample*n+k]
					if a <= 0 {
						continue
					}
					op := mark[sample*n+k]
					if op {
						anyOverprint = true
						pixelOverprint = true
					}
					// Premultiplied ink film at this coverage.
					film := paint.Pixel{R: inkColors[k].R * a, G: inkColors[k].G * a, B: inkColors[k].B * a, A: a}
					dst = paint.InkOver(dst, film, op)
				}
				sum.R += dst.R / 4
				sum.G += dst.G / 4
				sum.B += dst.B / 4
				sum.A += dst.A / 4
			}
			r, g, b, a := sum.RGBA8()
			img.SetNRGBA(x, y, color.NRGBA{R: r, G: g, B: b, A: a})
			if pixelOverprint {
				overprintPixels++
			}
			rr, gg, bb, aa := refImg.At(refBounds.Min.X+x, refBounds.Min.Y+y).RGBA()
			ref8 := color.NRGBA{R: uint8(rr >> 8), G: uint8(gg >> 8), B: uint8(bb >> 8), A: uint8(aa >> 8)}
			if ref8.R != r || ref8.G != g || ref8.B != b || ref8.A != a {
				diffPixels++
			}
		}
	}

	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		return nil, 0, 0, false, err
	}
	// Without overprint every film stacks with plain source-over, the exact
	// operator of the reference renderer; translucency alone never diverges.
	identity := !anyOverprint && diffPixels == 0
	return buf.Bytes(), diffPixels, overprintPixels, identity, nil
}
