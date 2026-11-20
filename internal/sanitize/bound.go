package sanitize

import (
	"context"
	"fmt"
	"image/color"

	"prismplate/internal/geom"
	"prismplate/internal/paint"
	"prismplate/internal/raster"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
)

// Bound quantifies the difference between the original scene and the delivery
// draft by comparing the canonical PNG exposures pixel for pixel. It is the
// verifiable error statement attached to an export.
//
// Pixels are compared in the 8-bit encoding a receiver actually sees. Pixels
// whose four-point sample grid straddles the region boundary are excluded,
// because the PNG proof resolves those with subpixels while the delivery has
// hard polygon edges; they are reported as boundary_skipped_pixels rather than
// folded into the tolerance. Pixels fully inside are compared exactly, and
// pixels fully outside must equal the original background.
type Bound struct {
	MaxError     int `json:"max_png_channel_error"`
	Interior     int `json:"compared_pixels"`
	Boundary     int `json:"boundary_skipped_pixels"`
	Outside      int `json:"outside_pixels"`
	OutsideError int `json:"max_outside_channel_error"`
}

// ComputeBound renders both scenes and compares them.
func ComputeBound(ctx context.Context, original, delivery scene.Document, area Area) (Bound, error) {
	co, err := validate.Compile(original)
	if err != nil {
		return Bound{}, fmt.Errorf("original scene: %w", err)
	}
	cd, err := validate.Compile(delivery)
	if err != nil {
		return Bound{}, fmt.Errorf("delivery scene: %w", err)
	}
	if co.Document.Width != cd.Document.Width || co.Document.Height != cd.Document.Height {
		return Bound{}, fmt.Errorf("delivery canvas differs from the source canvas")
	}
	a, err := raster.RenderImage(ctx, co)
	if err != nil {
		return Bound{}, err
	}
	z, err := raster.RenderImage(ctx, cd)
	if err != nil {
		return Bound{}, err
	}
	b := Bound{}
	// The background is a document value, not a sample: sampling a corner can
	// land inside a delivered shape and would misstate the outside check.
	background := paint.From(cd.Document.Background)
	for y := 0; y < co.Document.Height; y++ {
		if err := ctx.Err(); err != nil {
			return b, err
		}
		for x := 0; x < co.Document.Width; x++ {
			t := pixelClass(area, x, y, co.Document.Width, co.Document.Height)
			delta := pixelDelta(a.NRGBAAt(x, y), z.NRGBAAt(x, y))
			switch t {
			case inside:
				b.Interior++
				if delta > b.MaxError {
					b.MaxError = delta
				}
			case boundary:
				b.Boundary++
			case outside:
				b.Outside++
				if d := pixelDelta(z.NRGBAAt(x, y), nrgbaOf(background)); d > b.OutsideError {
					b.OutsideError = d
				}
			}
		}
	}
	return b, nil
}

type class int

const (
	inside class = iota
	boundary
	outside
)

// pixelClass uses the four canonical subsample centers. Only pixels whose four
// samples are all inside or all outside are compared; anything mixed is the
// boundary band and is excluded from the interior tolerance.
func pixelClass(area Area, x, y, width, height int) class {
	corners := [4]geom.Point{
		{X: float64(x) + 0.25, Y: float64(y) + 0.25},
		{X: float64(x) + 0.75, Y: float64(y) + 0.25},
		{X: float64(x) + 0.25, Y: float64(y) + 0.75},
		{X: float64(x) + 0.75, Y: float64(y) + 0.75},
	}
	in, out := 0, 0
	tol := area.Epsilon()
	for _, p := range corners {
		if d := area.Distance(p); d > -tol && d < tol {
			// A subsample sitting exactly on the boundary can be resolved
			// either way by the hard polygon edge; such a pixel carries no
			// verifiable interior evidence.
			return boundary
		}
		if area.InsideOrOn(p) {
			in++
		} else {
			out++
		}
	}
	switch {
	case in == 4:
		return inside
	case out == 4:
		return outside
	default:
		return boundary
	}
}

func pixelDelta(a, b color.NRGBA) int {
	d := func(x, y uint8) int {
		if x > y {
			return int(x) - int(y)
		}
		return int(y) - int(x)
	}
	m := d(a.R, b.R)
	for _, v := range []int{d(a.G, b.G), d(a.B, b.B), d(a.A, b.A)} {
		if v > m {
			m = v
		}
	}
	return m
}

func nrgbaOf(p paint.Pixel) color.NRGBA {
	r, g, b, a := p.RGBA8()
	return color.NRGBA{R: r, G: g, B: b, A: a}
}
