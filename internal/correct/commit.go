package correct

import (
	"fmt"
	"prismplate/internal/scene"
)

// Apply re-runs the full solve against the base document and returns the
// document carrying the confirmed candidate's stops. The server never trusts
// client-supplied colors: both probe and commit derive stops from the same
// deterministic solver, so identical inputs always yield an identical revision.
func Apply(d scene.Document, req CommitRequest) (scene.Document, Candidate, error) {
	if req.Mode != ModeColorsOnly && req.Mode != ModeColorsPositions {
		return scene.Document{}, Candidate{}, fmt.Errorf("mode must be %q or %q", ModeColorsOnly, ModeColorsPositions)
	}
	proposal, err := Solve(d, ProbeRequest{LayerID: req.LayerID, ShapeID: req.ShapeID, Targets: req.Targets})
	if err != nil {
		return scene.Document{}, Candidate{}, err
	}
	if proposal.UsableCount == 0 {
		return scene.Document{}, Candidate{}, fmt.Errorf("no usable targets; correction cannot be committed")
	}
	var chosen Candidate
	for _, cand := range proposal.Candidates {
		if cand.Mode == req.Mode {
			chosen = cand
			break
		}
	}
	out, err := cloneDocument(d)
	if err != nil {
		return scene.Document{}, Candidate{}, err
	}
	for li, l := range out.Layers {
		if l.ID != req.LayerID {
			continue
		}
		for si, s := range l.Shapes {
			if s.ID == req.ShapeID {
				out.Layers[li].Shapes[si].Brush.Stops = chosen.Stops
				return out, chosen, nil
			}
		}
	}
	return scene.Document{}, Candidate{}, fmt.Errorf("selected gradient disappeared during commit")
}
