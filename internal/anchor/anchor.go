// Package anchor converts linear gradients between object-anchored and
// canvas-anchored coordinate spaces while preserving the rendered color field.
//
// A linear gradient at point p is the stop ramp sampled at
//
//	t(p) = clamp( dot(p - from, to - from) / |to - from|^2 , 0, 1).
//
// t is an affine scalar function of the point. Affine coordinate changes keep
// affine scalar fields affine, so the field after an invertible transform M can
// be represented exactly by a new gradient axis built from the transformed
// linear form (explicitAxis). Merely moving the endpoints through M (naiveAxis)
// preserves the field only for similarity (conformal) transforms; under
// non-uniform scale the projected parameter changes and colors shift.
package anchor

import (
	"fmt"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

// Report explains how faithful the conversion was. Exact conversion keeps the
// scalar field identical for every invertible affine transform; the naive
// metrics quantify what a direct endpoint drag would have done instead.
type Report struct {
	LayerID          string  `json:"layer_id"`
	ShapeID          string  `json:"shape_id"`
	FromAnchor       string  `json:"from_anchor"`
	ToAnchor         string  `json:"to_anchor"`
	Exact            bool    `json:"exact_field_preserved"`
	NaiveExact       bool    `json:"naive_endpoint_move_exact"`
	Reflected        bool    `json:"transform_reflected"`
	ConformalRatio   float64 `json:"conformal_ratio"`
	NaiveMaxTError   float64 `json:"naive_max_t_error"`
	NaiveAxisDegrees float64 `json:"naive_axis_drift_degrees"`
	Note             string  `json:"note"`
}

type Result struct {
	Brush  scene.Brush
	Report Report
}

const (
	// similarityTol is the relative eigenvalue spread that still counts as a
	// similarity transform for reporting purposes.
	similarityTol = 1e-9
	// finiteBound mirrors validate.finite so converted axes cannot escape the
	// document admission contract.
	finiteBound = 1e6
	sampleGrid  = 16
)

func finite(p geom.Point) bool {
	for _, v := range []float64{p.X, p.Y} {
		if math.IsNaN(v) || math.IsInf(v, 0) || math.Abs(v) > finiteBound {
			return false
		}
	}
	return true
}

func transpose(m geom.Matrix) geom.Matrix {
	return geom.Matrix{m[0], m[2], m[1], m[3], m[4], m[5]}
}

func matVec(m geom.Matrix, v geom.Point) geom.Point {
	return geom.Point{X: m[0]*v.X + m[2]*v.Y, Y: m[1]*v.X + m[3]*v.Y}
}

func dot(a, b geom.Point) float64 { return a.X*b.X + a.Y*b.Y }

func sub(a, b geom.Point) geom.Point { return geom.Point{X: a.X - b.X, Y: a.Y - b.Y} }

// explicitAxis reconstructs endpoints in the new coordinate space whose scalar
// field equals the original. B maps old-space points into the new space:
// object->canvas uses the shape transform M, canvas->object uses its inverse.
//
// Given old axis (f0, w = to-from), t(x) = dot(x-f0, w) / |w|^2. With x = B q
// and u = B^-T w the identity dot(B a, b) = dot(a, B^T b) gives
//
//	t(q) = (dot(q, u) - dot(B f0, u)) / |w|^2.
//
// The new axis must keep the old denominator |w|^2, so set
// w' = u * |w|^2 / |u|^2 and f' = B f0. Then |w'|^2 = |w|^2 and
// dot(q-f', w') = dot(q, u) - dot(B f0, u): the projection parameter, its
// clamping and the stop ramp are all reproduced point for point.
func explicitAxis(from geom.Point, w geom.Point, b geom.Matrix) (geom.Point, geom.Point) {
	inv, ok := b.Inverse()
	if !ok {
		return geom.Point{}, geom.Point{}
	}
	u := matVec(transpose(inv), w)
	uu := dot(u, u)
	if uu == 0 {
		return geom.Point{}, geom.Point{}
	}
	fp := b.Apply(from)
	scale := dot(w, w) / uu
	wp := geom.Point{X: u.X * scale, Y: u.Y * scale}
	return fp, geom.Point{X: fp.X + wp.X, Y: fp.Y + wp.Y}
}

// naiveAxis moves endpoints through B, which is what direct endpoint editing
// (dragging from/to) does to a gradient under a transform.
func naiveAxis(from, to geom.Point, b geom.Matrix) (geom.Point, geom.Point) {
	return b.Apply(from), b.Apply(to)
}

// axisT evaluates the unclamped projection parameter of an axis.
func axisT(p, from, to geom.Point) float64 {
	w := sub(to, from)
	d := sub(p, from)
	l := dot(w, w)
	if l == 0 {
		return 0
	}
	return dot(d, w) / l
}

func clamp01(t float64) float64 { return math.Max(0, math.Min(1, t)) }

// localBounds returns the flattened shape bounding box in local coordinates.
func localBounds(ops []geom.Op) (min, max geom.Point) {
	path := geom.Flatten(ops)
	min = path[0][0]
	max = min
	for _, ring := range path {
		for _, p := range ring {
			min.X = math.Min(min.X, p.X)
			min.Y = math.Min(min.Y, p.Y)
			max.X = math.Max(max.X, p.X)
			max.Y = math.Max(max.Y, p.Y)
		}
	}
	return min, max
}

// matrixMetrics inspects B, the coordinate mapping of the conversion direction.
type matrixMetrics struct {
	reflected      bool
	conformalRatio float64
	naiveExact     bool
}

func inspect(b geom.Matrix, w geom.Point) matrixMetrics {
	a, c, d, e := b[0], b[2], b[1], b[3]
	det := a*e - c*d
	// Eigenvalues of the symmetric 2x2 matrix B^T B.
	m00, m01, m11 := a*a+d*d, a*c+d*e, c*c+e*e
	trace := m00 + m11
	disc := math.Sqrt(math.Max(0, (m00-m11)*(m00-m11)+4*m01*m01))
	lambdaMax := (trace + disc) / 2
	lambdaMin := (trace - disc) / 2
	ratio := math.Sqrt(lambdaMax / math.Max(lambdaMin, 1e-300))
	// A direct endpoint move is field-exact iff B^T B w is parallel to w
	// (the axis lands on an eigenvector), which includes every similarity.
	btbw := matVec(transpose(b), matVec(b, w))
	cross := w.X*btbw.Y - w.Y*btbw.X
	naiveExact := math.Abs(cross) <= similarityTol*math.Max(1, math.Hypot(w.X, w.Y)*math.Hypot(btbw.X, btbw.Y))
	return matrixMetrics{reflected: det < 0, conformalRatio: ratio, naiveExact: naiveExact}
}

func angleDegrees(u, v geom.Point) float64 {
	lu, lv := math.Hypot(u.X, u.Y), math.Hypot(v.X, v.Y)
	if lu == 0 || lv == 0 {
		return 0
	}
	c := (u.X*v.X + u.Y*v.Y) / (lu * lv)
	c = math.Max(-1, math.Min(1, c))
	return math.Acos(c) * 180 / math.Pi
}

// Convert switches one linear brush between object and canvas anchoring using
// the shape's local->canvas transform. The returned brush keeps every stop and
// color untouched; only the axis coordinate space changes.
func Convert(brush scene.Brush, transform geom.Matrix, pathOps []geom.Op, layerID, shapeID, target string) (Result, error) {
	if brush.Kind != "linear" {
		return Result{}, fmt.Errorf("anchor conversion requires a linear gradient brush")
	}
	from := brush.EffectiveAnchor()
	if target != scene.AnchorObject && target != scene.AnchorCanvas {
		return Result{}, fmt.Errorf("target anchor must be object or canvas")
	}
	if from == target {
		return Result{}, fmt.Errorf("brush is already %s-anchored", target)
	}
	if _, ok := transform.Inverse(); !ok {
		return Result{}, fmt.Errorf("cannot convert anchor under a singular transform")
	}

	// A direct endpoint move applies the point transform M; the exact
	// reconstruction needs the inverse transpose M^-T, so both the point
	// mapping M and the form mapping M^-1 are kept.
	pointMap := transform
	formMap := transform
	if from == scene.AnchorCanvas {
		inv, _ := transform.Inverse()
		pointMap, formMap = inv, inv
	}

	w := sub(brush.To, brush.From)
	exactFrom, exactTo := explicitAxis(brush.From, w, formMap)
	naiveFrom, naiveTo := naiveAxis(brush.From, brush.To, pointMap)
	if !finite(exactFrom) || !finite(exactTo) {
		return Result{}, fmt.Errorf("converted gradient axis exceeds finite coordinate bounds")
	}

	metrics := inspect(pointMap, w)

	// Compare the exact reconstruction against a direct endpoint move over the
	// region the shape actually occupies, in the new coordinate space.
	lo, hi := localBounds(pathOps)
	samplePoint := func(u, v float64) geom.Point {
		return geom.Point{X: lo.X + (hi.X-lo.X)*u, Y: lo.Y + (hi.Y-lo.Y)*v}
	}
	maxErr := 0.0
	for i := 0; i <= sampleGrid; i++ {
		for j := 0; j <= sampleGrid; j++ {
			old := samplePoint(float64(i)/sampleGrid, float64(j)/sampleGrid)
			q := pointMap.Apply(old)
			want := clamp01(axisT(old, brush.From, brush.To))
			got := clamp01(axisT(q, naiveFrom, naiveTo))
			if e := math.Abs(want - got); e > maxErr {
				maxErr = e
			}
		}
	}
	exactW := sub(exactTo, exactFrom)
	naiveW := sub(naiveTo, naiveFrom)
	drift := angleDegrees(exactW, naiveW)

	note := "exact scalar-field reconstruction; appearance preserved up to floating-point roundoff"
	if !metrics.naiveExact {
		note = "exact field preserved via linear-form reconstruction; a direct endpoint move would shift colors, see naive metrics"
	}
	out := brush
	out.From, out.To, out.Anchor = exactFrom, exactTo, target
	return Result{
		Brush: out,
		Report: Report{
			LayerID:          layerID,
			ShapeID:          shapeID,
			FromAnchor:       from,
			ToAnchor:         target,
			Exact:            true,
			NaiveExact:       metrics.naiveExact,
			Reflected:        metrics.reflected,
			ConformalRatio:   metrics.conformalRatio,
			NaiveMaxTError:   maxErr,
			NaiveAxisDegrees: drift,
			Note:             note,
		},
	}, nil
}
