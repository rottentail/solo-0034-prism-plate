package corridor

import (
	"fmt"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

// NodeDeviation states how far one drag intent was honoured by a candidate.
type NodeDeviation struct {
	ShapeID        string     `json:"shape_id"`
	From           geom.Point `json:"from"`
	To             geom.Point `json:"to"`
	Applied        geom.Point `json:"applied"`
	IntendedLength float64    `json:"intended_length"`
	AppliedLength  float64    `json:"applied_length"`
	// Ratio is AppliedLength/IntendedLength; zero-intent moves report 0.
	Ratio float64 `json:"ratio"`
}

// Candidate is one protected revision option at a fixed interpolation factor.
type Candidate struct {
	Alpha           float64         `json:"alpha"`
	Document        scene.Document  `json:"-"`
	Report          Report          `json:"report"`
	NodeDeviations  []NodeDeviation `json:"node_deviations"`
	MaxDeviationPct float64         `json:"max_deviation_pct"`
	MinDeviationPct float64         `json:"min_deviation_pct"`
	SceneDigest     string          `json:"scene_digest"`
}

// Proposal is the complete bounded answer to a protection request.
type Proposal struct {
	Feasible      bool        `json:"feasible"`
	BoundaryAlpha float64     `json:"boundary_alpha"`
	Baseline      Report      `json:"baseline"`
	Candidates    []Candidate `json:"candidates"`
	// Limit carries the full-intent channel report when the drag cannot be
	// honoured completely, naming the closing channel and limiting wall.
	Limit *Report `json:"limit,omitempty"`
}

// BaselineError means the original revision already violates its own markers,
// so no protected move can be offered and the original must be kept.
type BaselineError struct {
	Report Report
}

func (e *BaselineError) Error() string {
	return fmt.Sprintf("original revision fails channel protection: %s", limitingText(e.Report))
}

func limitingText(r Report) string {
	for _, ch := range r.Channels {
		if !ch.Passes {
			return fmt.Sprintf("channel %q clearance %.4f < required %.4f at wall %q",
				ch.ID, ch.MinClearance, ch.MinWidth/2+Tolerance, ch.Narrowest.Wall)
		}
	}
	return "no failing channel"
}

func cloneDocument(d scene.Document) scene.Document {
	out := d
	out.Layers = make([]scene.Layer, len(d.Layers))
	for li, l := range d.Layers {
		cp := l
		cp.Shapes = make([]scene.Shape, len(l.Shapes))
		for si, s := range l.Shapes {
			cs := s
			cs.Path = append([]geom.Op(nil), s.Path...)
			for oi := range cs.Path {
				cs.Path[oi].Points = append([]geom.Point(nil), s.Path[oi].Points...)
			}
			cp.Shapes[si] = cs
		}
		cp.Clip = append([]geom.Op(nil), l.Clip...)
		cp.Mask = append([]geom.Op(nil), l.Mask...)
		out.Layers[li] = cp
	}
	return out
}

type indexedMove struct {
	move Move
}

// resolveMoves confirms every drag intent hits a real path node in the source
// document and indexes replacements by shape id and local coordinate.
func resolveMoves(d scene.Document, moves []Move) (map[string]map[string]indexedMove, error) {
	index := map[string]map[string]indexedMove{}
	shapeExists := map[string]bool{}
	for _, l := range d.Layers {
		for _, s := range l.Shapes {
			shapeExists[s.ID] = true
			for _, op := range s.Path {
				for _, p := range op.Points {
					for _, m := range moves {
						if m.ShapeID != s.ID {
							continue
						}
						if math.Hypot(p.X-m.From.X, p.Y-m.From.Y) > 1e-6 {
							continue
						}
						if index[s.ID] == nil {
							index[s.ID] = map[string]indexedMove{}
						}
						index[s.ID][coordKey(m.From)] = indexedMove{move: m}
					}
				}
			}
		}
	}
	for _, m := range moves {
		if !shapeExists[m.ShapeID] {
			return nil, fmt.Errorf("move references unknown shape %q", m.ShapeID)
		}
		entry, ok := index[m.ShapeID][coordKey(m.From)]
		if !ok {
			return nil, fmt.Errorf("move on shape %q does not match any path node", m.ShapeID)
		}
		_ = entry
	}
	return index, nil
}

// applyMoves interpolates every marked node: alpha 0 is the original document,
// alpha 1 the full drag intent. Coincident nodes shared by verbs move together,
// keeping rings closed.
func applyMoves(d scene.Document, index map[string]map[string]indexedMove, alpha float64) scene.Document {
	out := cloneDocument(d)
	for li := range out.Layers {
		for si := range out.Layers[li].Shapes {
			s := &out.Layers[li].Shapes[si]
			replacements := index[s.ID]
			if replacements == nil {
				continue
			}
			for oi := range s.Path {
				for pi, p := range s.Path[oi].Points {
					if entry, ok := replacements[coordKey(p)]; ok {
						m := entry.move
						s.Path[oi].Points[pi] = geom.Point{
							X: m.From.X + alpha*(m.To.X-m.From.X),
							Y: m.From.Y + alpha*(m.To.Y-m.From.Y),
						}
					}
				}
			}
		}
	}
	return out
}

func deviations(moves []Move, alpha float64) []NodeDeviation {
	out := make([]NodeDeviation, len(moves))
	for i, m := range moves {
		applied := geom.Point{X: m.From.X + alpha*(m.To.X-m.From.X), Y: m.From.Y + alpha*(m.To.Y-m.From.Y)}
		intended := math.Hypot(m.To.X-m.From.X, m.To.Y-m.From.Y)
		actual := math.Hypot(applied.X-m.From.X, applied.Y-m.From.Y)
		ratio := 0.0
		if intended > 0 {
			ratio = actual / intended
		}
		out[i] = NodeDeviation{ShapeID: m.ShapeID, From: m.From, To: m.To, Applied: applied, IntendedLength: intended, AppliedLength: actual, Ratio: ratio}
	}
	return out
}

func deviationBounds(dev []NodeDeviation) (float64, float64) {
	maxPct, minPct := 0.0, math.Inf(1)
	honoured := false
	for _, d := range dev {
		if d.IntendedLength > 0 {
			honoured = true
			pct := d.Ratio * 100
			if pct > maxPct {
				maxPct = pct
			}
			if pct < minPct {
				minPct = pct
			}
		}
	}
	if !honoured {
		return 0, 0
	}
	return maxPct, minPct
}

// Solve evaluates the bounded alpha grid, refines the feasibility boundary by
// bisection and returns at most maxCandidates verified options plus the limit.
func Solve(d scene.Document, moves []Move, channels []Channel, digest func(scene.Document) string) (Proposal, error) {
	if err := validateRequest(moves, channels); err != nil {
		return Proposal{}, err
	}
	index, err := resolveMoves(d, moves)
	if err != nil {
		return Proposal{}, err
	}
	baseline, err := Check(d, channels)
	if err != nil {
		return Proposal{}, err
	}
	if !baseline.Passes {
		return Proposal{}, &BaselineError{Report: baseline}
	}

	evaluate := func(alpha float64) (scene.Document, Report, error) {
		cand := applyMoves(d, index, alpha)
		rep, err := Check(cand, channels)
		return cand, rep, err
	}
	grid := make([]float64, solveGridSteps+1)
	feasible := make([]bool, solveGridSteps+1)
	reps := make([]Report, solveGridSteps+1)
	for k := 0; k <= solveGridSteps; k++ {
		grid[k] = float64(k) / solveGridSteps
		_, reps[k], err = evaluate(grid[k])
		if err != nil {
			return Proposal{}, err
		}
		feasible[k] = reps[k].Passes
	}
	lastFeasible := 0
	for k := 0; k <= solveGridSteps; k++ {
		if feasible[k] {
			lastFeasible = k
		}
	}
	proposal := Proposal{Feasible: lastFeasible == solveGridSteps, Baseline: baseline}
	boundary := grid[lastFeasible]
	if !proposal.Feasible {
		lo, hi := grid[lastFeasible], grid[lastFeasible+1]
		for i := 0; i < bisectSteps; i++ {
			mid := (lo + hi) / 2
			_, rep, err := evaluate(mid)
			if err != nil {
				return Proposal{}, err
			}
			if rep.Passes {
				lo = mid
			} else {
				hi = mid
			}
		}
		boundary = lo
		fullIntent := reps[solveGridSteps]
		proposal.Limit = &fullIntent
	}
	proposal.BoundaryAlpha = boundary

	// Spread candidates evenly from the original (alpha 0) to the verified
	// feasibility boundary; every returned candidate is re-checked.
	for i := 0; i < maxCandidates; i++ {
		alpha := boundary * float64(i) / (maxCandidates - 1)
		cand, rep, err := evaluate(alpha)
		if err != nil {
			return Proposal{}, err
		}
		if !rep.Passes {
			return Proposal{}, fmt.Errorf("internal: candidate alpha %.6f failed re-verification", alpha)
		}
		dev := deviations(moves, alpha)
		maxPct, minPct := deviationBounds(dev)
		proposal.Candidates = append(proposal.Candidates, Candidate{
			Alpha:           alpha,
			Document:        cand,
			Report:          rep,
			NodeDeviations:  dev,
			MaxDeviationPct: maxPct,
			MinDeviationPct: minPct,
			SceneDigest:     digest(cand),
		})
	}
	return proposal, nil
}
