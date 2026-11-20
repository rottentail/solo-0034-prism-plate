// Package vector exports the compiled contour representation without raw markup.
package vector

import (
	"bytes"
	"fmt"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/paint"
	"prismplate/internal/scene"
	"sort"
	"strings"
)

func number(x float64) string { return fmt.Sprintf("%.9g", x) }
func path(p geom.Path) string {
	var b strings.Builder
	for _, ring := range p {
		for i, v := range ring {
			verb := "L"
			if i == 0 {
				verb = "M"
			}
			fmt.Fprintf(&b, "%s%s %s", verb, number(v.X), number(v.Y))
		}
		b.WriteString("Z")
	}
	return b.String()
}
func color(c scene.Color) string {
	return fmt.Sprintf("rgb(%s%%,%s%%,%s%%)", number(c.R*100), number(c.G*100), number(c.B*100))
}

// SVG uses the same flattened geometry. Browser gradient alpha interpolation and
// pixel coverage may differ from the canonical PNG renderer; SVG is a vector proof.
//
// Divergence choice for projective shapes: SVG has no projective transform
// attribute, so the flattened outline is emitted already mapped into display
// coordinates. Straight segments therefore stay straight and remain editable
// vertices, while curves are exported at the contract subdivision. The gradient
// is emitted along the mapped axis because SVG cannot express a projective
// gradient; the element carries data-prism-* markers so a receiver can tell a
// true perspective export from an affine approximation.
func Export(c scene.Compiled) []byte {
	var b bytes.Buffer
	d := c.Document
	fmt.Fprintf(&b, `<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" color-interpolation="linearRGB"><defs>`, d.Width, d.Height, d.Width, d.Height)
	for i, l := range c.Layers {
		if len(l.Clip) > 0 {
			fmt.Fprintf(&b, `<clipPath id="c%d" clipPathUnits="userSpaceOnUse"><path d="%s"/></clipPath>`, i, path(l.Clip))
		}
		if len(l.Mask) > 0 {
			fmt.Fprintf(&b, `<clipPath id="m%d" clipPathUnits="userSpaceOnUse"><path d="%s"/></clipPath>`, i, path(l.Mask))
		}
		for j, s := range l.Shapes {
			if s.FaceSet {
				fmt.Fprintf(&b, `<clipPath id="f%d_%d" clipPathUnits="userSpaceOnUse"><path d="M%s %sL%s %sL%s %sL%s %sZ"/></clipPath>`, i, j,
					number(s.Face[0].X), number(s.Face[0].Y), number(s.Face[1].X), number(s.Face[1].Y),
					number(s.Face[2].X), number(s.Face[2].Y), number(s.Face[3].X), number(s.Face[3].Y))
			}
			br := s.Shape.Brush
			if br.Kind != "linear" {
				continue
			}
			from, to := br.From, br.To
			extra := ""
			if s.Projective {
				// The mapped axis is a straight line (projections keep lines
				// straight), but its parameter is nonlinear. Emit the gradient
				// in display space with extra stops resampled through the
				// homography so the visible ramp tracks the true projection.
				if q, ok := s.LocalToPlane.Apply(from); ok {
					from = q
				}
				if q, ok := s.LocalToPlane.Apply(to); ok {
					to = q
				}
				extra = ` data-prism-gradient="perspective-resampled"`
			}
			fmt.Fprintf(&b, `<linearGradient id="b%d_%d" gradientUnits="userSpaceOnUse" x1="%s" y1="%s" x2="%s" y2="%s"%s>`, i, j, number(from.X), number(from.Y), number(to.X), number(to.Y), extra)
			for _, stop := range gradientStops(br, s, from, to) {
				fmt.Fprintf(&b, `<stop offset="%s" stop-color="%s" stop-opacity="%s"/>`, number(stop.At), color(stop.Color), number(stop.Color.A))
			}
			b.WriteString(`</linearGradient>`)
		}
	}
	b.WriteString(`</defs>`)
	fmt.Fprintf(&b, `<rect width="100%%" height="100%%" fill="%s" fill-opacity="%s"/>`, color(d.Background), number(d.Background.A))
	for i, l := range c.Layers {
		fmt.Fprintf(&b, `<g opacity="%s" style="isolation:isolate;mix-blend-mode:%s"`, number(l.Layer.Opacity), map[string]string{"normal": "normal", "multiply": "multiply", "screen": "screen"}[l.Layer.Blend])
		if len(l.Clip) > 0 {
			fmt.Fprintf(&b, ` clip-path="url(#c%d)"`, i)
		}
		b.WriteString(`><g`)
		if len(l.Mask) > 0 {
			fmt.Fprintf(&b, ` clip-path="url(#m%d)"`, i)
		}
		b.WriteString(`>`)
		for j, s := range l.Shapes {
			br := s.Shape.Brush
			fill := color(br.Color)
			alpha := br.Color.A
			if br.Kind == "linear" {
				fill = fmt.Sprintf("url(#b%d_%d)", i, j)
				alpha = 1
			}
			if s.Projective {
				mode := "perspective"
				if s.Shape.Projective != nil && s.Shape.Projective.Affine {
					mode = "affine-approx"
				}
				// d is in display coordinates so receivers can edit it
				// directly without a projective transform SVG cannot carry.
				// data-prism-source keeps the original local-space contour so
				// the artwork survives a round trip.
				fmt.Fprintf(&b, `<path d="%s" fill="%s" fill-opacity="%s" fill-rule="%s"`, path(s.ProjectedPath), fill, number(alpha), s.Shape.FillRule)
				if s.FaceSet {
					fmt.Fprintf(&b, ` clip-path="url(#f%d_%d)"`, i, j)
				}
				fmt.Fprintf(&b, ` data-prism-map="%s" data-prism-source="flattened" data-prism-original="%s"/>`, mode, escape(path(s.Path)))
				continue
			}
			m := s.Shape.Transform
			fmt.Fprintf(&b, `<path d="%s" fill="%s" fill-opacity="%s" fill-rule="%s" transform="matrix(%s %s %s %s %s %s)"/>`, path(s.Path), fill, number(alpha), s.Shape.FillRule, number(m[0]), number(m[1]), number(m[2]), number(m[3]), number(m[4]), number(m[5]))
		}
		b.WriteString(`</g></g>`)
	}
	b.WriteString(`</svg>`)
	return b.Bytes()
}

// gradientStops resamples an affine gradient through the projective map. For an
// affine shape the authored stops are emitted unchanged. For a perspective
// shape the axis endpoints are already mapped; intermediate stops are placed
// at the projected position of each authored parameter so the ramp follows the
// same colour law as the PNG renderer.
func gradientStops(br scene.Brush, s scene.CompiledShape, from, to geom.Point) []scene.Stop {
	if !s.Projective || (s.Shape.Projective != nil && s.Shape.Projective.Affine) {
		return br.Stops
	}
	dx, dy := to.X-from.X, to.Y-from.Y
	den := dx*dx + dy*dy
	if den < 1e-12 {
		return br.Stops
	}
	// A dense fixed set of parameters, plus every authored stop, keeps the
	// exported polyline gradient stable and free of stray coordinates.
	var ts []float64
	for i := 0; i <= 8; i++ {
		ts = append(ts, float64(i)/8)
	}
	for _, st := range br.Stops {
		ts = append(ts, st.At)
	}
	sort.Float64s(ts)
	out := make([]scene.Stop, 0, len(ts))
	seen := map[float64]bool{}
	for _, t := range ts {
		local := geom.Point{X: br.From.X + (br.To.X-br.From.X)*t, Y: br.From.Y + (br.To.Y-br.From.Y)*t}
		q, ok := s.LocalToPlane.Apply(local)
		if !ok {
			continue
		}
		offset := ((q.X-from.X)*dx + (q.Y-from.Y)*dy) / den
		if offset < 0 {
			offset = 0
		}
		if offset > 1 {
			offset = 1
		}
		key := math.Round(offset*1e9) / 1e9
		if seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, scene.Stop{At: offset, Color: paint.ColorAt(br, t)})
	}
	if len(out) < 2 {
		return br.Stops
	}
	return out
}

func escape(s string) string {
	r := strings.NewReplacer(`&`, `&amp;`, `<`, `&lt;`, `>`, `&gt;`, `"`, `&quot;`, `'`, `&#39;`)
	return r.Replace(s)
}
