// Package vector exports the compiled contour representation without raw markup.
package vector

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/paint"
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

// mapped flattens a local path into canvas coordinates for clip definitions.
func mapped(p geom.Path, m geom.Matrix) geom.Path {
	out := make(geom.Path, 0, len(p))
	for _, ring := range p {
		next := make(geom.Contour, 0, len(ring))
		for _, v := range ring {
			next = append(next, m.Apply(v))
		}
		out = append(out, next)
	}
	return out
}
func cssColor(c scene.Color) string {
	return fmt.Sprintf("rgb(%s%%,%s%%,%s%%)", number(c.R*100), number(c.G*100), number(c.B*100))
}

// meshFill renders a mesh brush to a canvas-resolution PNG and returns an
// image element clipped by the transformed outline. SVG 1.1 has no triangle
// mesh gradient and a single linearGradient cannot carry the four independent
// affine channel fields of one triangle, so the honest handoff is a raster of
// the same color function the PNG proof samples, at the same subpixel grid.
func meshFill(s scene.CompiledShape, id string, w, h int) string {
	m := s.Shape.Brush.Mesh
	minX, minY := math.Inf(1), math.Inf(1)
	maxX, maxY := math.Inf(-1), math.Inf(-1)
	for _, v := range m.Vertices {
		p := s.Shape.Transform.Apply(geom.Point{X: v.X, Y: v.Y})
		minX, minY = math.Min(minX, p.X), math.Min(minY, p.Y)
		maxX, maxY = math.Max(maxX, p.X), math.Max(maxY, p.Y)
	}
	x0 := int(math.Floor(math.Max(0, minX)))
	y0 := int(math.Floor(math.Max(0, minY)))
	x1 := int(math.Ceil(math.Min(float64(w), maxX)))
	y1 := int(math.Ceil(math.Min(float64(h), maxY)))
	if x1 <= x0 || y1 <= y0 {
		return ""
	}
	cm := paint.CompileMesh(m)
	img := image.NewNRGBA(image.Rect(0, 0, x1-x0, y1-y0))
	for y := 0; y < y1-y0; y++ {
		for x := 0; x < x1-x0; x++ {
			sum := paint.Pixel{}
			for sy := 0; sy < 2; sy++ {
				for sx := 0; sx < 2; sx++ {
					p := geom.Point{X: float64(x0+x) + (float64(sx)+0.5)/2, Y: float64(y0+y) + (float64(sy)+0.5)/2}
					px := cm.Sample(s.Inverse.Apply(p))
					sum.R += px.R / 4
					sum.G += px.G / 4
					sum.B += px.B / 4
					sum.A += px.A / 4
				}
			}
			r, g, b, a := sum.RGBA8()
			img.SetNRGBA(x, y, color.NRGBA{R: r, G: g, B: b, A: a})
		}
	}
	var buf bytes.Buffer
	// Encoding a valid NRGBA into a memory buffer cannot fail.
	_ = png.Encode(&buf, img)
	return fmt.Sprintf(`<image x="%d" y="%d" width="%d" height="%d" href="data:image/png;base64,%s" clip-path="url(#%s)"/>`, x0, y0, x1-x0, y1-y0, base64.StdEncoding.EncodeToString(buf.Bytes()), id)
}

// SVG uses the same flattened geometry. Browser gradient alpha interpolation and
// pixel coverage may differ from the canonical PNG renderer; SVG is a vector proof.
// Mesh fills are embedded as canvas-resolution raster clipped by vector outlines.
func Export(c scene.Compiled) []byte {
	var b bytes.Buffer
	d := c.Document
	meshes := 0
	for _, l := range c.Layers {
		for _, s := range l.Shapes {
			if s.Shape.Brush.Kind == "mesh" && s.Shape.Brush.Mesh != nil {
				meshes++
			}
		}
	}
	fmt.Fprintf(&b, `<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" color-interpolation="linearRGB">`, d.Width, d.Height, d.Width, d.Height)
	if meshes > 0 {
		b.WriteString(`<desc>mesh brush fills are embedded as canvas-resolution raster sampled from the same color function as the authoritative PNG proof; outlines, holes, layer clips and masks stay vector. PNG remains the pixel proof.</desc>`)
	}
	b.WriteString(`<defs>`)
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
				fmt.Fprintf(&b, `<linearGradient id="b%d_%d" gradientUnits="userSpaceOnUse" x1="%s" y1="%s" x2="%s" y2="%s">`, i, j, number(br.From.X), number(br.From.Y), number(br.To.X), number(br.To.Y))
				for _, stop := range br.Stops {
					fmt.Fprintf(&b, `<stop offset="%s" stop-color="%s" stop-opacity="%s"/>`, number(stop.At), cssColor(stop.Color), number(stop.Color.A))
				}
				b.WriteString(`</linearGradient>`)
			}
			if br.Kind == "mesh" && br.Mesh != nil {
				fmt.Fprintf(&b, `<clipPath id="k%d_%d" clipPathUnits="userSpaceOnUse"><path d="%s" clip-rule="%s"/></clipPath>`, i, j, path(mapped(s.Path, s.Shape.Transform)), s.Shape.FillRule)
			}
		}
	}
	b.WriteString(`</defs>`)
	fmt.Fprintf(&b, `<rect width="100%%" height="100%%" fill="%s" fill-opacity="%s"/>`, cssColor(d.Background), number(d.Background.A))
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
			if br.Kind == "mesh" && br.Mesh != nil {
				b.WriteString(meshFill(s, fmt.Sprintf("k%d_%d", i, j), d.Width, d.Height))
				continue
			}
			fill := cssColor(br.Color)
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
