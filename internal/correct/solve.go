package correct

import (
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/paint"
	"prismplate/internal/scene"
)

// Mode names for the two candidate families. Both keep axis, stop count and
// every stop alpha; "positions" additionally moves interior stop positions.
const (
	ModeColorsOnly      = "colors_only"
	ModeColorsPositions = "colors_and_positions"
)

// gradientModel evaluates a linear gradient in the same premultiplied
// linear-light space as paint.Sample. Alpha is interpolated identically to the
// renderer and remains structural data, never a fitting variable.
type gradientModel struct {
	brush scene.Brush
}

func newGradientModel(b scene.Brush) gradientModel { return gradientModel{brush: b} }

func (g gradientModel) at(t float64) paint.Pixel {
	return paint.Sample(g.brush, geom.Point{X: g.brush.From.X + t*(g.brush.To.X-g.brush.From.X), Y: g.brush.From.Y + t*(g.brush.To.Y-g.brush.From.Y)})
}

// basis returns, for parameter t, the two bracketing stops and the local
// interpolation fraction under an explicit stop-position vector.
func basis(stops []scene.Stop, pos []float64, t float64) (int, int, float64) {
	if t <= pos[0] {
		return 0, 0, 0
	}
	last := len(pos) - 1
	if t >= pos[last] {
		return last, last, 0
	}
	for i := 1; i <= last; i++ {
		if t <= pos[i] {
			return i - 1, i, (t - pos[i-1]) / (pos[i] - pos[i-1])
		}
	}
	return last, last, 0
}

// premulStops converts sRGB stop colors to the premultiplied linear-light
// variables the optimizer works in. Bounds keep every variable a legal color.
func premulStops(stops []scene.Stop) ([][3]float64, [][3]float64) {
	n := len(stops)
	x := make([][3]float64, n)
	hi := make([][3]float64, n)
	for i, s := range stops {
		p := paint.From(s.Color)
		x[i] = [3]float64{p.R, p.G, p.B}
		hi[i] = [3]float64{s.Color.A, s.Color.A, s.Color.A}
	}
	return x, hi
}

// solveColors fixes stop positions and fits stop premultiplied RGB against the
// required brush colors. The problem is a box-constrained convex quadratic per
// channel; Nesterov projected gradient descent gives one deterministic answer.
// A weak pull toward the original stops makes the minimum-norm solution unique
// when the targets leave degrees of freedom.
func solveColors(targets []fittedTarget, stops []scene.Stop, pos []float64, start [][3]float64) [][3]float64 {
	n := len(stops)
	orig, hi := premulStops(stops)
	// Normalize weights so the regularization scale is input-independent.
	wsum := 0.0
	for _, t := range targets {
		wsum += math.Max(t.weight, 1e-4)
	}
	if wsum == 0 {
		wsum = 1
	}
	const reg = 1e-6
	// Largest eigenvalue of A^T W A is bounded by 2*sum(w) (basis sums to 1).
	lipschitz := 2*wsum + reg
	step := 1.0 / lipschitz
	x := cloneVars(start)
	y := cloneVars(x)
	// Reuse scratch buffers so the many position-search trials do not thrash GC.
	grad := make([][3]float64, n)
	next := make([][3]float64, n)
	ny := make([][3]float64, n)
	for iter := 0; iter < 1200; iter++ {
		for i := range grad {
			for k := 0; k < 3; k++ {
				grad[i][k] = reg * (y[i][k] - orig[i][k])
			}
		}
		for _, t := range targets {
			a, b, u := basis(stops, pos, t.brushT)
			w := math.Max(t.weight, 1e-4)
			want := [3]float64{t.brush.R, t.brush.G, t.brush.B}
			for k := 0; k < 3; k++ {
				got := y[a][k]*(1-u) + y[b][k]*u
				d := got - want[k]
				grad[a][k] += w * d * (1 - u)
				grad[b][k] += w * d * u
			}
		}
		maxMove, gradNorm := 0.0, 0.0
		for i := range next {
			for k := 0; k < 3; k++ {
				v := y[i][k] - step*grad[i][k]
				if v < 0 {
					v = 0
				}
				if v > hi[i][k] {
					v = hi[i][k]
				}
				next[i][k] = v
				maxMove = math.Max(maxMove, math.Abs(v-x[i][k]))
				gradNorm = math.Max(gradNorm, math.Abs(grad[i][k]))
			}
		}
		beta := float64(iter) / (float64(iter) + 3)
		for i := range ny {
			for k := 0; k < 3; k++ {
				ny[i][k] = next[i][k] + beta*(next[i][k]-x[i][k])
			}
		}
		x, next = next, x
		y, ny = ny, y
		if maxMove < 1e-12 || (iter > 30 && gradNorm < 1e-10) {
			break
		}
	}
	return x
}

func cloneVars(x [][3]float64) [][3]float64 {
	out := make([][3]float64, len(x))
	copy(out, x)
	return out
}

// stopPositions extracts the position vector and builds a candidate with moved
// interior positions, enforcing the minimum-gap and 0..1 contract.
func stopPositions(stops []scene.Stop) []float64 {
	pos := make([]float64, len(stops))
	for i, s := range stops {
		pos[i] = s.At
	}
	return pos
}

// solvePositions runs deterministic coordinate-descent sweeps over a fine grid
// per interior stop. Each trial re-solves the convex color problem and keeps the
// lowest weighted SSE; ties retain the position closest to the original.
func solvePositions(targets []fittedTarget, stops []scene.Stop) ([][3]float64, []float64) {
	n := len(stops)
	origPos := stopPositions(stops)
	pos := append([]float64(nil), origPos...)
	origVars, _ := premulStops(stops)
	vars := solveColors(targets, stops, pos, origVars)
	bestSSE := weightedSSE(targets, stops, pos, vars)
	for sweep := 0; sweep < positionSweeps; sweep++ {
		moved := false
		for i := 1; i < n-1; i++ {
			lo := pos[i-1] + minStopGap
			hi := pos[i+1] - minStopGap
			if hi <= lo {
				continue
			}
			bestPos := pos[i]
			bestVars := vars
			for g := 0; g <= interiorGrid; g++ {
				trial := lo + (hi-lo)*float64(g)/interiorGrid
				if math.Abs(trial-pos[i]) < 1e-12 {
					continue
				}
				tp := append([]float64(nil), pos...)
				tp[i] = trial
				tv := solveColors(targets, stops, tp, vars)
				sse := weightedSSE(targets, stops, tp, tv)
				if sse < bestSSE-1e-14 {
					bestSSE = sse
					bestPos = trial
					bestVars = tv
				}
			}
			if bestPos != pos[i] {
				pos[i] = bestPos
				vars = bestVars
				moved = true
			}
		}
		if !moved {
			break
		}
	}
	return vars, pos
}

func weightedSSE(targets []fittedTarget, stops []scene.Stop, pos []float64, x [][3]float64) float64 {
	sse := 0.0
	for _, t := range targets {
		a, b, u := basis(stops, pos, t.brushT)
		for k := 0; k < 3; k++ {
			got := x[a][k]*(1-u) + x[b][k]*u
			var want float64
			switch k {
			case 0:
				want = t.brush.R
			case 1:
				want = t.brush.G
			default:
				want = t.brush.B
			}
			w := math.Max(t.weight, 1e-4)
			sse += w * (got - want) * (got - want)
		}
	}
	return sse
}

// buildStops renders optimizer variables back into the scene contract: same stop
// count, same alphas, clamped sRGB values and rounded positions for deterministic
// JSON and byte-stable artifacts.
func buildStops(orig []scene.Stop, x [][3]float64, pos []float64) []scene.Stop {
	out := make([]scene.Stop, len(orig))
	for i := range orig {
		a := orig[i].Color.A
		col := scene.Color{A: a}
		channels := [3]float64{}
		for k := 0; k < 3; k++ {
			v := x[i][k]
			if v < 0 {
				v = 0
			}
			if v > a {
				v = a
			}
			channels[k] = v
		}
		if a > 0 {
			col.R = round9(paint.SRGB(channels[0] / a))
			col.G = round9(paint.SRGB(channels[1] / a))
			col.B = round9(paint.SRGB(channels[2] / a))
		}
		out[i] = scene.Stop{At: round9(pos[i]), Color: col}
	}
	return out
}

func round9(v float64) float64 {
	return math.Round(v*1e9) / 1e9
}

// positionDrift measures how far interior stops moved, used in the tie-break.
func positionDrift(orig, pos []float64) float64 {
	d := 0.0
	for i := range orig {
		d = math.Max(d, math.Abs(orig[i]-pos[i]))
	}
	return d
}
