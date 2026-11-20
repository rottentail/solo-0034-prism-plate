package coverage

import (
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

// geometricShapes reports the non-sampling polygon area of every compiled
// shape in both its local coordinate system and on the canvas. The canvas
// area applies the affine determinant but does not subtract overlap, holes
// caused by other layers, clipping or occlusion; it is the geometric basis
// the sampling report is cross-checked against, not a visible-area claim.
func geometricShapes(c scene.Compiled) []ShapeArea {
	out := make([]ShapeArea, 0)
	for _, l := range c.Layers {
		for _, s := range l.Shapes {
			det := affineDeterminant(s.Shape.Transform)
			local := s.Path.FillArea(s.Shape.FillRule)
			canvasPath := geom.TransformPath(s.Path, s.Shape.Transform)
			bounds, ok := canvasPath.Bounds()
			outside := false
			if ok {
				outside = bounds.MaxX <= 0 || bounds.MinX >= float64(c.Document.Width) ||
					bounds.MaxY <= 0 || bounds.MinY >= float64(c.Document.Height)
			}
			out = append(out, ShapeArea{
				LayerID:       l.Layer.ID,
				ShapeID:       s.Shape.ID,
				LocalArea:     local,
				CanvasArea:    math.Abs(local * det),
				FillRule:      s.Shape.FillRule,
				OutsideCanvas: outside,
			})
		}
	}
	return out
}

// affineDeterminant returns |det| of the 2x2 affine block; it maps local area
// units to canvas area units.
func affineDeterminant(m geom.Matrix) float64 {
	return math.Abs(m[0]*m[3] - m[1]*m[2])
}

// layerAreas joins per-layer gross footprint (fraction of each pixel whose
// sub-samples the layer source reaches, ignoring layers above it) with the
// net visible alpha-area left after the full compositing chain.
func layerAreas(g *grid, layers []scene.CompiledLayer, visible []float64) []LayerArea {
	out := make([]LayerArea, 0, len(layers))
	for i, l := range layers {
		out = append(out, LayerArea{
			ID:          l.Layer.ID,
			Blend:       l.Layer.Blend,
			GrossArea:   g.layerGross[i],
			VisibleArea: visible[i],
		})
	}
	return out
}
