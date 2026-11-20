// Package coverage measures how much visible canvas each requested color
// family actually occupies. Measurement shares the canonical paint evaluator
// with raster rendering, so occlusion, clipping, masks, low alpha and
// multiply/screen blending are attributed under the same imaging rules as the
// authoritative PNG rather than from nominal shape areas.
package coverage

import (
	"fmt"
	"math"
	"prismplate/internal/paint"
	"prismplate/internal/scene"
)

// Family is one requested color family plus the tolerance used to match it.
type Family struct {
	Name      string      `json:"name"`
	Reference scene.Color `json:"reference"`
	// MaxDeltaE is the maximum CIE76 delta E (CIELAB) distance for a pixel to
	// count as this family. Zero selects the documented default of 12.
	MaxDeltaE float64 `json:"max_delta_e,omitempty"`
}

// Lab is a CIE L*a*b* point with D65 reference white.
type Lab struct{ L, A, B float64 }

// srgbToLinear converts one 0..1 sRGB channel to linear light using the same
// transfer function the paint pipeline applies.
func srgbToLinear(v float64) float64 {
	if v <= 0.04045 {
		return v / 12.92
	}
	return math.Pow((v+0.055)/1.055, 2.4)
}

// ToLab converts a straight (non-premultiplied) sRGB color to CIE L*a*b*
// under D65. Alpha is intentionally ignored: families classify hue and tone,
// while opacity is accounted for separately through the area weights.
func ToLab(c scene.Color) Lab {
	r := srgbToLinear(c.R)
	g := srgbToLinear(c.G)
	bl := srgbToLinear(c.B)
	// sRGB D65 primaries -> XYZ. The matrix yields X,Y,Z in 0..1 with the
	// D65 white point at (0.95047, 1.0, 1.08883).
	x := r*0.4124564 + g*0.3575761 + bl*0.1804375
	y := r*0.2126729 + g*0.7151522 + bl*0.0721750
	z := r*0.0193339 + g*0.1191920 + bl*0.9503041
	const xr, yr, zr = 0.95047, 1.0, 1.08883
	f := func(t float64) float64 {
		const e = 216.0 / 24389.0
		const k = 24389.0 / 27.0
		if t > e {
			return math.Cbrt(t)
		}
		return (k*t + 16) / 116
	}
	fx, fy, fz := f(x/xr), f(y/yr), f(z/zr)
	return Lab{L: 116*fy - 16, A: 500 * (fx - fy), B: 200 * (fy - fz)}
}

// DeltaE is the CIE76 euclidean distance in L*a*b* space.
func DeltaE(a, b Lab) float64 {
	return math.Hypot(math.Hypot(a.L-b.L, a.A-b.A), a.B-b.B)
}

// Classifier resolves observed sRGB colors to one named family or the empty
// string when no family is within tolerance. The first matching family in
// request order wins, which keeps attribution deterministic and explicit.
type Classifier struct {
	families []Family
	labs     []Lab
}

func NewClassifier(families []Family) (*Classifier, error) {
	if len(families) < 1 || len(families) > 16 {
		return nil, fmt.Errorf("coverage requires 1..16 color families")
	}
	seen := map[string]bool{}
	for _, f := range families {
		if f.Name == "" || len(f.Name) > 64 {
			return nil, fmt.Errorf("invalid family name")
		}
		if seen[f.Name] {
			return nil, fmt.Errorf("duplicate family name %q", f.Name)
		}
		seen[f.Name] = true
		for _, v := range []float64{f.Reference.R, f.Reference.G, f.Reference.B, f.Reference.A} {
			if math.IsNaN(v) || math.IsInf(v, 0) || v < 0 || v > 1 {
				return nil, fmt.Errorf("family reference color must be in 0..1")
			}
		}
		if f.MaxDeltaE < 0 || f.MaxDeltaE > 100 {
			return nil, fmt.Errorf("max_delta_e must be in 0..100")
		}
	}
	c := &Classifier{families: families, labs: make([]Lab, len(families))}
	for i, f := range families {
		c.labs[i] = ToLab(f.Reference)
	}
	return c, nil
}

// tolerance reports the effective delta-E threshold for family i.
func (c *Classifier) tolerance(i int) float64 {
	if t := c.families[i].MaxDeltaE; t > 0 {
		return t
	}
	return 12
}

// classify returns the family index for an observed straight sRGB color, or
// -1 when the color falls outside every requested family.
func (c *Classifier) classify(col scene.Color) int {
	lab := ToLab(col)
	best, bestD := -1, math.Inf(1)
	for i, ref := range c.labs {
		if d := DeltaE(lab, ref); d <= c.tolerance(i) && d < bestD {
			best, bestD = i, d
		}
	}
	return best
}

// straightColor unpremultiplies a linear-light paint pixel back to straight
// sRGB channels for classification. Transparent pixels return alpha 0.
func straightColor(p paint.Pixel) scene.Color {
	if p.A <= 1e-12 {
		return scene.Color{}
	}
	inv := 1 / p.A
	return scene.Color{
		R: toSrgb(p.R * inv),
		G: toSrgb(p.G * inv),
		B: toSrgb(p.B * inv),
		A: math.Min(1, p.A),
	}
}

func toSrgb(v float64) float64 {
	v = math.Max(0, math.Min(1, v))
	if v <= 0.0031308 {
		return v * 12.92
	}
	return 1.055*math.Pow(v, 1/2.4) - 0.055
}
