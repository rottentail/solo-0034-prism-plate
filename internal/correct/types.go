// Package correct turns designer canvas color samples into constrained linear
// gradient revisions. It inverts the same linear-light compositing chain that
// raster.Render uses, so shape transforms, layer opacity and blend modes are
// accounted for before any gradient fitting happens. Points landing in holes,
// behind masks/clips or fully covered by other paint are rejected instead of
// being treated as direct color pickers.
package correct

import (
	"fmt"
	"prismplate/internal/geom"
	"prismplate/internal/paint"
	"prismplate/internal/scene"
)

// Target is one canvas point with the final visible sRGB color the designer wants.
type Target struct {
	X     float64     `json:"x"`
	Y     float64     `json:"y"`
	Color scene.Color `json:"color"`
}

// Rejection reasons surfaced to the caller. They describe why a canvas point
// cannot drive a brush-level correction.
const (
	ReasonOutsideCanvas = "outside_canvas"
	ReasonLayerMasked   = "layer_masked_or_clipped"
	ReasonShapeMiss     = "outside_shape"
	ReasonHole          = "hole"
	ReasonTransparent   = "zero_coverage"
	ReasonOccluded      = "occluded"
	ReasonBlendGamut    = "unreachable_under_blend"
	ReasonBrushGamut    = "unreachable_brush_color"
)

// RejectedTarget explains why a target cannot participate in the solve.
type RejectedTarget struct {
	Index  int     `json:"index"`
	X      float64 `json:"x"`
	Y      float64 `json:"y"`
	Reason string  `json:"reason"`
	Detail string  `json:"detail,omitempty"`
}

// PointDeviation reports the prediction error at one usable target, measured
// both in the linear-light fitting space and against the authoritative PNG.
type PointDeviation struct {
	Index      int         `json:"index"`
	X          float64     `json:"x"`
	Y          float64     `json:"y"`
	GradientT  float64     `json:"gradient_t"`
	Target     scene.Color `json:"target"`
	Predicted  scene.Color `json:"predicted"`
	LinearRMSE float64     `json:"linear_rmse"`
	SRGBMean   float64     `json:"srgb_mean_abs"`
	SRGBMax    float64     `json:"srgb_max_abs"`
	PNG        scene.Color `json:"png_observed"`
	PNGMean    float64     `json:"png_mean_abs_8bit"`
	PNGMax     int         `json:"png_max_abs_8bit"`
	Conflict   bool        `json:"conflict,omitempty"`
}

// Candidate is one admissible re-coloring of the selected gradient. The axis,
// the stop count and every stop alpha are preserved by construction.
type Candidate struct {
	Mode            string           `json:"mode"`
	Stops           []scene.Stop     `json:"stops"`
	WeightedRMSE    float64          `json:"weighted_linear_rmse"`
	MaxLinearRMSE   float64          `json:"max_linear_rmse"`
	Deviations      []PointDeviation `json:"deviations"`
	ConflictIndexes []int            `json:"conflict_indexes,omitempty"`
	PNGMaxAbs8      int              `json:"png_max_abs_8bit"`
	PNGMeanAbs8     float64          `json:"png_mean_abs_8bit"`
	PositionDrift   float64          `json:"stop_position_drift"`
	Notes           string           `json:"notes,omitempty"`
}

// Proposal is the stateless answer of a probe request; nothing is persisted.
type Proposal struct {
	PlateID       string           `json:"plate_id"`
	Revision      int              `json:"revision"`
	LayerID       string           `json:"layer_id"`
	ShapeID       string           `json:"shape_id"`
	UsableCount   int              `json:"usable_targets"`
	Rejected      []RejectedTarget `json:"rejected_targets,omitempty"`
	Candidates    []Candidate      `json:"candidates"`
	Recommended   string           `json:"recommended_mode"`
	SelectionNote string           `json:"selection_note"`
}

// solverBound keeps the numeric work bounded regardless of request size.
const (
	MaxTargets          = 64
	minStopGap          = 1e-3
	interiorGrid        = 16
	positionSweeps      = 4
	conflictLinearLevel = 0.08
	conflictPNGLevel    = 12
	gamutEpsilon        = 1e-9
	// influenceFloor is the smallest premultiplied linear-light channel change a
	// target brush must produce at a subsample for that point to be a usable
	// color constraint. Below it the point is effectively hidden.
	influenceFloor = 1e-5
)

// locatedTarget is the internal representation after visibility analysis.
type locatedTarget struct {
	index  int
	canvas geom.Point
	want   paint.Pixel // premultiplied linear-light final visible color
	brushT float64     // gradient parameter at the shape-local point
	weight float64     // effective target alpha (fitting weight)
}

func srgbColor(p paint.Pixel) scene.Color {
	if p.A <= 0 {
		return scene.Color{R: 0, G: 0, B: 0, A: 0}
	}
	return scene.Color{
		R: paint.SRGB(p.R / p.A),
		G: paint.SRGB(p.G / p.A),
		B: paint.SRGB(p.B / p.A),
		A: clamp01(p.A),
	}
}

func clamp01(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

func finitePoint(x, y float64) bool {
	return x == x && y == y && x-x == 0 && y-y == 0
}

// ValidateTargets enforces the request contract shared by probe and commit.
func ValidateTargets(targets []Target) error {
	if len(targets) == 0 {
		return fmt.Errorf("at least one color target required")
	}
	if len(targets) > MaxTargets {
		return fmt.Errorf("at most %d color targets per correction", MaxTargets)
	}
	for i, t := range targets {
		if !finitePoint(t.X, t.Y) {
			return fmt.Errorf("target %d has non-finite coordinates", i)
		}
		c := t.Color
		for _, v := range []float64{c.R, c.G, c.B, c.A} {
			if v != v || v < 0 || v > 1 {
				return fmt.Errorf("target %d color must use 0..1 sRGB values", i)
			}
		}
	}
	return nil
}
