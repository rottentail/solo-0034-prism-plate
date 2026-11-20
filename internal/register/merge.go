package register

import (
	"fmt"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

// Placement records where one foreign layer landed in the merged document.
type Placement struct {
	Source string `json:"source"`
	Placed string `json:"placed"`
}

// Merge places foreign layers on top of the local layers. The local canvas
// size and background win. Shape contours and gradient axes stay in
// shape-local coordinates with the fitted map composed into each shape
// transform; clips and masks live in canvas coordinates and are rewritten
// through the fitted map. Reflections are safe for both fill rules because
// the nonzero and evenodd containment tests are invariant under winding
// negation.
func Merge(local, foreign scene.Document, m geom.Matrix, foreignID string) (scene.Document, []Placement) {
	taken := map[string]bool{}
	for _, l := range local.Layers {
		taken[l.ID] = true
		for _, s := range l.Shapes {
			taken[s.ID] = true
		}
	}
	out := scene.Document{Width: local.Width, Height: local.Height, Background: local.Background, Layers: append([]scene.Layer(nil), local.Layers...)}
	placements := make([]Placement, 0, len(foreign.Layers))
	for _, l := range foreign.Layers {
		nl := scene.Layer{ID: uniquify(foreignID+"-"+l.ID, taken), Opacity: l.Opacity, Blend: l.Blend, Clip: remapOps(l.Clip, m), Mask: remapOps(l.Mask, m)}
		for _, s := range l.Shapes {
			ns := s
			ns.ID = uniquify(foreignID+"-"+s.ID, taken)
			ns.Transform = geom.Mul(m, s.Transform)
			nl.Shapes = append(nl.Shapes, ns)
		}
		placements = append(placements, Placement{Source: l.ID, Placed: nl.ID})
		out.Layers = append(out.Layers, nl)
	}
	return out, placements
}

// remapOps re-expresses a canvas-space contour through an affine map. Affine
// maps preserve Bézier curves, so transforming control points transforms the
// flattened geometry exactly.
func remapOps(ops []geom.Op, m geom.Matrix) []geom.Op {
	if len(ops) == 0 {
		return nil
	}
	out := make([]geom.Op, len(ops))
	for i, o := range ops {
		out[i].Kind = o.Kind
		if len(o.Points) > 0 {
			pts := make([]geom.Point, len(o.Points))
			for j, p := range o.Points {
				pts[j] = m.Apply(p)
			}
			out[i].Points = pts
		}
	}
	return out
}

// uniquify allocates deterministic collision-free node identifiers so a
// preview and its commit produce byte-identical documents.
func uniquify(base string, taken map[string]bool) string {
	cand := clipID(base)
	for i := 2; taken[cand]; i++ {
		cand = clipID(fmt.Sprintf("%s-%d", base, i))
	}
	taken[cand] = true
	return cand
}
func clipID(s string) string {
	if len(s) > 64 {
		return s[:64]
	}
	return s
}
