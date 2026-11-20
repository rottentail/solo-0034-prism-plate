// Package reframe re-frames an immutable scene onto a different canvas.
//
// A reframe materialises one uniform canvas affine into the scene itself:
//
//   - clip and mask contours live in canvas coordinates, so every point is
//     mapped through the canvas affine;
//   - a shape transform maps shape-local points to the canvas, so it is
//     pre-composed with the canvas affine;
//   - shape paths and brushes stay in shape-local coordinates and are never
//     rewritten. A brush still follows its shape, which is the intended
//     behaviour when the artwork is scaled.
//
// The resulting document is self-contained: any renderer that understands
// prism-plate-v1 reproduces it without knowing that a reframe happened or
// which mode produced it. Source revisions are never touched.
package reframe

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

type Mode string

const (
	// ModeFit scales uniformly so the whole source canvas lands inside the
	// target, centred, leaving margins on the less constrained axis.
	ModeFit Mode = "fit"
	// ModePreserve keeps the physical scale at exactly 1:1 and centres the
	// source; a larger target gains margins, a smaller target trims overflow.
	ModePreserve Mode = "preserve"
	// ModeCrop scales uniformly so the source covers the target, centred,
	// trimming whatever falls outside.
	ModeCrop Mode = "crop"
)

func ParseMode(s string) (Mode, error) {
	switch Mode(s) {
	case ModeFit, ModePreserve, ModeCrop:
		return Mode(s), nil
	}
	return "", fmt.Errorf("mode must be fit, preserve or crop")
}

type Size struct {
	Width  int `json:"width"`
	Height int `json:"height"`
}

func (s Size) check() error {
	if s.Width < 1 || s.Height < 1 || s.Width > 512 || s.Height > 512 {
		return fmt.Errorf("reframe canvas must be 1..512 on each side")
	}
	return nil
}

// Placement is the canvas-space affine p_target = Scale*p_source + Offset.
type Placement struct {
	Scale   float64 `json:"scale"`
	OffsetX float64 `json:"offset_x"`
	OffsetY float64 `json:"offset_y"`
}

// Matrix expresses the placement as a geom.Matrix mapping source canvas
// coordinates into the target canvas.
func (p Placement) Matrix() geom.Matrix {
	return geom.Matrix{p.Scale, 0, 0, p.Scale, p.OffsetX, p.OffsetY}
}

// Margins are the unused bands of the target canvas, in target pixels. They
// are all zero for a pure crop.
type Margins struct {
	Left   float64 `json:"left"`
	Top    float64 `json:"top"`
	Right  float64 `json:"right"`
	Bottom float64 `json:"bottom"`
}

// Rect is an axis-aligned target-canvas-space footprint.
type Rect struct {
	X      float64 `json:"x"`
	Y      float64 `json:"y"`
	Width  float64 `json:"width"`
	Height float64 `json:"height"`
}

// Effect records how a named source node survives the new frame.
type Effect string

const (
	// EffectIntact: the node lies wholly inside the target.
	EffectIntact Effect = "intact"
	// EffectCropped: part of the node is visible, part falls outside.
	EffectCropped Effect = "cropped"
	// EffectRemoved: no part of the node remains inside the target.
	EffectRemoved Effect = "removed"
)

// ShapeImpact describes one source shape after the frame is applied.
type ShapeImpact struct {
	Layer  string `json:"layer"`
	Shape  string `json:"shape"`
	Effect Effect `json:"effect"`
	// Bounds is the flattened path bounds under the resolved transform,
	// in target-canvas coordinates.
	Bounds Rect `json:"bounds"`
	// Visible is false when the shape is removed, or when its layer's clip
	// or mask is entirely outside the target.
	Visible bool `json:"visible"`
	// HiddenBy names the layer whose clip or mask hides this shape, if any.
	HiddenBy string `json:"hidden_by,omitempty"`
}

// LayerImpact describes whether the layer's canvas-space clip and mask
// survive the frame.
type LayerImpact struct {
	Layer      string `json:"layer"`
	ClipEffect Effect `json:"clip_effect,omitempty"`
	ClipBounds *Rect  `json:"clip_bounds,omitempty"`
	MaskEffect Effect `json:"mask_effect,omitempty"`
	MaskBounds *Rect  `json:"mask_bounds,omitempty"`
	Visible    bool   `json:"visible"`
}

// Detail summarises what the user loses: nodes fully removed and nodes that
// keep only part of their footprint.
type Detail struct {
	RemovedShapes []string `json:"removed_shapes"`
	CroppedShapes []string `json:"cropped_shapes"`
	RemovedLayers []string `json:"removed_layers"`
	CroppedLayers []string `json:"cropped_layers"`
}

func (d *Detail) recordShape(id string, e Effect) {
	switch e {
	case EffectRemoved:
		d.RemovedShapes = append(d.RemovedShapes, id)
	case EffectCropped:
		d.CroppedShapes = append(d.CroppedShapes, id)
	}
}

func (d *Detail) recordLayer(id string, e Effect) {
	switch e {
	case EffectRemoved:
		d.RemovedLayers = append(d.RemovedLayers, id)
	case EffectCropped:
		d.CroppedLayers = append(d.CroppedLayers, id)
	}
}

// Preview is a reviewable, non-committed reframe. Document is exactly what a
// later commit would append, and Digest identifies it, so a commit cannot
// silently apply a result other than the one the user reviewed.
type Preview struct {
	Mode         Mode           `json:"mode"`
	Source       Size           `json:"source"`
	SourceDigest string         `json:"source_digest"`
	Target       Size           `json:"target"`
	Placement    Placement      `json:"placement"`
	Margins      Margins        `json:"margins"`
	Document     scene.Document `json:"document"`
	Digest       string         `json:"digest"`
	Layers       []LayerImpact  `json:"layers"`
	Shapes       []ShapeImpact  `json:"shapes"`
	Detail       Detail         `json:"detail"`
	Notes        []string       `json:"notes,omitempty"`
}

// Plan resolves the canvas affine for a mode without touching a document.
func Plan(mode Mode, source, target Size) (Placement, error) {
	if err := source.check(); err != nil {
		return Placement{}, fmt.Errorf("invalid source canvas: %w", err)
	}
	if err := target.check(); err != nil {
		return Placement{}, err
	}
	sw, sh := float64(source.Width), float64(source.Height)
	tw, th := float64(target.Width), float64(target.Height)
	var scale float64
	switch mode {
	case ModeFit:
		scale = math.Min(tw/sw, th/sh)
	case ModePreserve:
		scale = 1
	case ModeCrop:
		scale = math.Max(tw/sw, th/sh)
	default:
		return Placement{}, fmt.Errorf("mode must be fit, preserve or crop")
	}
	return Placement{
		Scale:   snap(scale),
		OffsetX: snap((tw - sw*scale) / 2),
		OffsetY: snap((th - sh*scale) / 2),
	}, nil
}

// snap removes sub-nanometre floating-point noise such as -3.55e-15. Without
// it, an artwork edge that lands exactly on the target boundary could be
// classified as cropped by a rounding error.
func snap(v float64) float64 {
	if math.Abs(v) < 1e-9 {
		return 0
	}
	return v
}

// Apply returns a preview of source re-framed onto target. The input document
// is never mutated. Admission of the produced document is the caller's job so
// this package stays a pure geometric transform.
func Apply(source scene.Document, target Size, mode Mode) (Preview, error) {
	placement, err := Plan(mode, Size{source.Width, source.Height}, target)
	if err != nil {
		return Preview{}, err
	}
	m := placement.Matrix()
	doc := clone(source)
	doc.Width, doc.Height = target.Width, target.Height
	w, h := float64(target.Width), float64(target.Height)
	p := Preview{
		Mode:         mode,
		Source:       Size{source.Width, source.Height},
		SourceDigest: DocumentDigest(source),
		Target:       target,
		Placement:    placement,
		Margins: Margins{
			Left:   math.Max(0, placement.OffsetX),
			Top:    math.Max(0, placement.OffsetY),
			Right:  math.Max(0, w-(float64(source.Width)*placement.Scale+placement.OffsetX)),
			Bottom: math.Max(0, h-(float64(source.Height)*placement.Scale+placement.OffsetY)),
		},
		Shapes: []ShapeImpact{},
		Detail: Detail{RemovedShapes: []string{}, CroppedShapes: []string{}, RemovedLayers: []string{}, CroppedLayers: []string{}},
	}
	for li := range doc.Layers {
		l := &doc.Layers[li]
		impact := LayerImpact{Layer: l.ID, Visible: true}
		if len(l.Clip) > 0 {
			l.Clip = transformOps(l.Clip, m)
			effect, bounds := classify(geom.Flatten(l.Clip), "nonzero", geom.Identity, w, h)
			impact.ClipEffect, impact.ClipBounds = effect, &bounds
			p.Detail.recordLayer(l.ID+"(clip)", effect)
			if effect == EffectRemoved {
				impact.Visible = false
			}
		}
		if len(l.Mask) > 0 {
			l.Mask = transformOps(l.Mask, m)
			effect, bounds := classify(geom.Flatten(l.Mask), "nonzero", geom.Identity, w, h)
			impact.MaskEffect, impact.MaskBounds = effect, &bounds
			p.Detail.recordLayer(l.ID+"(mask)", effect)
			if effect == EffectRemoved {
				impact.Visible = false
			}
		}
		p.Layers = append(p.Layers, impact)
		for si := range l.Shapes {
			s := &l.Shapes[si]
			s.Transform = compose(m, s.Transform)
			effect, bounds := classify(geom.Flatten(s.Path), s.FillRule, s.Transform, w, h)
			sh := ShapeImpact{Layer: l.ID, Shape: s.ID, Effect: effect, Bounds: bounds, Visible: effect != EffectRemoved}
			if !impact.Visible {
				sh.Visible = false
				sh.HiddenBy = l.ID
			}
			p.Shapes = append(p.Shapes, sh)
			p.Detail.recordShape(s.ID, effect)
		}
	}
	p.Document = doc
	p.Digest = DocumentDigest(doc)
	p.Notes = notes(p)
	return p, nil
}

func notes(p Preview) []string {
	out := []string{"clip and mask contours move with the canvas; shape paths and brushes stay shape-local"}
	switch p.Mode {
	case ModeFit:
		out = append(out, fmt.Sprintf("uniform fit scale %.6g; margins fill the remaining target", p.Placement.Scale))
	case ModePreserve:
		out = append(out, "physical 1:1 scale; artwork is never resampled")
	case ModeCrop:
		out = append(out, fmt.Sprintf("uniform cover scale %.6g; content outside the target is trimmed", p.Placement.Scale))
	}
	if len(p.Detail.RemovedShapes) > 0 || len(p.Detail.RemovedLayers) > 0 {
		out = append(out, "some detail disappears entirely; the commit requires explicit acknowledgement")
	} else if len(p.Detail.CroppedShapes) > 0 || len(p.Detail.CroppedLayers) > 0 {
		out = append(out, "some detail is trimmed at the target edge; check the preview before committing")
	}
	if p.Source.Width != p.Target.Width || p.Source.Height != p.Target.Height {
		out = append(out, "the target revision gets a new scene digest; earlier revisions and their artifacts are unchanged")
	}
	return out
}

func clone(d scene.Document) scene.Document {
	b, _ := json.Marshal(d)
	var out scene.Document
	_ = json.Unmarshal(b, &out)
	return out
}

func transformOps(ops []geom.Op, m geom.Matrix) []geom.Op {
	out := make([]geom.Op, len(ops))
	for i, o := range ops {
		n := geom.Op{Kind: o.Kind}
		if len(o.Points) > 0 {
			n.Points = make([]geom.Point, len(o.Points))
			for j, pt := range o.Points {
				n.Points[j] = m.Apply(pt)
			}
		}
		out[i] = n
	}
	return out
}

// compose returns outer*inner in the geom layout [a b c d e f]:
//
//	x' = a*x + c*y + e
//	y' = b*x + d*y + f
//
// Applying the result to a point equals applying inner and then outer.
func compose(outer, inner geom.Matrix) geom.Matrix {
	a1, b1, c1, d1, e1, f1 := outer[0], outer[1], outer[2], outer[3], outer[4], outer[5]
	a2, b2, c2, d2, e2, f2 := inner[0], inner[1], inner[2], inner[3], inner[4], inner[5]
	return geom.Matrix{
		a1*a2 + c1*b2,
		b1*a2 + d1*b2,
		a1*c2 + c1*d2,
		b1*c2 + d1*d2,
		a1*e2 + c1*f2 + e1,
		b1*e2 + d1*f2 + f1,
	}
}

// transformPath maps a flattened path through m.
func transformPath(path geom.Path, m geom.Matrix) geom.Path {
	out := make(geom.Path, len(path))
	for i, ring := range path {
		out[i] = make(geom.Contour, len(ring))
		for j, pt := range ring {
			out[i][j] = m.Apply(pt)
		}
	}
	return out
}

func boundsOf(paths geom.Path) Rect {
	first := true
	var minX, minY, maxX, maxY float64
	for _, ring := range paths {
		for _, pt := range ring {
			if first {
				minX, minY, maxX, maxY = pt.X, pt.Y, pt.X, pt.Y
				first = false
				continue
			}
			minX, minY = math.Min(minX, pt.X), math.Min(minY, pt.Y)
			maxX, maxY = math.Max(maxX, pt.X), math.Max(maxY, pt.Y)
		}
	}
	if first {
		return Rect{}
	}
	return Rect{X: minX, Y: minY, Width: maxX - minX, Height: maxY - minY}
}

// classify reports how a filled path intersects the target canvas. The path is
// evaluated in shape-local space and mapped through m before testing; w and h
// are the target canvas dimensions. Overlap is an exact test against the
// canvas rectangle, not a bounding-box estimate, so a hollow contour whose
// bounds cover the canvas is still reported as removed.
func classify(path geom.Path, rule string, m geom.Matrix, w, h float64) (Effect, Rect) {
	painted := transformPath(path, m)
	bounds := boundsOf(painted)
	if len(painted) == 0 {
		return EffectRemoved, bounds
	}
	// A boundary tolerance keeps arithmetic noise from turning a flush edge
	// into a false crop.
	const tolerance = 1e-7
	allInside := true
	for _, ring := range painted {
		for _, pt := range ring {
			if pt.X < -tolerance || pt.Y < -tolerance || pt.X > w+tolerance || pt.Y > h+tolerance {
				allInside = false
			}
		}
	}
	if allInside {
		return EffectIntact, bounds
	}
	if overlaps(painted, rule, w, h) {
		return EffectCropped, bounds
	}
	return EffectRemoved, bounds
}

var canvasCorners = [4]geom.Point{{X: 0, Y: 0}, {X: 1, Y: 0}, {X: 1, Y: 1}, {X: 0, Y: 1}}

func overlaps(painted geom.Path, rule string, w, h float64) bool {
	// Any contour vertex inside the canvas.
	for _, ring := range painted {
		for _, pt := range ring {
			if pt.X >= 0 && pt.Y >= 0 && pt.X <= w && pt.Y <= h {
				return true
			}
		}
	}
	// Any canvas corner inside the filled path.
	for _, c := range canvasCorners {
		if painted.Contains(geom.Point{X: c.X * w, Y: c.Y * h}, rule) {
			return true
		}
	}
	// Any contour edge crossing a canvas edge.
	rect := [][2]geom.Point{
		{{X: 0, Y: 0}, {X: w, Y: 0}},
		{{X: w, Y: 0}, {X: w, Y: h}},
		{{X: w, Y: h}, {X: 0, Y: h}},
		{{X: 0, Y: h}, {X: 0, Y: 0}},
	}
	for _, ring := range painted {
		for i, a := range ring {
			b := ring[(i+1)%len(ring)]
			for _, e := range rect {
				if segmentsCross(a, b, e[0], e[1]) {
					return true
				}
			}
		}
	}
	return false
}

func segmentsCross(a, b, c, d geom.Point) bool {
	o := func(p, q, r geom.Point) float64 {
		return (q.X-p.X)*(r.Y-p.Y) - (q.Y-p.Y)*(r.X-p.X)
	}
	d1 := o(c, d, a)
	d2 := o(c, d, b)
	d3 := o(a, b, c)
	d4 := o(a, b, d)
	return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))
}

// DocumentDigest is the canonical scene digest used by revisions. It matches
// repository.SceneHash so a preview digest can be compared before committing.
func DocumentDigest(d scene.Document) string {
	b, _ := json.Marshal(d)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}
