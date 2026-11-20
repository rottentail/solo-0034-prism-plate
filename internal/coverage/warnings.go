package coverage

import (
	"context"
	"fmt"
	"prismplate/internal/geom"
	"prismplate/internal/paint"
	"prismplate/internal/scene"
)

// flipThreshold is the relative family-area disagreement between the primary
// 4x4 grid and an independent 1x1 grid above which the conclusion is flagged
// as sensitive to inspection resolution. It deliberately exceeds expected
// sampling noise so genuine thin-feature flips stand out.
const flipThreshold = 0.2

// coarseMeasure re-samples the scene on an independent pixel-centered grid of
// pitch 1.0 using the same canonical evaluator, returning per-family visible
// alpha area. It is the cross-resolution check behind resolution_flip.
func coarseMeasure(ctx context.Context, c scene.Compiled, cl *Classifier) []float64 {
	nf := len(cl.families)
	fam := make([]float64, nf)
	for iy := 0; iy < c.Document.Height; iy++ {
		if err := ctx.Err(); err != nil {
			return fam
		}
		for ix := 0; ix < c.Document.Width; ix++ {
			p := geom.Point{X: float64(ix) + 0.5, Y: float64(iy) + 0.5}
			dst, _ := paint.Evaluate(c.Document.Background, c.Layers, p)
			if dst.A < VisibleAlphaFloor {
				continue
			}
			if idx := cl.classify(straightColor(dst)); idx >= 0 {
				fam[idx] += dst.A
			}
		}
	}
	return fam
}

// resolutionWarnings compares the primary-grid family areas with the
// independent coarse grid. Presence flips and large relative disagreements are
// surfaced explicitly instead of letting the chosen resolution silently
// decide the answer.
func resolutionWarnings(ctx context.Context, c scene.Compiled, cl *Classifier, fine []float64) []Warning {
	coarse := coarseMeasure(ctx, c, cl)
	var out []Warning
	for f := range cl.families {
		hi, lo := fine[f], coarse[f]
		if lo > hi {
			hi, lo = lo, hi
		}
		switch {
		case (fine[f] > 0) != (coarse[f] > 0):
			out = append(out, Warning{
				Code:   WarnResolutionFlip,
				Family: cl.families[f].Name,
				Detail: fmt.Sprintf("family presence flips with inspection grid: %.4f area at pitch %.2f versus %.4f at pitch %.2f", fine[f], SampleStep, coarse[f], coarseStep),
			})
		case hi > 0 && (hi-lo)/hi >= flipThreshold:
			out = append(out, Warning{
				Code:     WarnResolutionFlip,
				Family:   cl.families[f].Name,
				Detail:   fmt.Sprintf("family area moves %.1f%% between sampling grids (pitch %.2f vs %.2f); thin or cutout geometry dominates", 100*(hi-lo)/hi, SampleStep, coarseStep),
				Strength: (hi - lo) / hi,
			})
		}
	}
	return out
}

// tinyFeatureWarnings flags boundary pixels that form a connected component
// smaller than the 8x8 localization block: those are fine cutouts and
// hairlines whose verdict can move when sampling resolution changes. The flag
// is attached without altering any area number.
func tinyFeatureWarnings(g *grid) []Warning {
	const block = 8
	boundary := make([][]bool, g.h)
	for y := range boundary {
		boundary[y] = make([]bool, g.w)
	}
	for y := 0; y < g.h; y++ {
		for x := 0; x < g.w; x++ {
			vis := g.vis[y][x]
			if vis == 0 {
				continue
			}
			var assigned float64
			for f := 0; f < g.nf; f++ {
				assigned += g.famW[y][x*g.nf+f]
			}
			if frac := assigned / vis; frac > 1e-12 && frac < 0.999999 {
				boundary[y][x] = true
			}
		}
	}
	labels := labelComponents(boundary)
	counts := map[int]int{}
	for _, row := range labels {
		for _, v := range row {
			if v != 0 {
				counts[v]++
			}
		}
	}
	var warns []Warning
	for id, n := range counts {
		if n >= block*block {
			continue
		}
		warns = append(warns, Warning{
			Code:     WarnTinyFeature,
			Detail:   fmt.Sprintf("connected boundary feature #%d of %d pixel(s) is smaller than the %dx8 localization block; its verdict is resolution-sensitive", id, n, block),
			Strength: float64(n),
		})
	}
	// Deterministic ordering by strength then first label id.
	for i := 1; i < len(warns); i++ {
		for j := i; j > 0 && (warns[j].Strength > warns[j-1].Strength || (warns[j].Strength == warns[j-1].Strength && warns[j].Detail < warns[j-1].Detail)); j-- {
			warns[j], warns[j-1] = warns[j-1], warns[j]
		}
	}
	if len(warns) > 12 {
		warns = warns[:12]
	}
	return warns
}

// labelComponents performs 4-connected component labeling on a boolean mask
// using an explicit stack so a fully-flagged 512x512 mask cannot recurse past
// the goroutine stack limit.
func labelComponents(mask [][]bool) [][]int {
	h := len(mask)
	if h == 0 {
		return nil
	}
	w := len(mask[0])
	labels := make([][]int, h)
	for y := range labels {
		labels[y] = make([]int, w)
	}
	type cell struct{ x, y int }
	next := 1
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			if !mask[y][x] || labels[y][x] != 0 {
				continue
			}
			stack := []cell{{x, y}}
			labels[y][x] = next
			for len(stack) > 0 {
				c := stack[len(stack)-1]
				stack = stack[:len(stack)-1]
				neighbors := [4]cell{{c.x + 1, c.y}, {c.x - 1, c.y}, {c.x, c.y + 1}, {c.x, c.y - 1}}
				for _, n := range neighbors {
					if n.x < 0 || n.y < 0 || n.x >= w || n.y >= h || !mask[n.y][n.x] || labels[n.y][n.x] != 0 {
						continue
					}
					labels[n.y][n.x] = next
					stack = append(stack, n)
				}
			}
			next++
		}
	}
	return labels
}
