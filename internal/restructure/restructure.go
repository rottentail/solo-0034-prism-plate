// Package restructure proposes appearance-preserving layer merges and splits.
//
// Every candidate is graded into one of three equivalence classes:
//
//   - exact: a compositing identity that holds for any background and any
//     layers outside the edited range, proven from structure (associativity
//     of normal source-over, or disjoint coverage) and confirmed by a
//     deterministic re-render of the whole document.
//   - bounded: not a universal identity, but the re-rendered document matches
//     the original within the caller's per-channel tolerance on the current
//     background; a different background or neighbouring layers may diverge.
//   - refused: the edit cannot be expressed in the scene contract, or the
//     measured difference exceeds the tolerance; reasons explain what fails.
//
// Candidates always remain editable shape scenes in the prism-plate-v1
// contract; no bitmap substitution is ever produced.
package restructure

import (
	"context"
	"fmt"
	"prismplate/internal/geom"
	"prismplate/internal/raster"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"reflect"
	"sort"
	"strconv"
	"strings"
)

const (
	Exact   = "exact"
	Bounded = "bounded"
	Refused = "refused"
)

// Op describes one edit. Merge takes layer ids in any order (stack order is
// used); split takes a layer id, the shape ids to extract, and whether the
// extracted layer sits above or below the remainder.
type Op struct {
	Kind     string   `json:"kind"`
	Layers   []string `json:"layers,omitempty"`
	Layer    string   `json:"layer,omitempty"`
	Shapes   []string `json:"shapes,omitempty"`
	Position string   `json:"position,omitempty"`
	NewID    string   `json:"new_id,omitempty"`
}

// Request carries an optional op; without one, Preview discovers candidates.
// Tolerance is the accepted per-channel RGBA8 delta (0..255, default 0).
type Request struct {
	Op        *Op `json:"op,omitempty"`
	Tolerance int `json:"tolerance"`
}

// Plan is the graded outcome of one candidate edit.
type Plan struct {
	Op                    Op              `json:"op"`
	Class                 string          `json:"class"`
	BackgroundIndependent bool            `json:"background_independent"`
	MeasuredMaxDelta      int             `json:"measured_max_delta"`
	Tolerance             int             `json:"tolerance"`
	Reasons               []string        `json:"reasons"`
	Document              *scene.Document `json:"document,omitempty"`
}

// Discovery lists viable candidates and refused edits with their reasons.
type Discovery struct {
	Candidates []Plan `json:"candidates"`
	Rejected   []Plan `json:"rejected"`
}

// Refusal carries the rejected plan so transports can explain the refusal.
type Refusal struct{ Plan Plan }

func (e Refusal) Error() string {
	return "restructure refused: " + strings.Join(e.Plan.Reasons, "; ")
}

// Evaluate grades one edit against a document. A refused plan is a normal
// result, not an error; errors are reserved for malformed requests.
func Evaluate(ctx context.Context, doc scene.Document, op Op, tolerance int) (Plan, error) {
	if tolerance < 0 || tolerance > 255 {
		return Plan{}, fmt.Errorf("tolerance must be within 0..255")
	}
	switch op.Kind {
	case "merge":
		return evaluateMerge(ctx, doc, op, tolerance)
	case "split":
		return evaluateSplit(ctx, doc, op, tolerance)
	}
	return Plan{}, fmt.Errorf("unknown restructure kind %q", op.Kind)
}

// Discover enumerates adjacent-layer merges and disjoint-group splits.
func Discover(ctx context.Context, doc scene.Document, tolerance int) (Discovery, error) {
	if tolerance < 0 || tolerance > 255 {
		return Discovery{}, fmt.Errorf("tolerance must be within 0..255")
	}
	var d Discovery
	keep := func(p Plan) {
		p.Document = nil
		if p.Class == Refused {
			d.Rejected = append(d.Rejected, p)
		} else {
			d.Candidates = append(d.Candidates, p)
		}
	}
	for i := 0; i+1 < len(doc.Layers); i++ {
		p, err := Evaluate(ctx, doc, Op{Kind: "merge", Layers: []string{doc.Layers[i].ID, doc.Layers[i+1].ID}}, tolerance)
		if err != nil {
			return d, err
		}
		keep(p)
	}
	for _, l := range doc.Layers {
		groups := components(l)
		if len(groups) < 2 {
			continue
		}
		top := groups[len(groups)-1]
		ids := make([]string, 0, len(top))
		for _, i := range top {
			ids = append(ids, l.Shapes[i].ID)
		}
		p, err := Evaluate(ctx, doc, Op{Kind: "split", Layer: l.ID, Shapes: ids, Position: "above"}, tolerance)
		if err != nil {
			return d, err
		}
		if len(groups) > 2 {
			p.Reasons = append(p.Reasons, fmt.Sprintf("layer %q has %d disjoint coverage groups; further splits are possible", l.ID, len(groups)))
		}
		keep(p)
	}
	return d, nil
}

func evaluateMerge(ctx context.Context, doc scene.Document, op Op, tol int) (Plan, error) {
	plan := Plan{Op: op, Class: Refused, Tolerance: tol}
	refuse := func(format string, args ...any) (Plan, error) {
		plan.Reasons = append(plan.Reasons, fmt.Sprintf(format, args...))
		return plan, nil
	}
	if len(op.Layers) < 2 {
		return refuse("merge requires at least two layer ids")
	}
	index := map[string]int{}
	for i, l := range doc.Layers {
		index[l.ID] = i
	}
	order := []int{}
	seen := map[int]bool{}
	for _, id := range op.Layers {
		i, ok := index[id]
		if !ok {
			return refuse("layer %q not found", id)
		}
		if seen[i] {
			return refuse("layer %q listed twice", id)
		}
		seen[i] = true
		order = append(order, i)
	}
	sort.Ints(order)
	for i := 1; i < len(order); i++ {
		if order[i] != order[i-1]+1 {
			return refuse("layers %q and %q are not adjacent; merging across %q would reorder the stack", doc.Layers[order[i-1]].ID, doc.Layers[order[i]].ID, doc.Layers[order[i-1]+1].ID)
		}
	}
	group := make([]scene.Layer, 0, len(order))
	for _, i := range order {
		group = append(group, doc.Layers[i])
	}
	total := 0
	for _, l := range group {
		total += len(l.Shapes)
	}
	if total > 32 {
		return refuse("merged layer would hold %d shapes; the contract allows at most 32", total)
	}
	base := group[0]
	for _, l := range group[1:] {
		if !opsEqual(l.Clip, base.Clip) {
			return refuse("clip regions differ between %q and %q; one merged layer cannot keep both", base.ID, l.ID)
		}
		if !opsEqual(l.Mask, base.Mask) {
			return refuse("mask regions differ between %q and %q; one merged layer cannot keep both", base.ID, l.ID)
		}
		if l.Blend != base.Blend {
			return refuse("blend modes differ (%q on %q vs %q on %q); merged compositing would change", base.Blend, base.ID, l.Blend, l.ID)
		}
	}
	fold := false
	for _, l := range group[1:] {
		if l.Opacity != base.Opacity {
			fold = true
		}
	}
	if fold && base.Blend != "normal" {
		return refuse("group opacity differs and blend %q is not normal; folding opacity into brush alpha is only defined for normal layers", base.Blend)
	}
	merged := base
	merged.Shapes = nil
	for _, l := range group {
		for _, sh := range l.Shapes {
			if fold && l.Opacity != 1 {
				sh.Brush = scaleBrush(sh.Brush, l.Opacity)
			}
			merged.Shapes = append(merged.Shapes, sh)
		}
	}
	if fold {
		merged.Opacity = 1
		plan.Reasons = append(plan.Reasons, "differing group opacity folded into brush alpha; merged layer runs at opacity 1")
	}
	taken := nodeIDs(doc)
	for _, l := range group {
		delete(taken, l.ID)
	}
	switch {
	case op.NewID != "" && !validate.ID(op.NewID):
		return refuse("new_id %q is not a valid identifier", op.NewID)
	case op.NewID != "" && taken[op.NewID]:
		return refuse("new_id %q collides with an existing node id", op.NewID)
	case op.NewID != "":
		merged.ID = op.NewID
	}
	// Structural exactness: identical gating plus either associativity of
	// normal source-over (opacity 1, any coverage, opaque overlaps included)
	// or coverage disjointness that lets group opacity and blend commute.
	exact := false
	switch {
	case fold:
		exact = foldedExact(group)
	case base.Opacity == 1 && base.Blend == "normal":
		exact = true
	default:
		exact = crossLayerDisjoint(group)
	}
	layers := make([]scene.Layer, 0, len(doc.Layers)-len(group)+1)
	layers = append(layers, doc.Layers[:order[0]]...)
	layers = append(layers, merged)
	layers = append(layers, doc.Layers[order[len(order)-1]+1:]...)
	next := doc
	next.Layers = layers
	if _, err := validate.Compile(next); err != nil {
		return refuse("merged scene fails admission: %v", err)
	}
	return classify(ctx, doc, next, exact, plan)
}

func evaluateSplit(ctx context.Context, doc scene.Document, op Op, tol int) (Plan, error) {
	plan := Plan{Op: op, Class: Refused, Tolerance: tol}
	refuse := func(format string, args ...any) (Plan, error) {
		plan.Reasons = append(plan.Reasons, fmt.Sprintf(format, args...))
		return plan, nil
	}
	position := op.Position
	if position == "" {
		position = "above"
	}
	if position != "above" && position != "below" {
		return Plan{}, fmt.Errorf("position must be above or below")
	}
	plan.Op.Position = position
	li := -1
	for i, l := range doc.Layers {
		if l.ID == op.Layer {
			li = i
		}
	}
	if li < 0 {
		return refuse("layer %q not found", op.Layer)
	}
	layer := doc.Layers[li]
	if len(op.Shapes) == 0 {
		return refuse("split requires at least one shape id")
	}
	idxOf := map[string]int{}
	for i, s := range layer.Shapes {
		idxOf[s.ID] = i
	}
	sel := map[int]bool{}
	for _, id := range op.Shapes {
		i, ok := idxOf[id]
		if !ok {
			return refuse("shape %q not found in layer %q", id, layer.ID)
		}
		sel[i] = true
	}
	if len(sel) == len(layer.Shapes) {
		return refuse("split must leave at least one shape in %q", layer.ID)
	}
	if len(doc.Layers) >= 16 {
		return refuse("layer capacity reached: the contract allows at most 16 layers")
	}
	var extracted, remainder []scene.Shape
	for i, s := range layer.Shapes {
		if sel[i] {
			extracted = append(extracted, s)
		} else {
			remainder = append(remainder, s)
		}
	}
	isSuffix, isPrefix := true, true
	for i := range layer.Shapes {
		if sel[i] != (i >= len(layer.Shapes)-len(sel)) {
			isSuffix = false
		}
		if sel[i] != (i < len(sel)) {
			isPrefix = false
		}
	}
	orderPreserved := (position == "above" && isSuffix) || (position == "below" && isPrefix)
	// Structural exactness: a plain layer splits by associativity when the
	// stacking order survives, or when reordered pairs share no coverage; a
	// layer with group opacity or a blend mode splits exactly only when the
	// two resulting groups share no coverage at all.
	plain := layer.Opacity == 1 && layer.Blend == "normal"
	exact := false
	switch {
	case plain && orderPreserved:
		exact = true
	case plain:
		exact = crossedDisjoint(layer, sel, position)
	default:
		exact = groupsDisjoint(extracted, remainder)
	}
	taken := nodeIDs(doc)
	var xid string
	switch {
	case op.NewID != "" && !validate.ID(op.NewID):
		return refuse("new_id %q is not a valid identifier", op.NewID)
	case op.NewID != "" && taken[op.NewID]:
		return refuse("new_id %q collides with an existing node id", op.NewID)
	case op.NewID != "":
		xid = op.NewID
	default:
		xid = freshID(layer.ID, taken)
	}
	keep := layer
	keep.Shapes = remainder
	out := layer
	out.ID = xid
	out.Shapes = extracted
	if !plain || len(layer.Clip) > 0 || len(layer.Mask) > 0 {
		plan.Reasons = append(plan.Reasons, "both resulting layers keep the original opacity, blend, clip and mask")
	}
	layers := make([]scene.Layer, 0, len(doc.Layers)+1)
	layers = append(layers, doc.Layers[:li]...)
	if position == "below" {
		layers = append(layers, out, keep)
	} else {
		layers = append(layers, keep, out)
	}
	layers = append(layers, doc.Layers[li+1:]...)
	next := doc
	next.Layers = layers
	if _, err := validate.Compile(next); err != nil {
		return refuse("split scene fails admission: %v", err)
	}
	return classify(ctx, doc, next, exact, plan)
}

// classify combines the structural verdict with a deterministic re-render of
// the whole document against the current background.
func classify(ctx context.Context, before, after scene.Document, exact bool, plan Plan) (Plan, error) {
	delta, err := renderDelta(ctx, before, after)
	if err != nil {
		return plan, err
	}
	plan.MeasuredMaxDelta = delta
	switch {
	case exact && delta == 0:
		plan.Class = Exact
		plan.BackgroundIndependent = true
		plan.Reasons = append(plan.Reasons, "compositing identity holds for any background; confirmed by deterministic re-render with zero channel delta")
	case exact:
		plan.Reasons = append(plan.Reasons, fmt.Sprintf("structural proof claims exactness but re-render differs by %d; refusing as a precaution", delta))
	case delta <= plan.Tolerance:
		plan.Class = Bounded
		plan.Reasons = append(plan.Reasons, "matches the original within tolerance on the current background only; a different background or layers outside the edit may diverge")
	default:
		plan.Reasons = append(plan.Reasons, fmt.Sprintf("measured max channel delta %d exceeds tolerance %d on the current background", delta, plan.Tolerance))
	}
	if plan.Class == Exact || plan.Class == Bounded {
		doc := after
		plan.Document = &doc
	}
	return plan, nil
}

// renderDelta is the exhaustive per-channel RGBA8 difference of the two
// documents rendered by the authoritative pipeline at the current background.
func renderDelta(ctx context.Context, a, b scene.Document) (int, error) {
	ca, err := validate.Compile(a)
	if err != nil {
		return 0, err
	}
	cb, err := validate.Compile(b)
	if err != nil {
		return 0, err
	}
	ia, err := raster.Image(ctx, ca)
	if err != nil {
		return 0, err
	}
	ib, err := raster.Image(ctx, cb)
	if err != nil {
		return 0, err
	}
	if !ia.Rect.Eq(ib.Rect) || len(ia.Pix) != len(ib.Pix) {
		return 0, fmt.Errorf("canvas dimensions differ")
	}
	worst := 0
	for i := range ia.Pix {
		d := int(ia.Pix[i]) - int(ib.Pix[i])
		if d < 0 {
			d = -d
		}
		if d > worst {
			worst = d
		}
	}
	return worst, nil
}

func opsEqual(a, b []geom.Op) bool {
	if len(a) == 0 && len(b) == 0 {
		return true
	}
	return reflect.DeepEqual(a, b)
}

// scaleBrush folds a group opacity into brush alpha; gradient stops are
// copied so the source document is never mutated.
func scaleBrush(b scene.Brush, o float64) scene.Brush {
	if b.Kind == "solid" {
		b.Color.A *= o
		return b
	}
	stops := make([]scene.Stop, len(b.Stops))
	copy(stops, b.Stops)
	for i := range stops {
		stops[i].Color.A *= o
	}
	b.Stops = stops
	return b
}

func nodeIDs(doc scene.Document) map[string]bool {
	t := map[string]bool{}
	for _, l := range doc.Layers {
		t[l.ID] = true
		for _, s := range l.Shapes {
			t[s.ID] = true
		}
	}
	return t
}

func freshID(base string, taken map[string]bool) string {
	for i := 1; ; i++ {
		suffix := "-x" + strconv.Itoa(i)
		id := base
		if len(id)+len(suffix) > 64 {
			id = id[:64-len(suffix)]
		}
		id += suffix
		if validate.ID(id) && !taken[id] {
			return id
		}
	}
}
