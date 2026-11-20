// Package vector exports the compiled contour representation without raw markup.
package vector

import (
	"bytes"
	"fmt"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
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
			br := s.Shape.Brush
			if br.Kind == "linear" {
				if br.EffectiveAnchor() == scene.AnchorCanvas {
					// Canvas-anchored axes are document coordinates. The path
					// itself carries M, so the gradient gets M^-1 to map
					// document axis points into the transformed user space;
					// composition reproduces the canvas field exactly.
					inv := s.Inverse
					fmt.Fprintf(&b, `<linearGradient id="b%d_%d" gradientUnits="userSpaceOnUse" gradientTransform="matrix(%s %s %s %s %s %s)" x1="%s" y1="%s" x2="%s" y2="%s">`, i, j, number(inv[0]), number(inv[1]), number(inv[2]), number(inv[3]), number(inv[4]), number(inv[5]), number(br.From.X), number(br.From.Y), number(br.To.X), number(br.To.Y))
				} else {
					fmt.Fprintf(&b, `<linearGradient id="b%d_%d" gradientUnits="userSpaceOnUse" x1="%s" y1="%s" x2="%s" y2="%s">`, i, j, number(br.From.X), number(br.From.Y), number(br.To.X), number(br.To.Y))
				}
				for _, stop := range br.Stops {
					fmt.Fprintf(&b, `<stop offset="%s" stop-color="%s" stop-opacity="%s"/>`, number(stop.At), color(stop.Color), number(stop.Color.A))
				}
				b.WriteString(`</linearGradient>`)
			}
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
			m := s.Shape.Transform
			br := s.Shape.Brush
			fill := color(br.Color)
			alpha := br.Color.A
			if br.Kind == "linear" {
				fill = fmt.Sprintf("url(#b%d_%d)", i, j)
				alpha = 1
			}
			fmt.Fprintf(&b, `<path d="%s" fill="%s" fill-opacity="%s" fill-rule="%s" transform="matrix(%s %s %s %s %s %s)"/>`, path(s.Path), fill, number(alpha), s.Shape.FillRule, number(m[0]), number(m[1]), number(m[2]), number(m[3]), number(m[4]), number(m[5]))
		}
		b.WriteString(`</g></g>`)
	}
	b.WriteString(`</svg>`)
	return b.Bytes()
}
