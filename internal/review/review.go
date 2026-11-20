// Package review projects region annotations across revisions and renders a
// review sheet that keeps the rejected proof's final image beside the new draft.
//
// Projection never silently reuses a same-named object: every annotation carries
// the canvas size and affine transform captured on the proofed revision, and a
// target revision always receives an explicit status explaining how the region
// was derived or why the association failed.
package review

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"prismplate/internal/geom"
	"prismplate/internal/repository"
	"prismplate/internal/scene"
	"strconv"
	"strings"
)

// Projection is a derived, never-persisted view of one annotation on a target revision.
type Projection struct {
	Annotation repository.Annotation `json:"annotation"`
	Status     string                `json:"status"`
	Unverified bool                  `json:"unverified"`
	Outside    bool                  `json:"outside_canvas"`
	Reason     string                `json:"reason"`
	Region     repository.Region     `json:"region"`
}

const (
	StatusAnchored  = "anchored"
	StatusRelocated = "relocated"
	StatusRescaled  = "rescaled"
	StatusOrphaned  = "orphaned"
	StatusAbsolute  = "absolute"
)

func bbox(m geom.Matrix, r repository.Region) repository.Region {
	corners := []geom.Point{
		{X: r.X, Y: r.Y}, {X: r.X + r.W, Y: r.Y},
		{X: r.X + r.W, Y: r.Y + r.H}, {X: r.X, Y: r.Y + r.H},
	}
	minX, minY := corners[0].X, corners[0].Y
	maxX, maxY := minX, minY
	for _, p := range corners[1:] {
		q := m.Apply(p)
		if q.X < minX {
			minX = q.X
		}
		if q.X > maxX {
			maxX = q.X
		}
		if q.Y < minY {
			minY = q.Y
		}
		if q.Y > maxY {
			maxY = q.Y
		}
	}
	return repository.Region{X: minX, Y: minY, W: maxX - minX, H: maxY - minY}
}

func invertBBox(inv geom.Matrix, r repository.Region) repository.Region { return bbox(inv, r) }

func outside(r repository.Region, w, h int) bool {
	return r.X < 0 || r.Y < 0 || r.X+r.W > float64(w)+1e-9 || r.Y+r.H > float64(h)+1e-9
}

// PickAnchor returns the topmost painted shape containing the region center.
func PickAnchor(c scene.Compiled, region repository.Region) *repository.Anchor {
	center := geom.Point{X: region.X + region.W/2, Y: region.Y + region.H/2}
	d := c.Document
	for li := len(c.Layers) - 1; li >= 0; li-- {
		l := c.Layers[li]
		if len(l.Clip) > 0 && !l.Clip.Contains(center, "nonzero") {
			continue
		}
		if len(l.Mask) > 0 && !l.Mask.Contains(center, "nonzero") {
			continue
		}
		for si := len(l.Shapes) - 1; si >= 0; si-- {
			s := l.Shapes[si]
			q := s.Inverse.Apply(center)
			if !s.Path.Contains(q, s.Shape.FillRule) {
				continue
			}
			return &repository.Anchor{
				Layer:        l.Layer.ID,
				Shape:        s.Shape.ID,
				Transform:    s.Shape.Transform,
				CanvasWidth:  d.Width,
				CanvasHeight: d.Height,
				LocalRegion:  invertBBox(s.Inverse, region),
			}
		}
	}
	return nil
}

func findShape(d scene.Document, layerID, shapeID string) (geom.Matrix, bool) {
	for _, l := range d.Layers {
		if l.ID != layerID {
			continue
		}
		for _, s := range l.Shapes {
			if s.ID == shapeID {
				return s.Transform, true
			}
		}
	}
	return geom.Matrix{}, false
}

// Project maps one stored annotation into the coordinate space of target.
func Project(n repository.Annotation, target scene.Document) Projection {
	p := Projection{Annotation: n, Region: n.Region}
	if n.Anchor == nil {
		return ProjectAbsolute(n, target)
	}
	a := n.Anchor
	m1, ok := findShape(target, a.Layer, a.Shape)
	if !ok {
		// The anchored shape no longer exists. Keep the original coordinates and
		// flag the association instead of pasting onto a same-named new object.
		p.Status = StatusOrphaned
		p.Unverified = true
		p.Reason = fmt.Sprintf("anchored shape %q in layer %q no longer exists; original region retained without re-attachment", a.Shape, a.Layer)
		p.Outside = outside(n.Region, target.Width, target.Height)
		return p
	}
	p.Region = bbox(m1, a.LocalRegion)
	p.Outside = outside(p.Region, target.Width, target.Height)
	sameMatrix := repository.SameMatrix(a.Transform, m1)
	dimsChanged := a.CanvasWidth != target.Width || a.CanvasHeight != target.Height
	switch {
	case sameMatrix && !dimsChanged:
		p.Status = StatusAnchored
		p.Reason = fmt.Sprintf("anchored to %q; transform and canvas unchanged", a.Shape)
	case sameMatrix:
		p.Status = StatusRescaled
		p.Unverified = true
		p.Reason = fmt.Sprintf("canvas changed %dx%d -> %dx%d; shape transform unchanged, region replayed in its local coordinates", a.CanvasWidth, a.CanvasHeight, target.Width, target.Height)
	default:
		p.Status = StatusRelocated
		p.Unverified = true
		p.Reason = fmt.Sprintf("shape %q transform changed; region recomputed from captured local coordinates via the new affine transform", a.Shape)
		if dimsChanged {
			p.Reason += fmt.Sprintf("; canvas size also changed %dx%d -> %dx%d", a.CanvasWidth, a.CanvasHeight, target.Width, target.Height)
		}
	}
	if p.Outside {
		p.Reason += "; projected region extends beyond the target canvas"
	}
	return p
}

// ProjectAbsolute handles anchor-free annotations, scaling only on canvas change.
func ProjectAbsolute(n repository.Annotation, target scene.Document) Projection {
	p := Projection{Annotation: n, Region: n.Region, Status: StatusAbsolute, Reason: "free annotation without shape anchor"}
	aw, ah := n.CanvasWidth, n.CanvasHeight
	if aw != 0 && ah != 0 && (aw != target.Width || ah != target.Height) {
		sx := float64(target.Width) / float64(aw)
		sy := float64(target.Height) / float64(ah)
		p.Region = repository.Region{X: n.Region.X * sx, Y: n.Region.Y * sy, W: n.Region.W * sx, H: n.Region.H * sy}
		p.Status = StatusRescaled
		p.Unverified = true
		p.Reason = fmt.Sprintf("canvas changed %dx%d -> %dx%d; free region scaled proportionally", aw, ah, target.Width, target.Height)
	}
	p.Outside = outside(p.Region, target.Width, target.Height)
	if p.Outside {
		p.Reason += "; projected region extends beyond the target canvas"
	}
	return p
}

var palette = []string{"#ff4136", "#ff851b", "#2ecc40", "#39cccc", "#b10dc9", "#f012be", "#01ff70", "#ffdc00"}

func statusColor(status string) string {
	switch status {
	case StatusAnchored:
		return "#2ecc40"
	case StatusRelocated:
		return "#ff851b"
	case StatusRescaled:
		return "#39cccc"
	case StatusOrphaned:
		return "#ff4136"
	default:
		return "#aaaaaa"
	}
}

func xmlEscape(s string) string {
	r := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&apos;")
	return r.Replace(s)
}
func num(x float64) string { return fmt.Sprintf("%.9g", x) }

// SourceGroup bundles one rejected source revision with its original image and
// the open annotations recorded on its final proof.
type SourceGroup struct {
	Revision    int
	ProofID     string
	Width       int
	Height      int
	PNG         []byte
	Projections []Projection
}

type sheetMarker struct {
	region repository.Region
	label  string
	color  string
	dash   bool
}
type sheetColumn struct {
	title   string
	png     []byte
	width   int
	height  int
	markers []sheetMarker
}

// Sheet renders an SVG review sheet: every source column keeps its immutable
// proof image and original regions; the final column shows the target draft with
// projected regions and explicit association statuses.
func Sheet(target scene.Document, targetRevision int, targetPNG []byte, sources []SourceGroup, comments map[string]string) []byte {
	var cols []sheetColumn
	globalIndex := 0
	for _, g := range sources {
		col := sheetColumn{title: fmt.Sprintf("原校样 · 稿本%d · 校样 %s", g.Revision, g.ProofID), png: g.PNG, width: g.Width, height: g.Height}
		for _, p := range g.Projections {
			col.markers = append(col.markers, sheetMarker{region: p.Annotation.Region, label: badge(globalIndex), color: palette[globalIndex%len(palette)]})
			globalIndex++
		}
		cols = append(cols, col)
	}
	targetCol := sheetColumn{title: fmt.Sprintf("新稿 · 稿本%d · 待处理意见对照", targetRevision), png: targetPNG, width: target.Width, height: target.Height}
	globalIndex = 0
	for _, g := range sources {
		for _, p := range g.Projections {
			targetCol.markers = append(targetCol.markers, sheetMarker{region: p.Region, label: badge(globalIndex), color: statusColor(p.Status), dash: p.Unverified})
			globalIndex++
		}
	}
	cols = append(cols, targetCol)

	const pad, header, gap = 16, 28, 24
	width := pad
	for _, c := range cols {
		width += c.width + gap
	}
	width += pad - gap
	ledgerLines := 0
	for _, g := range sources {
		ledgerLines += len(g.Projections)
	}
	footer := 150 + 16*ledgerLines
	height := header + maxDim(cols) + footer + pad

	var b bytes.Buffer
	fmt.Fprintf(&b, `<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" font-family="sans-serif">`, width, height, width, height)
	b.WriteString(`<rect width="100%" height="100%" fill="#11141a"/>`)
	x := pad
	for ci, col := range cols {
		fmt.Fprintf(&b, `<text x="%d" y="18" fill="#e6e8ec" font-size="13">%s</text>`, x, xmlEscape(col.title))
		clip := fmt.Sprintf("clip%d", ci)
		fmt.Fprintf(&b, `<clipPath id="%s"><rect x="%d" y="%d" width="%d" height="%d"/></clipPath>`, clip, x, header, col.width, col.height)
		fmt.Fprintf(&b, `<rect x="%d" y="%d" width="%d" height="%d" fill="#000"/>`, x, header, col.width, col.height)
		if len(col.png) > 0 {
			fmt.Fprintf(&b, `<image x="%d" y="%d" width="%d" height="%d" clip-path="url(#%s)" href="data:image/png;base64,%s"/>`, x, header, col.width, col.height, clip, base64.StdEncoding.EncodeToString(col.png))
		}
		for _, mk := range col.markers {
			stroke := `stroke-width="2"`
			if mk.dash {
				stroke += ` stroke-dasharray="6 4"`
			}
			rx, ry := float64(x)+mk.region.X, float64(header)+mk.region.Y
			fmt.Fprintf(&b, `<g clip-path="url(#%s)"><rect x="%s" y="%s" width="%s" height="%s" fill="none" stroke="%s" %s/>`, clip, num(rx), num(ry), num(mk.region.W), num(mk.region.H), mk.color, stroke)
			fmt.Fprintf(&b, `<circle cx="%s" cy="%s" r="9" fill="%s"/><text x="%s" y="%s" fill="#0b0d10" font-size="11" text-anchor="middle" font-weight="bold">%s</text></g>`, num(rx), num(ry), mk.color, num(rx), num(ry+4), xmlEscape(mk.label))
		}
		x += col.width + gap
	}
	// Legend and comment ledger.
	y := header + maxDim(cols) + 24
	legend := []struct{ name, label string }{
		{StatusAnchored, "anchored 锚定有效：对象与变换均未变"},
		{StatusRelocated, "relocated 已迁移：按新仿射矩阵重算（虚线，需人工确认）"},
		{StatusRescaled, "rescaled 画布已变：按尺寸比例重放（虚线，需人工确认）"},
		{StatusOrphaned, "orphaned 关联失效：锚定对象消失，保留原坐标未重贴（虚线）"},
		{StatusAbsolute, "absolute 自由批注：无对象锚点"},
	}
	for i, e := range legend {
		lx := pad + (i%3)*300
		ly := y + (i/3)*20
		dash := e.name == StatusRelocated || e.name == StatusRescaled || e.name == StatusOrphaned
		dashAttr := ""
		if dash {
			dashAttr = ` stroke-dasharray="4 3"`
		}
		fmt.Fprintf(&b, `<rect x="%d" y="%d" width="14" height="10" fill="none" stroke="%s"%s/>`, lx, ly, statusColor(e.name), dashAttr)
		fmt.Fprintf(&b, `<text x="%d" y="%d" fill="#cfd3d8" font-size="11">%s</text>`, lx+20, ly+9, xmlEscape(e.label))
	}
	y += 48
	fmt.Fprintf(&b, `<text x="%d" y="%d" fill="#e6e8ec" font-size="12" font-weight="bold">批注台账（原始文字不可改写，处理状态见 API）</text>`, pad, y)
	y += 18
	i := 0
	for _, g := range sources {
		for _, p := range g.Projections {
			color := palette[i%len(palette)]
			line := fmt.Sprintf("%s 稿本%d 批注 %s · %s · %s", badge(i), g.Revision, p.Annotation.ID, p.Status, comments[p.Annotation.ID])
			fmt.Fprintf(&b, `<text x="%d" y="%d" fill="%s" font-size="11">%s</text>`, pad, y, color, xmlEscape(line))
			y += 16
			i++
		}
	}
	b.WriteString(`</svg>`)
	return b.Bytes()
}

func maxDim(cols []sheetColumn) int {
	m := 0
	for _, c := range cols {
		if c.height > m {
			m = c.height
		}
	}
	return m
}

// badge is the compact sequence number drawn inside marker circles; full
// annotation ids stay in the ledger so no identifier gets truncated.
func badge(i int) string { return strconv.Itoa(i + 1) }
