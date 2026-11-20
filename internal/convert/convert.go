package convert

import (
	"fmt"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
)

// Reliability bands tell the caller how much trust the geometric evidence has.
const (
	ReliabilityExact  = "exact"  // clean topology, all equivalence checks pass
	ReliabilityApprox = "approx" // narrow or near-coincident geometry forced snapping
	ReliabilityReject = "reject" // topology could not be made safe; no candidate
)

// maxOps mirrors validate.path's 256-operation admission limit. A candidate
// beyond it could never compile, so it is refused rather than silently shipped.
const maxOps = 256

// Report is the geometric evidence attached to every conversion candidate.
type Report struct {
	PlateID            string   `json:"plate_id"`
	LayerID            string   `json:"layer_id"`
	ShapeID            string   `json:"shape_id"`
	SourceRule         string   `json:"source_rule"`
	TargetRule         string   `json:"target_rule"`
	Reliability        string   `json:"reliability"`
	Warnings           []string `json:"warnings"`
	SourceContours     int      `json:"source_contours"`
	SourceVertices     int      `json:"source_vertices"`
	OutputContours     int      `json:"output_contours"`
	OutputVertices     int      `json:"output_vertices"`
	Operations         int      `json:"operations"`
	RemovedCollinear   int      `json:"removed_collinear_vertices"`
	SplitVertices      int      `json:"split_vertices_added"`
	Intersections      int      `json:"intersection_vertices"`
	CoincidentContacts int      `json:"coincident_contacts"`
	MinChannelLocal    float64  `json:"min_channel_local_units"`
	MinChannelDevice   float64  `json:"min_channel_device_units"`
	SourceArea         float64  `json:"source_signed_fill_area"`
	CandidateArea      float64  `json:"candidate_signed_fill_area"`
	AreaDelta          float64  `json:"area_absolute_delta"`
	AreaTolerance      float64  `json:"area_tolerance"`
	GridPoints         int      `json:"grid_points_checked"`
	GridMismatches     int      `json:"grid_mismatches"`
	GridOnEdgeSkipped  int      `json:"grid_on_edge_skipped"`
	CandidateCompiles  bool     `json:"candidate_compiles"`
	Approximation      bool     `json:"approximation_required"`
	NodeBudgetBlocked  bool     `json:"node_budget_blocked"`
}

// Result is the read-only revision candidate plus its evidence. The source
// plate is never modified; the caller submits the document through the normal
// revision flow only after reviewing the report.
type Result struct {
	Document scene.Document `json:"candidate_document"`
	FillRule string         `json:"fill_rule"`
	Report   Report         `json:"report"`
}

// orientedFillArea returns the exact visible filled area regardless of how the
// source rings happen to be oriented. Both fill rules are measured through one
// shared analytic routine so source and candidate use the same area definition.
func orientedFillArea(path geom.Path, rule string, eps float64) float64 {
	area, err := exactFilledArea(path, rule, eps)
	if err != nil {
		// Degenerate inputs are separately rejected by Shape; keep the metric
		// finite so callers never panic on an unconvertible diagnostic path.
		return math.NaN()
	}
	return area
}

func flattenPoints(path geom.Path) []v2 {
	var out []v2
	for _, ring := range path {
		out = append(out, ring...)
	}
	return out
}

// matrixScale reports the smaller singular-axis scale of an affine matrix,
// used to convert local narrow-channel widths into device units.
func matrixScale(m geom.Matrix) float64 {
	// Columns are the images of the x and y basis vectors.
	sx := math.Hypot(m[0], m[1])
	sy := math.Hypot(m[2], m[3])
	return math.Min(sx, sy)
}

// simplifyCollinear removes straight-through vertices while preserving
// endpoints, T-junctions (vertices whose removal changes a crossing) and any
// corner. Points within eps of the supporting line with projection inside the
// segment are considered collinear.
func simplifyCollinear(ring []v2, eps float64) ([]v2, int) {
	if len(ring) <= 3 {
		return ring, 0
	}
	removed := 0
	out := make([]v2, 0, len(ring))
	n := len(ring)
	removable := make([]bool, n)
	for i := 0; i < n; i++ {
		a, p, b := ring[(i-1+n)%n], ring[i], ring[(i+1)%n]
		ab := sub(b, a)
		ap := sub(p, a)
		l := length(ab)
		if l <= eps {
			continue
		}
		if math.Abs(cross(ab, ap)) <= eps*l {
			t := dot(ap, ab) / dot(ab, ab)
			if t > eps/l && t < 1-eps/l {
				removable[i] = true
			}
		}
	}
	for i, p := range ring {
		if removable[i] {
			removed++
			continue
		}
		out = append(out, p)
	}
	if len(out) < 3 {
		return ring, 0
	}
	return out, removed
}

// toOps renders loops back into the existing editable M/L/Z closed path
// contract. No curve verbs are invented: the arrangement works on the same
// flattened polyline geometry the raster and vector proofs already use.
func toOps(loops [][]v2) []geom.Op {
	var ops []geom.Op
	for _, ring := range loops {
		ops = append(ops, geom.Op{Kind: "M", Points: []geom.Point{ring[0]}})
		for _, p := range ring[1:] {
			ops = append(ops, geom.Op{Kind: "L", Points: []geom.Point{p}})
		}
		ops = append(ops, geom.Op{Kind: "Z"})
	}
	return ops
}

// gridCheck samples the source and candidate fills on a deterministic jittered
// lattice over the local bounding box. Samples landing on an edge (where the
// ray-cast predicate is ambiguous by construction) are skipped and counted.
func gridCheck(source, candidate geom.Path, srcRule, dstRule string, n int, eps float64) (checked, mismatches, onEdge int) {
	pts := flattenPoints(source)
	min, max, _ := bbox(pts)
	grow := math.Max(max.X-min.X, max.Y-min.Y) * 0.05
	min.X -= grow
	min.Y -= grow
	max.X += grow
	max.Y += grow
	// Deterministic low-discrepancy jitter without external dependencies.
	jitter := func(i int) float64 {
		x := float64(i) * 0.7548776662466927 // golden ratio conjugate mod 1
		return x - math.Floor(x)
	}
	for iy := 0; iy < n; iy++ {
		for ix := 0; ix < n; ix++ {
			fx := (float64(ix) + 0.25 + 0.5*jitter(ix*7+iy*13)) / float64(n)
			fy := (float64(iy) + 0.25 + 0.5*jitter(ix*11+iy*5)) / float64(n)
			p := geom.Point{X: min.X + fx*(max.X-min.X), Y: min.Y + fy*(max.Y-min.Y)}
			if nearAnyEdge(source, p, eps) || nearAnyEdge(candidate, p, eps) {
				onEdge++
				continue
			}
			checked++
			if source.Contains(p, srcRule) != candidate.Contains(p, dstRule) {
				mismatches++
			}
		}
	}
	return checked, mismatches, onEdge
}

func nearAnyEdge(path geom.Path, p v2, eps float64) bool {
	for _, ring := range path {
		for i, a := range ring {
			b := ring[(i+1)%len(ring)]
			if pointSegmentDistance(p, a, b) <= eps*4 {
				return true
			}
		}
	}
	return false
}

// narrowestChannel measures the minimum non-zero gap between distinct
// arrangement edges; sub-eps gaps already produced contact warnings.
func narrowestChannel(ar *arrangement) float64 {
	best := math.Inf(1)
	var segs [][2]v2
	for h, e := range ar.edges {
		if h > e.twin {
			continue
		}
		segs = append(segs, [2]v2{ar.points[e.from], ar.points[e.to]})
	}
	for i := 0; i < len(segs); i++ {
		for j := i + 1; j < len(segs); j++ {
			if d := segmentDistance(segs[i][0], segs[i][1], segs[j][0], segs[j][1]); d > 0 && d < best {
				best = d
			}
		}
	}
	if math.IsInf(best, 1) {
		return 0
	}
	return best
}

// Shape converts one shape's contours to targetRule, preserving the visible
// region. It returns a fully populated Report even when reliability is not
// exact; hard topology or budget failures return an error and no candidate.
func Shape(plateID, layerID string, document scene.Document, shape scene.Shape, targetRule string) (Result, error) {
	if targetRule != "nonzero" && targetRule != "evenodd" {
		return Result{}, fmt.Errorf("target fill rule must be nonzero or evenodd")
	}
	if shape.FillRule == targetRule {
		return Result{}, fmt.Errorf("shape %q already uses %s", shape.ID, targetRule)
	}
	report := Report{
		PlateID: plateID, LayerID: layerID, ShapeID: shape.ID,
		SourceRule: shape.FillRule, TargetRule: targetRule,
	}
	source := geom.Flatten(shape.Path)
	if len(source) == 0 {
		return Result{}, fmt.Errorf("shape %q has no closed contours", shape.ID)
	}
	report.SourceContours = len(source)
	min, max, _ := bbox(flattenPoints(source))
	eps := tolerance(min, max)

	var rings [][]v2
	var sourceVerts int
	for _, ring := range source {
		clean, err := dedupRing(ring, eps)
		if err != nil {
			report.Reliability = ReliabilityReject
			return Result{}, fmt.Errorf("shape %q: %w", shape.ID, err)
		}
		sourceVerts += len(clean)
		rings = append(rings, clean)
	}
	report.SourceVertices = sourceVerts

	ar, err := buildArrangement(rings, eps)
	if err != nil {
		report.Reliability = ReliabilityReject
		report.Warnings = append(report.Warnings, err.Error())
		return Result{}, fmt.Errorf("fill-rule conversion refused for shape %q: %w", shape.ID, err)
	}
	loops, err := arrangementToLoops(ar, source, shape.FillRule)
	if err != nil {
		report.Reliability = ReliabilityReject
		report.Warnings = append(report.Warnings, err.Error())
		return Result{}, fmt.Errorf("fill-rule conversion refused for shape %q: %w", shape.ID, err)
	}
	report.Intersections = len(ar.points) - sourceVerts
	if report.Intersections < 0 {
		report.Intersections = 0
	}
	report.SplitVertices = report.Intersections

	// Collinear cleanup is purely cosmetic and preserves the exact geometry.
	simplified := make([][]v2, 0, len(loops))
	for _, ring := range loops {
		r, removed := simplifyCollinear(ring, eps)
		report.RemovedCollinear += removed
		simplified = append(simplified, r)
	}
	loops = simplified

	ops := toOps(loops)
	report.Operations = len(ops)
	report.OutputContours = len(loops)
	for _, ring := range loops {
		report.OutputVertices += len(ring)
	}
	if len(ops) > maxOps {
		report.NodeBudgetBlocked = true
		report.Reliability = ReliabilityReject
		report.Warnings = append(report.Warnings,
			fmt.Sprintf("candidate needs %d path operations, exceeding the %d operation admission limit", len(ops), maxOps))
		return Result{}, fmt.Errorf("fill-rule conversion exceeds node budget for shape %q (%d > %d operations)", shape.ID, len(ops), maxOps)
	}

	// Reliability evidence: contacts, channel widths and curve flattening.
	report.CoincidentContacts = countNotes(ar.notes, "coincident-or-touching geometry within tolerance")
	report.Warnings = append(report.Warnings, ar.notes...)
	channel := narrowestChannel(ar)
	report.MinChannelLocal = channel
	report.MinChannelDevice = channel * matrixScale(shape.Transform)
	if report.MinChannelDevice > 0 && report.MinChannelDevice < 0.25 {
		report.Approximation = true
		report.Warnings = append(report.Warnings, fmt.Sprintf(
			"narrow channel %.4f device units is below 0.25; coverage near the channel is approximate", report.MinChannelDevice))
	}
	for _, o := range shape.Path {
		if o.Kind == "Q" || o.Kind == "C" {
			report.Warnings = append(report.Warnings, "curves are re-emitted at the shared 16/24-segment flatten contract; no curve verbs are synthesised")
			report.Approximation = true
			break
		}
	}

	// Build the candidate document: only the targeted shape changes; brush,
	// transform, layer clip, mask and every other shape are untouched.
	candidate := shape
	candidate.FillRule = targetRule
	candidate.Path = ops
	doc, err := ReplaceShape(document, layerID, shape.ID, candidate)
	if err != nil {
		return Result{}, err
	}

	// The candidate must pass the same compilation admission as any HTTP or
	// recovery input; otherwise it could never be rendered or persisted.
	if _, err := validate.Compile(*doc); err != nil {
		report.Reliability = ReliabilityReject
		report.Warnings = append(report.Warnings, "candidate rejected by compilation admission: "+err.Error())
		return Result{}, fmt.Errorf("converted shape fails admission: %w", err)
	}
	report.CandidateCompiles = true

	// Equivalence evidence 1: signed fill area before and after.
	report.SourceArea = orientedFillArea(source, shape.FillRule, eps)
	candidatePath := geom.Flatten(ops)
	report.CandidateArea = orientedFillArea(candidatePath, targetRule, eps)
	report.AreaDelta = math.Abs(report.CandidateArea - report.SourceArea)
	report.AreaTolerance = math.Max(eps*eps, math.Abs(report.SourceArea)*1e-9)
	if report.AreaDelta > report.AreaTolerance {
		report.Approximation = true
		report.Warnings = append(report.Warnings, fmt.Sprintf(
			"fill area delta %.3g exceeds analytic tolerance %.3g", report.AreaDelta, report.AreaTolerance))
	}

	// Equivalence evidence 2: deterministic point-in-region sampling using the
	// renderer's own Contains predicate for both rules.
	report.GridPoints, report.GridMismatches, report.GridOnEdgeSkipped = gridCheck(source, candidatePath, shape.FillRule, targetRule, 64, eps)
	if report.GridMismatches > 0 {
		report.Reliability = ReliabilityReject
		report.Warnings = append(report.Warnings, fmt.Sprintf(
			"%d sampled points change visibility; conversion is not region-equivalent", report.GridMismatches))
		return Result{}, fmt.Errorf("fill-rule conversion failed region equivalence for shape %q", shape.ID)
	}

	switch {
	case report.CoincidentContacts > 0 || report.Approximation:
		report.Reliability = ReliabilityApprox
	default:
		report.Reliability = ReliabilityExact
	}
	return Result{Document: *doc, FillRule: targetRule, Report: report}, nil
}

func countNotes(notes []string, want string) int {
	n := 0
	for _, s := range notes {
		if s == want {
			n++
		}
	}
	return n
}

// ReplaceShape returns a copy of document in which the named shape inside the
// named layer is swapped. IDs, brushes, transforms and all other nodes remain
// byte-identical so local brush sampling and layer clip regions are preserved.
func ReplaceShape(document scene.Document, layerID, shapeID string, next scene.Shape) (*scene.Document, error) {
	out := document
	out.Layers = append([]scene.Layer(nil), document.Layers...)
	foundLayer := false
	for li, layer := range out.Layers {
		if layer.ID != layerID {
			continue
		}
		foundLayer = true
		shapes := append([]scene.Shape(nil), layer.Shapes...)
		foundShape := false
		for si, shape := range shapes {
			if shape.ID == shapeID {
				shapes[si] = next
				foundShape = true
				break
			}
		}
		if !foundShape {
			return nil, fmt.Errorf("shape %q not found in layer %q", shapeID, layerID)
		}
		layer.Shapes = shapes
		out.Layers[li] = layer
	}
	if !foundLayer {
		return nil, fmt.Errorf("layer %q not found", layerID)
	}
	return &out, nil
}

// Document locates the named shape and converts its fill rule. The source
// document is treated as immutable evidence and is never edited in place.
func Document(plateID, layerID, shapeID, targetRule string, document scene.Document) (Result, error) {
	for _, layer := range document.Layers {
		if layer.ID != layerID {
			continue
		}
		for _, shape := range layer.Shapes {
			if shape.ID == shapeID {
				return Shape(plateID, layerID, document, shape, targetRule)
			}
		}
		return Result{}, fmt.Errorf("shape %q not found in layer %q", shapeID, layerID)
	}
	return Result{}, fmt.Errorf("layer %q not found", layerID)
}
