// Package register fits foreign artwork onto the local canvas from paired
// markers and re-expresses foreign layers in local coordinates.
package register

import (
	"fmt"
	"math"
	"prismplate/internal/geom"
)

// Pair marks one corresponding position: source in the foreign canvas,
// target in the local canvas.
type Pair struct {
	Source geom.Point `json:"source"`
	Target geom.Point `json:"target"`
}

// Residual is the leftover misalignment of one pair after fitting.
type Residual struct {
	Pair  int     `json:"pair"`
	DX    float64 `json:"dx"`
	DY    float64 `json:"dy"`
	Error float64 `json:"error"`
}

// Fit is the least-squares similarity estimate plus the evidence a caller
// needs before committing to it.
type Fit struct {
	Transform        geom.Matrix `json:"transform"`
	Scale            float64     `json:"scale"`
	RotationDegrees  float64     `json:"rotation_degrees"`
	Reflected        bool        `json:"reflected"`
	RMSError         float64     `json:"rms_error"`
	MaxError         float64     `json:"max_error"`
	Residuals        []Residual  `json:"residuals"`
	DegreesOfFreedom int         `json:"degrees_of_freedom"`
	Confidence       string      `json:"confidence"`
	Diagnostics      []string    `json:"diagnostics"`
}

const (
	minPairs = 3
	maxPairs = 64
	// Confidence grades compare RMS error against the local canvas diagonal.
	mediumResidual = 0.002
	lowResidual    = 0.01
	// collinearRatio flags marker spreads whose minor axis carries almost no
	// information, as the eigenvalue ratio of the source scatter matrix.
	collinearRatio = 1e-4
	// affineRatio gates the affine cross-check, which needs a two-dimensional
	// marker spread to be solvable at all.
	affineRatio = 1e-8
)

func finite(x float64) bool { return !math.IsNaN(x) && !math.IsInf(x, 0) && math.Abs(x) <= 1e6 }

func rss(pairs []Pair, m geom.Matrix) float64 {
	sum := 0.0
	for _, pr := range pairs {
		q := m.Apply(pr.Source)
		sum += (q.X-pr.Target.X)*(q.X-pr.Target.X) + (q.Y-pr.Target.Y)*(q.Y-pr.Target.Y)
	}
	return sum
}

// FitTransform estimates the similarity map (translation, rotation, uniform
// scale, and — when allowed — reflection) taking foreign canvas coordinates
// to local canvas coordinates. Measurement noise is absorbed by least
// squares; near-collinear marker spreads and residuals that no single plane
// transform can remove are surfaced through Confidence and Diagnostics
// instead of being silently folded into the map.
func FitTransform(pairs []Pair, allowReflection bool, canvasW, canvasH int) (Fit, error) {
	var fit Fit
	if len(pairs) < minPairs {
		return fit, fmt.Errorf("registration requires at least three marker pairs")
	}
	if len(pairs) > maxPairs {
		return fit, fmt.Errorf("registration accepts at most %d marker pairs", maxPairs)
	}
	for _, pr := range pairs {
		if !finite(pr.Source.X) || !finite(pr.Source.Y) || !finite(pr.Target.X) || !finite(pr.Target.Y) {
			return fit, fmt.Errorf("non-finite marker coordinate")
		}
	}
	n := float64(len(pairs))
	var sx, sy, tx, ty float64
	for _, pr := range pairs {
		sx += pr.Source.X
		sy += pr.Source.Y
		tx += pr.Target.X
		ty += pr.Target.Y
	}
	msx, msy, mtx, mty := sx/n, sy/n, tx/n, ty/n
	var sxx, sxy, syy float64
	var pa, pb, ra, rb float64
	for _, pr := range pairs {
		ux, uy := pr.Source.X-msx, pr.Source.Y-msy
		vx, vy := pr.Target.X-mtx, pr.Target.Y-mty
		sxx += ux * ux
		sxy += ux * uy
		syy += uy * uy
		pa += vx*ux + vy*uy
		pb += vy*ux - vx*uy
		ra += vx*ux - vy*uy
		rb += vy*ux + vx*uy
	}
	scatter := sxx + syy
	if scatter < 1e-12 {
		return fit, fmt.Errorf("source markers are coincident; scale and rotation are unobservable")
	}
	// Proper: target = c*source + d in complex form. Reflected: c*conj(source)+d.
	proper := geom.Matrix{pa / scatter, pb / scatter, -pb / scatter, pa / scatter, 0, 0}
	mirror := geom.Matrix{ra / scatter, rb / scatter, rb / scatter, -ra / scatter, 0, 0}
	proper[4] = mtx - (proper[0]*msx + proper[2]*msy)
	proper[5] = mty - (proper[1]*msx + proper[3]*msy)
	mirror[4] = mtx - (mirror[0]*msx + mirror[2]*msy)
	mirror[5] = mty - (mirror[1]*msx + mirror[3]*msy)
	rssProper, rssMirror := rss(pairs, proper), rss(pairs, mirror)
	chosen, reflected, best := proper, false, rssProper
	if allowReflection && rssMirror < rssProper {
		chosen, reflected, best = mirror, true, rssMirror
	}
	scale := math.Hypot(chosen[0], chosen[1])
	if scale < 1e-9 {
		return fit, fmt.Errorf("fitted scale collapses; target markers are effectively coincident")
	}
	fit.Transform = chosen
	fit.Scale = scale
	fit.RotationDegrees = math.Atan2(chosen[1], chosen[0]) * 180 / math.Pi
	fit.Reflected = reflected
	fit.DegreesOfFreedom = 2*len(pairs) - 4
	fit.RMSError = math.Sqrt(best / n)
	for i, pr := range pairs {
		q := chosen.Apply(pr.Source)
		dx, dy := q.X-pr.Target.X, q.Y-pr.Target.Y
		e := math.Hypot(dx, dy)
		fit.Residuals = append(fit.Residuals, Residual{Pair: i, DX: dx, DY: dy, Error: e})
		if e > fit.MaxError {
			fit.MaxError = e
		}
	}
	diagonal := math.Hypot(float64(canvasW), float64(canvasH))
	rel := fit.RMSError / diagonal
	grade := 0
	if rel > mediumResidual {
		grade = 1
	}
	if rel > lowResidual {
		grade = 2
	}
	// Marker spread eigenvalues expose a nearly collinear configuration: the
	// fit still solves, but placement perpendicular to the marker line rests
	// on very little observation, so confidence drops one grade.
	trace, det := sxx+syy, sxx*syy-sxy*sxy
	root := math.Sqrt(math.Max(0, trace*trace/4-det))
	ratio := (trace/2 - root) / (trace/2 + root)
	if ratio < collinearRatio {
		grade++
		fit.Diagnostics = append(fit.Diagnostics, "source markers are nearly collinear: placement perpendicular to the marker line rests on very little spread")
	}
	if grade > 2 {
		grade = 2
	}
	fit.Confidence = []string{"high", "medium", "low"}[grade]
	if len(pairs) == minPairs {
		fit.Diagnostics = append(fit.Diagnostics, "exactly three marker pairs: a mismeasured marker cannot be isolated from the fit")
	}
	if reflected {
		fit.Diagnostics = append(fit.Diagnostics, "reflected transform selected; confirm the mirroring is intentional")
	}
	if !allowReflection && rssMirror < 0.5*rssProper && rel > lowResidual {
		fit.Diagnostics = append(fit.Diagnostics, "residuals fall sharply when reflection is allowed; if the foreign artwork is mirrored, resubmit with allow_reflection")
	}
	// Affine cross-check: a full 6-DOF refit separates deviations a
	// similarity map cannot remove (anisotropic scale, shear) from deviations
	// no single plane transform can remove (perspective, local distortion).
	if ratio > affineRatio {
		d2 := sxx*syy - sxy*sxy
		var exx, exy, eyx, eyy float64
		for _, pr := range pairs {
			ux, uy := pr.Source.X-msx, pr.Source.Y-msy
			vx, vy := pr.Target.X-mtx, pr.Target.Y-mty
			exx += ux * vx
			exy += uy * vx
			eyx += ux * vy
			eyy += uy * vy
		}
		a := (exx*syy - exy*sxy) / d2
		c := (exy*sxx - exx*sxy) / d2
		b := (eyx*syy - eyy*sxy) / d2
		d := (eyy*sxx - eyx*sxy) / d2
		affine := geom.Matrix{a, b, c, d, mtx - a*msx - c*msy, mty - b*msx - d*msy}
		affineRMS := math.Sqrt(rss(pairs, affine) / n)
		if a*d-b*c > 0 && affineRMS < 0.5*fit.RMSError && (fit.RMSError-affineRMS)/diagonal > 0.001 {
			fit.Diagnostics = append(fit.Diagnostics, fmt.Sprintf("residuals carry anisotropic scale or shear (affine cross-check RMS %.4g vs similarity %.4g); no rotation plus uniform scale transform can remove them", affineRMS, fit.RMSError))
		}
		if affineRMS/diagonal > 0.005 {
			fit.Diagnostics = append(fit.Diagnostics, "residuals persist under a full affine fit: perspective or local distortion cannot be removed by any single plane transform")
		}
	} else {
		fit.Diagnostics = append(fit.Diagnostics, "affine cross-check unavailable: source markers do not span two dimensions")
	}
	if len(fit.Diagnostics) == 0 {
		fit.Diagnostics = append(fit.Diagnostics, "residuals consistent with marker noise only")
	}
	return fit, nil
}
