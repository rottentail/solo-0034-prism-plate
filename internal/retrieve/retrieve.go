// Package retrieve plans selective retrieval of shapes from a historical
// revision into the current document. It restores geometry verbatim and reports
// every group/document attribute it cannot carry, so callers can review the
// scope before a normal revision is created.
package retrieve

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"sort"
	"strings"

	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
)

const (
	ContextDestination = "destination"
	ContextSourceLayer = "source"
	maxSelection       = 64
)

// Item selects one source shape and, for destination context, where it lands.
type Item struct {
	Shape string `json:"shape"`
	Layer string `json:"layer,omitempty"`
	Index *int   `json:"index,omitempty"`
}
type Request struct {
	BaseRevision   int    `json:"base_revision"`
	SourceRevision int    `json:"source_revision"`
	Context        string `json:"context"`
	Items          []Item `json:"items"`
}

type Geometry struct {
	FillRule  string      `json:"fill_rule"`
	Contours  int         `json:"contours"`
	Vertices  int         `json:"vertices"`
	Winding   string      `json:"winding"`
	Holes     bool        `json:"hole_capable"`
	Transform geom.Matrix `json:"transform"`
	Brush     string      `json:"brush"`
	Stops     int         `json:"gradient_stops,omitempty"`
}
type ItemReport struct {
	Shape            string   `json:"shape"`
	SourceLayer      string   `json:"source_layer"`
	DestinationLayer string   `json:"destination_layer"`
	Action           string   `json:"action"`
	Index            int      `json:"index"`
	Geometry         Geometry `json:"geometry"`
	Copied           []string `json:"copied"`
	Adopted          []string `json:"adopted,omitempty"`
	NotPreserved     []string `json:"not_preserved,omitempty"`
}
type Difference struct {
	Scope     string `json:"scope"`
	Node      string `json:"node"`
	Attribute string `json:"attribute"`
	Source    string `json:"source"`
	Current   string `json:"current"`
	Preserved bool   `json:"preserved"`
	Detail    string `json:"detail,omitempty"`
}
type Scope struct {
	RestoredShapes         int      `json:"restored_shapes"`
	UntouchedShapes        int      `json:"untouched_shapes"`
	UnselectedSourceShapes int      `json:"unselected_source_layer_shapes"`
	LayersAdded            []string `json:"layers_added"`
	ShapesReplaced         []string `json:"shapes_replaced,omitempty"`
	LayersBefore           int      `json:"layers_before"`
	LayersAfter            int      `json:"layers_after"`
}
type Plan struct {
	Plate          string       `json:"plate"`
	BaseRevision   int          `json:"base_revision"`
	BaseDigest     string       `json:"base_digest"`
	SourceRevision int          `json:"source_revision"`
	SourceDigest   string       `json:"source_digest"`
	Context        string       `json:"context"`
	Items          []ItemReport `json:"items"`
	ContextDiffs   []Difference `json:"context_differences"`
	CannotPreserve []string     `json:"cannot_preserve"`
	Warnings       []string     `json:"warnings,omitempty"`
	Scope          Scope        `json:"scope"`
	ResultDigest   string       `json:"result_digest"`
	PlanDigest     string       `json:"plan_digest"`
	candidate      scene.Document
}
type Input struct {
	Plate          string
	BaseRevision   int
	Base           scene.Document
	BaseDigest     string
	SourceRevision int
	Source         scene.Document
	SourceDigest   string
	Request        Request
}

// Build resolves the selection against immutable revisions, produces the full
// candidate document and a digest that pins the reviewed scope.
func Build(in Input) (Plan, error) {
	p := Plan{
		Plate: in.Plate, BaseRevision: in.BaseRevision, BaseDigest: in.BaseDigest,
		SourceRevision: in.SourceRevision, SourceDigest: in.SourceDigest,
		Context: in.Request.Context,
		Items:   make([]ItemReport, 0, len(in.Request.Items)), ContextDiffs: []Difference{},
		CannotPreserve: []string{}, Warnings: []string{},
	}
	if in.Request.Context != ContextDestination && in.Request.Context != ContextSourceLayer {
		return p, fmt.Errorf("context must be %q or %q", ContextDestination, ContextSourceLayer)
	}
	if in.Request.BaseRevision < 1 || in.Request.SourceRevision < 1 {
		return p, fmt.Errorf("base_revision and source_revision must be positive")
	}
	if n := len(in.Request.Items); n < 1 || n > maxSelection {
		return p, fmt.Errorf("selection requires 1..%d shapes", maxSelection)
	}
	sourceIndex := indexShapes(in.Source)
	seen := map[string]bool{}
	for _, it := range in.Request.Items {
		if !validate.ID(it.Shape) {
			return p, fmt.Errorf("invalid shape id %q", it.Shape)
		}
		if seen[it.Shape] {
			return p, fmt.Errorf("shape %q selected twice", it.Shape)
		}
		seen[it.Shape] = true
		if _, ok := sourceIndex[it.Shape]; !ok {
			return p, fmt.Errorf("shape %q is not in source revision %d", it.Shape, in.Request.SourceRevision)
		}
		if in.Request.Context == ContextSourceLayer && (it.Layer != "" || it.Index != nil) {
			return p, fmt.Errorf("layer and index are only valid with %q context", ContextDestination)
		}
	}
	candidate := cloneDocument(in.Base)
	if _, err := validate.Compile(candidate); err != nil {
		return p, err
	}
	baseShapes := countShapes(candidate)
	scope := Scope{LayersBefore: len(candidate.Layers), LayersAdded: []string{}}
	selectedPerLayer := map[string]int{}

	if in.Request.Context == ContextDestination {
		for _, it := range in.Request.Items {
			src := sourceIndex[it.Shape]
			report := ItemReport{
				Shape: it.Shape, SourceLayer: src.layer.ID, Action: "appended",
				Geometry: geometryOf(src.shape), Copied: copiedGeometry(src.shape),
			}
			existingLayer, existingShape, found := findShape(candidate, it.Shape)
			if found {
				if it.Layer != "" && it.Layer != existingLayer {
					return p, fmt.Errorf("shape %q already exists in layer %q; restore replaces in place and does not move shapes between layers", it.Shape, existingLayer)
				}
				if it.Index != nil {
					return p, fmt.Errorf("index cannot be used when replacing existing shape %q", it.Shape)
				}
				candidate.Layers[existingShape.layer].Shapes[existingShape.shape] = cloneShape(src.shape)
				report.Action, report.Index, report.DestinationLayer = "replaced", existingShape.shape, existingLayer
				scope.ShapesReplaced = append(scope.ShapesReplaced, it.Shape)
			} else {
				if it.Layer == "" {
					return p, fmt.Errorf("shape %q does not exist in the current document; layer is required", it.Shape)
				}
				li := findLayer(candidate, it.Layer)
				if li < 0 {
					return p, fmt.Errorf("destination layer %q does not exist", it.Layer)
				}
				insert := len(candidate.Layers[li].Shapes)
				if it.Index != nil {
					insert = *it.Index
					if insert < 0 || insert > len(candidate.Layers[li].Shapes) {
						return p, fmt.Errorf("index %d outside layer %q", insert, it.Layer)
					}
				}
				shapes := candidate.Layers[li].Shapes
				shapes = append(shapes, scene.Shape{})
				copy(shapes[insert+1:], shapes[insert:])
				shapes[insert] = cloneShape(src.shape)
				candidate.Layers[li].Shapes = shapes
				report.Index, report.DestinationLayer = insert, it.Layer
				if insert != len(shapes)-1 {
					report.Action = "inserted"
				}
			}
			// The restored shape is always composited through the destination layer.
			// Even a same-named layer may have changed opacity, blend, clip or mask
			// since the source revision, so every group attribute is reported.
			if src.layer.ID != report.DestinationLayer {
				addUnique(&p.CannotPreserve, fmt.Sprintf("shape %q moved from layer %q to layer %q; source stacking position is not preserved", it.Shape, src.layer.ID, report.DestinationLayer))
				report.NotPreserved = append(report.NotPreserved, "source layer stacking position")
			}
			diffs := layerDiffs(src.layer, candidate.Layers[findLayer(candidate, report.DestinationLayer)])
			addDiffs(&p, diffs)
			report.NotPreserved = append(report.NotPreserved, diffNotes(diffs)...)
			for _, d := range diffs {
				if !d.Preserved {
					addUnique(&p.CannotPreserve, fmt.Sprintf("layer %q %s: source %s, destination keeps %s; the restored shape is composited by the destination group", report.DestinationLayer, d.Attribute, d.Source, d.Current))
				}
			}
			selectedPerLayer[src.layer.ID]++
			p.Items = append(p.Items, report)
		}
	} else {
		groups := groupBySourceLayer(in.Request, sourceIndex)
		for _, g := range groups {
			added, err := addSourceLayer(&candidate, g.layer, g.shapes)
			if err != nil {
				return p, err
			}
			scope.LayersAdded = append(scope.LayersAdded, added)
			for i, shape := range g.shapes {
				report := ItemReport{
					Shape: shape.ID, SourceLayer: g.layer.ID, DestinationLayer: added,
					Action: "created-layer", Index: i, Geometry: geometryOf(shape),
					Copied: copiedGeometry(shape),
				}
				report.Adopted = []string{"layer.opacity", "layer.blend"}
				if len(g.layer.Clip) > 0 {
					report.Adopted = append(report.Adopted, "layer.clip")
				}
				if len(g.layer.Mask) > 0 {
					report.Adopted = append(report.Adopted, "layer.mask")
				}
				p.Items = append(p.Items, report)
			}
			selectedPerLayer[g.layer.ID] = len(g.shapes)
			addDiff(&p, Difference{Scope: "layer", Node: added, Attribute: "opacity", Source: number(g.layer.Opacity), Current: number(g.layer.Opacity), Preserved: true, Detail: "copied into a new layer"})
			addDiff(&p, Difference{Scope: "layer", Node: added, Attribute: "blend", Source: g.layer.Blend, Current: g.layer.Blend, Preserved: true, Detail: "copied into a new layer"})
			if len(g.layer.Clip) > 0 {
				addDiff(&p, Difference{Scope: "layer", Node: added, Attribute: "clip", Source: opsText(g.layer.Clip), Current: opsText(g.layer.Clip), Preserved: true, Detail: "copied into a new layer"})
			}
			if len(g.layer.Mask) > 0 {
				addDiff(&p, Difference{Scope: "layer", Node: added, Attribute: "mask", Source: opsText(g.layer.Mask), Current: opsText(g.layer.Mask), Preserved: true, Detail: "copied into a new layer"})
			}
			sourcePos := indexLayer(in.Source, g.layer.ID)
			if sourcePos != len(in.Source.Layers)-1 {
				addUnique(&p.CannotPreserve, fmt.Sprintf("layer %q is recreated at the top of the current stack; its source position %d relative to other layers is not preserved", added, sourcePos))
			} else {
				addUnique(&p.CannotPreserve, fmt.Sprintf("layer %q is recreated at the top of the current stack; source inter-layer relationships to other revisions are not reconstructed", added))
			}
		}
	}

	if in.Source.Background != in.Base.Background {
		addDiff(&p, Difference{Scope: "document", Node: "background", Attribute: "background", Source: colorText(in.Source.Background), Current: colorText(in.Base.Background), Preserved: false, Detail: "document background belongs to the current draft"})
		addUnique(&p.CannotPreserve, fmt.Sprintf("document background: source %s, current %s; restore never changes the plate background", colorText(in.Source.Background), colorText(in.Base.Background)))
	} else {
		addDiff(&p, Difference{Scope: "document", Node: "background", Attribute: "background", Source: colorText(in.Source.Background), Current: colorText(in.Base.Background), Preserved: true})
	}
	if in.Source.Width != in.Base.Width || in.Source.Height != in.Base.Height {
		msg := fmt.Sprintf("canvas size: source %dx%d, current %dx%d; restored geometry keeps source plate coordinates, so shapes, layer clip and layer mask may be clipped or shifted by the current canvas", in.Source.Width, in.Source.Height, in.Base.Width, in.Base.Height)
		addDiff(&p, Difference{Scope: "document", Node: "canvas", Attribute: "canvas", Source: fmt.Sprintf("%dx%d", in.Source.Width, in.Source.Height), Current: fmt.Sprintf("%dx%d", in.Base.Width, in.Base.Height), Preserved: false, Detail: msg})
		addUnique(&p.CannotPreserve, msg)
		addUnique(&p.Warnings, msg)
	}
	// Iterate source document order, never a map, so review output is repeatable.
	var ignoredLayers []string
	for _, l := range in.Source.Layers {
		selected, ok := selectedPerLayer[l.ID]
		if !ok {
			ignoredLayers = append(ignoredLayers, l.ID)
			continue
		}
		n := len(l.Shapes) - selected
		if n > 0 {
			addUnique(&p.CannotPreserve, fmt.Sprintf("source layer %q contained %d unselected shape(s); only the selected objects are restored", l.ID, n))
		}
		p.Scope.UnselectedSourceShapes += n
	}
	if len(ignoredLayers) > 0 {
		addUnique(&p.CannotPreserve, fmt.Sprintf("source layers not selected are not copied: %s", strings.Join(ignoredLayers, ", ")))
	}
	for _, item := range p.Items {
		if item.Geometry.Holes {
			msg := fmt.Sprintf("shape %q has a contour nested inside another under %s fill, so that interior is a hole and stays open; it shows the destination composition, not the source background", item.Shape, item.Geometry.FillRule)
			addUnique(&p.Warnings, msg)
			addUnique(&p.CannotPreserve, "hole interiors reveal the current destination composition")
		}
	}
	// Several items may target the same layer; report where each shape finally
	// rests, so the reviewed plan matches the document that would be committed.
	for i := range p.Items {
		if _, where, ok := findShape(candidate, p.Items[i].Shape); ok {
			p.Items[i].Index = where.shape
		}
	}
	for _, item := range p.Items {
		var shape scene.Shape
		if _, where, ok := findShape(candidate, item.Shape); ok {
			shape = candidate.Layers[where.layer].Shapes[where.shape]
		}
		if x0, y0, x1, y1, ok := bounds(shape); ok && (x0 < 0 || y0 < 0 || x1 > float64(candidate.Width) || y1 > float64(candidate.Height)) {
			addUnique(&p.Warnings, fmt.Sprintf("restored shape %q extends outside the current %dx%d canvas and will be clipped there", item.Shape, candidate.Width, candidate.Height))
		}
	}
	addOverlapWarnings(&p, candidate)
	scope.RestoredShapes = len(p.Items)
	scope.UntouchedShapes = baseShapes - len(scope.ShapesReplaced)
	scope.LayersAfter = len(candidate.Layers)
	p.Scope = scope
	if _, err := validate.Compile(candidate); err != nil {
		return p, fmt.Errorf("restored document is not admissible: %w", err)
	}
	if documentDigest(candidate) == documentDigest(in.Base) {
		addUnique(&p.Warnings, "the restored selection produces a document identical to the base revision; committing it would add a revision with the same scene digest and no visual change")
	}
	p.candidate = candidate
	p.ResultDigest = documentDigest(candidate)
	p.PlanDigest = planDigest(p)
	return p, nil
}

type indexedShape struct {
	layerIndex int
	shapeIndex int
	layer      scene.Layer
	shape      scene.Shape
}
type place struct{ layer, shape int }

func indexShapes(d scene.Document) map[string]indexedShape {
	m := map[string]indexedShape{}
	for li, l := range d.Layers {
		for si, s := range l.Shapes {
			m[s.ID] = indexedShape{layerIndex: li, shapeIndex: si, layer: l, shape: s}
		}
	}
	return m
}
func findLayer(d scene.Document, id string) int {
	for i, l := range d.Layers {
		if l.ID == id {
			return i
		}
	}
	return -1
}
func indexLayer(d scene.Document, id string) int { return findLayer(d, id) }
func findShape(d scene.Document, id string) (string, place, bool) {
	for li, l := range d.Layers {
		for si, s := range l.Shapes {
			if s.ID == id {
				return l.ID, place{li, si}, true
			}
		}
	}
	return "", place{}, false
}
func countShapes(d scene.Document) int {
	n := 0
	for _, l := range d.Layers {
		n += len(l.Shapes)
	}
	return n
}

type group struct {
	layer  scene.Layer
	shapes []scene.Shape
}

// groupBySourceLayer preserves source stacking order inside each source layer,
// regardless of the order in which the caller listed the selection.
func groupBySourceLayer(req Request, idx map[string]indexedShape) []group {
	var order []string
	byLayer := map[string][]indexedShape{}
	for _, it := range req.Items {
		s := idx[it.Shape]
		if _, ok := byLayer[s.layer.ID]; !ok {
			order = append(order, s.layer.ID)
		}
		byLayer[s.layer.ID] = append(byLayer[s.layer.ID], s)
	}
	var out []group
	for _, id := range order {
		shapes := byLayer[id]
		sort.SliceStable(shapes, func(i, j int) bool { return shapes[i].shapeIndex < shapes[j].shapeIndex })
		g := group{layer: shapes[0].layer}
		for _, s := range shapes {
			g.shapes = append(g.shapes, s.shape)
		}
		out = append(out, g)
	}
	return out
}

func addSourceLayer(d *scene.Document, src scene.Layer, shapes []scene.Shape) (string, error) {
	id := allocateLayerID(src.ID, *d)
	if id == "" {
		return "", fmt.Errorf("cannot allocate a destination layer id for %q", src.ID)
	}
	for _, s := range shapes {
		if _, _, ok := findShape(*d, s.ID); ok {
			return "", fmt.Errorf("shape %q already exists in the current document; use %q context to replace it", s.ID, ContextDestination)
		}
	}
	layer := scene.Layer{ID: id, Opacity: src.Opacity, Blend: src.Blend,
		Clip: cloneOps(src.Clip), Mask: cloneOps(src.Mask)}
	for _, s := range shapes {
		layer.Shapes = append(layer.Shapes, cloneShape(s))
	}
	d.Layers = append(d.Layers, layer)
	return id, nil
}
func allocateLayerID(source string, d scene.Document) string {
	const suffix = "-restored"
	base := source
	if len(base)+len(suffix) > 64 {
		base = base[:64-len(suffix)]
	}
	for n := 1; n <= 100; n++ {
		id := base + suffix
		if n > 1 {
			tail := fmt.Sprintf("-%d", n)
			stem := base
			if len(stem)+len(suffix)+len(tail) > 64 {
				stem = stem[:64-len(suffix)-len(tail)]
			}
			id = stem + suffix + tail
		}
		if validate.ID(id) && findLayer(d, id) < 0 {
			return id
		}
	}
	return ""
}

func layerDiffs(src, dst scene.Layer) []Difference {
	var out []Difference
	add := func(attr, s, c string, same bool) {
		out = append(out, Difference{Scope: "layer", Node: dst.ID, Attribute: attr, Source: s, Current: c, Preserved: same})
	}
	add("opacity", number(src.Opacity), number(dst.Opacity), src.Opacity == dst.Opacity)
	add("blend", src.Blend, dst.Blend, src.Blend == dst.Blend)
	add("clip", opsText(src.Clip), opsText(dst.Clip), equalOps(src.Clip, dst.Clip))
	add("mask", opsText(src.Mask), opsText(dst.Mask), equalOps(src.Mask, dst.Mask))
	return out
}
func diffNotes(diffs []Difference) []string {
	var out []string
	for _, d := range diffs {
		if !d.Preserved {
			out = append(out, fmt.Sprintf("%s: source %s, destination keeps %s", d.Attribute, d.Source, d.Current))
		}
	}
	return out
}
func addDiffs(p *Plan, diffs []Difference) {
	for _, d := range diffs {
		addDiff(p, d)
	}
}
func addDiff(p *Plan, d Difference) {
	key := d.Scope + "|" + d.Node + "|" + d.Attribute + "|" + d.Source
	for _, old := range p.ContextDiffs {
		if old.Scope+"|"+old.Node+"|"+old.Attribute+"|"+old.Source == key {
			return
		}
	}
	p.ContextDiffs = append(p.ContextDiffs, d)
}
func addUnique(dst *[]string, value string) {
	for _, old := range *dst {
		if old == value {
			return
		}
	}
	*dst = append(*dst, value)
}

func equalOps(a, b []geom.Op) bool {
	if len(a) == 0 && len(b) == 0 {
		return true
	}
	return reflect.DeepEqual(a, b)
}
func opsText(ops []geom.Op) string {
	if len(ops) == 0 {
		return "none"
	}
	vertices := 0
	for _, o := range ops {
		vertices += len(o.Points)
	}
	return fmt.Sprintf("%d ops/%d vertices", len(ops), vertices)
}
func number(v float64) string { return fmt.Sprintf("%.6g", v) }
func colorText(c scene.Color) string {
	return fmt.Sprintf("rgba(%s,%s,%s,%s)", number(c.R), number(c.G), number(c.B), number(c.A))
}
func copiedGeometry(s scene.Shape) []string {
	out := []string{"path (all contours in order)", "fill_rule", "transform", "brush"}
	if s.Brush.Kind == "linear" {
		out = append(out, "gradient axis and stops (shape-local coordinates)")
	}
	return out
}

func geometryOf(s scene.Shape) Geometry {
	path := geom.Flatten(s.Path)
	g := Geometry{FillRule: s.FillRule, Contours: len(path), Transform: s.Transform, Brush: s.Brush.Kind}
	if s.Brush.Kind == "linear" {
		g.Stops = len(s.Brush.Stops)
	}
	pos, neg := 0, 0
	for _, ring := range path {
		g.Vertices += len(ring)
		a := ringArea(ring)
		if a > 1e-9 {
			pos++
		} else if a < -1e-9 {
			neg++
		}
	}
	switch {
	case g.Contours <= 1:
		g.Winding = "single"
	case pos > 0 && neg > 0:
		g.Winding = "mixed"
	default:
		g.Winding = "consistent"
	}
	// Holes are a visual fact, not a contour count. Nested rings are holes under
	// even-odd fill; under nonzero fill only opposite winding makes a hole.
	// Analysis is capped so adversarial contour sets cannot inflate review cost.
	if len(path) > 1 && len(path) <= 64 && g.Vertices <= 8192 {
		for i, ring := range path {
			if len(ring) == 0 {
				continue
			}
			for j, other := range path {
				if i == j || len(other) < 3 {
					continue
				}
				if ringInside(ring, other) {
					if s.FillRule == "evenodd" || (ringArea(ring) > 0) != (ringArea(other) > 0) {
						g.Holes = true
					}
				}
			}
		}
	} else {
		// Refuse to claim a hole that was not analyzed; a false warning is worse
		// than a missing one.
		g.Holes = false
	}
	return g
}

// ringInside reports whether a representative vertex of ring lies strictly
// inside other. Bounding boxes reject most pairs before any point test.
func ringInside(ring, other geom.Contour) bool {
	rx0, ry0, rx1, ry1 := contourBounds(ring)
	ox0, oy0, ox1, oy1 := contourBounds(other)
	if rx0 < ox0 || ry0 < oy0 || rx1 > ox1 || ry1 > oy1 {
		return false
	}
	return ringContains(other, ring[0])
}
func contourBounds(ring geom.Contour) (float64, float64, float64, float64) {
	minX, minY := math.Inf(1), math.Inf(1)
	maxX, maxY := math.Inf(-1), math.Inf(-1)
	for _, p := range ring {
		minX, minY = math.Min(minX, p.X), math.Min(minY, p.Y)
		maxX, maxY = math.Max(maxX, p.X), math.Max(maxY, p.Y)
	}
	return minX, minY, maxX, maxY
}
func ringContains(ring geom.Contour, p geom.Point) bool {
	inside := false
	for i, a := range ring {
		b := ring[(i+1)%len(ring)]
		if (a.Y > p.Y) != (b.Y > p.Y) {
			x := a.X + (p.Y-a.Y)*(b.X-a.X)/(b.Y-a.Y)
			if x > p.X {
				inside = !inside
			}
		}
	}
	return inside
}
func ringArea(ring geom.Contour) float64 {
	sum := 0.0
	for i, a := range ring {
		b := ring[(i+1)%len(ring)]
		sum += a.X*b.Y - b.X*a.Y
	}
	return sum / 2
}
func bounds(shape scene.Shape) (float64, float64, float64, float64, bool) {
	path := geom.Flatten(shape.Path)
	minX, minY := math.Inf(1), math.Inf(1)
	maxX, maxY := math.Inf(-1), math.Inf(-1)
	for _, ring := range path {
		for _, p := range ring {
			q := shape.Transform.Apply(p)
			minX, minY = math.Min(minX, q.X), math.Min(minY, q.Y)
			maxX, maxY = math.Max(maxX, q.X), math.Max(maxY, q.Y)
		}
	}
	return minX, minY, maxX, maxY, maxX > minX && maxY > minY
}
func overlaps(a, b scene.Shape) bool {
	ax0, ay0, ax1, ay1, ok := bounds(a)
	bx0, by0, bx1, by1, ok2 := bounds(b)
	if !ok || !ok2 {
		return false
	}
	return ax0 < bx1 && bx0 < ax1 && ay0 < by1 && by0 < ay1
}

// addOverlapWarnings compares bounding boxes only. It is a review hint, never a
// claim that pixels overlap or that the visible result equals the old artifact.
func addOverlapWarnings(p *Plan, candidate scene.Document) {
	restored := map[string]bool{}
	for _, item := range p.Items {
		restored[item.Shape] = true
	}
	for _, l := range candidate.Layers {
		var ids []string
		for _, s := range l.Shapes {
			if restored[s.ID] {
				ids = append(ids, s.ID)
			}
		}
		sort.Strings(ids)
		for i := 0; i < len(ids); i++ {
			for j := i + 1; j < len(ids); j++ {
				a, b := shapeInLayer(l, ids[i]), shapeInLayer(l, ids[j])
				if overlaps(a, b) {
					addUnique(&p.Warnings, fmt.Sprintf("restored shapes %q and %q have overlapping bounds in layer %q; their stacking follows the produced layer order, and layer opacity is applied once to the whole group", ids[i], ids[j], l.ID))
				}
			}
			for _, s := range l.Shapes {
				if restored[s.ID] {
					continue
				}
				if overlaps(shapeInLayer(l, ids[i]), s) {
					addUnique(&p.Warnings, fmt.Sprintf("restored shape %q has bounds overlapping existing shape %q in layer %q; the overlap is composited in destination order and the destination layer opacity applies to both", ids[i], s.ID, l.ID))
				}
			}
		}
	}
}
func shapeInLayer(l scene.Layer, id string) scene.Shape {
	for _, s := range l.Shapes {
		if s.ID == id {
			return s
		}
	}
	return scene.Shape{}
}

func cloneShape(s scene.Shape) scene.Shape {
	c := s
	c.Path = cloneOps(s.Path)
	if s.Brush.Stops != nil {
		c.Brush.Stops = append([]scene.Stop(nil), s.Brush.Stops...)
	}
	return c
}
func cloneOps(ops []geom.Op) []geom.Op {
	if ops == nil {
		return nil
	}
	out := make([]geom.Op, len(ops))
	for i, o := range ops {
		out[i] = o
		if o.Points != nil {
			out[i].Points = append([]geom.Point(nil), o.Points...)
		}
	}
	return out
}
func cloneDocument(d scene.Document) scene.Document {
	b, _ := json.Marshal(d)
	var c scene.Document
	_ = json.Unmarshal(b, &c)
	return c
}

// Candidate returns a copy of the document this plan would commit. The plan is
// the caller's review artifact; mutating it cannot alter what will be committed.
func (p Plan) Candidate() scene.Document { return cloneDocument(p.candidate) }

func documentDigest(d scene.Document) string {
	b, _ := json.Marshal(d)
	v := sha256.Sum256(b)
	return hex.EncodeToString(v[:])
}
func planDigest(p Plan) string {
	b, _ := json.Marshal(struct {
		Plate          string       `json:"plate"`
		BaseRevision   int          `json:"base_revision"`
		BaseDigest     string       `json:"base_digest"`
		SourceRevision int          `json:"source_revision"`
		SourceDigest   string       `json:"source_digest"`
		Context        string       `json:"context"`
		Items          []ItemReport `json:"items"`
		ResultDigest   string       `json:"result_digest"`
	}{
		Plate: p.Plate, BaseRevision: p.BaseRevision, BaseDigest: p.BaseDigest,
		SourceRevision: p.SourceRevision, SourceDigest: p.SourceDigest,
		Context: p.Context, Items: p.Items, ResultDigest: p.ResultDigest,
	})
	v := sha256.Sum256(b)
	return hex.EncodeToString(v[:])
}
