package correct

import (
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/paint"
	"prismplate/internal/scene"
)

// pointChain is the full compositing trace at one canvas subsample. It mirrors
// raster.Render exactly: background, per-layer clip/mask gating, normal-over
// shape accumulation, group opacity and the layer blend mode.
type pointChain struct {
	point             geom.Point
	layerVisible      []bool
	dstBeforeLayer    []paint.Pixel
	layerSrc          []paint.Pixel // shape composite before group opacity
	shapeBrush        []paint.Pixel
	shapeHit          []bool
	shapePrefixBefore []paint.Pixel // layer-internal src immediately before shape i
	final             paint.Pixel
	finalWithout      paint.Pixel // same trace with the target shape unpainted
}

func traceChain(c scene.Compiled, p geom.Point, targetLayer, targetShape int) pointChain {
	n := len(c.Layers)
	ch := pointChain{
		point:          p,
		layerVisible:   make([]bool, n),
		dstBeforeLayer: make([]paint.Pixel, n),
		layerSrc:       make([]paint.Pixel, n),
	}
	dst := paint.From(c.Document.Background)
	dstWithout := dst
	for li, l := range c.Layers {
		visible := true
		if len(l.Clip) > 0 && !l.Clip.Contains(p, "nonzero") {
			visible = false
		}
		if len(l.Mask) > 0 && !l.Mask.Contains(p, "nonzero") {
			visible = false
		}
		ch.layerVisible[li] = visible
		ch.dstBeforeLayer[li] = dst
		if !visible {
			continue
		}
		src := paint.Pixel{}
		srcWithout := paint.Pixel{}
		for si, s := range l.Shapes {
			q := s.Inverse.Apply(p)
			hit := s.Path.Contains(q, s.Shape.FillRule)
			var brush paint.Pixel
			if hit {
				brush = paint.Sample(s.Shape.Brush, q)
			}
			ch.shapeBrush = append(ch.shapeBrush, brush)
			ch.shapeHit = append(ch.shapeHit, hit)
			ch.shapePrefixBefore = append(ch.shapePrefixBefore, src)
			if hit {
				src = paint.Over(src, brush, "normal")
				if li == targetLayer && si == targetShape {
					// The counterfactual skips the target shape entirely.
					continue
				}
				srcWithout = paint.Over(srcWithout, brush, "normal")
			}
		}
		ch.layerSrc[li] = src
		dst = paint.Over(dst, src.Scale(l.Layer.Opacity), l.Layer.Blend)
		dstWithout = paint.Over(dstWithout, srcWithout.Scale(l.Layer.Opacity), l.Layer.Blend)
	}
	ch.final = dst
	ch.finalWithout = dstWithout
	return ch
}

// shapeOffset maps (layer, shape) to the flat shape-array index inside a chain.
func shapeOffset(c scene.Compiled, layerIndex, shapeIndex int) int {
	off := 0
	for li := 0; li < layerIndex; li++ {
		off += len(c.Layers[li].Shapes)
	}
	return off + shapeIndex
}

// influence returns how strongly the target brush moves the final pixel at this
// subsample, as the maximum premultiplied linear-light channel delta. A target
// whose brush cannot change what is on canvas (hole, mask, full occlusion) has
// zero influence and is rejected instead of producing a vacuous constraint.
func (ch pointChain) influence() float64 {
	d := math.Abs(ch.final.R - ch.finalWithout.R)
	d = math.Max(d, math.Abs(ch.final.G-ch.finalWithout.G))
	d = math.Max(d, math.Abs(ch.final.B-ch.finalWithout.B))
	return d
}

// unOver inverts paint.Over. All pixels are premultiplied linear light and
// srcAlpha is the structural coverage taken from the real chain (alpha is never
// fitted, only RGB). ok is false for an out-of-gamut or unreachable request.
func unOver(dst, want paint.Pixel, mode string, srcAlpha float64) (paint.Pixel, bool) {
	if srcAlpha <= 0 {
		return paint.Pixel{}, false
	}
	sa := srcAlpha
	if mode == "normal" {
		// want = src + dst*(1-sa)
		fa := 1 - sa
		out := paint.Pixel{R: want.R - dst.R*fa, G: want.G - dst.G*fa, B: want.B - dst.B*fa, A: sa}
		return out, inPremulGamut(out)
	}
	if dst.A <= 0 {
		out := paint.Pixel{R: want.R, G: want.G, B: want.B, A: sa}
		return out, inPremulGamut(out)
	}
	da := dst.A
	// Solve for the premultiplied source channel s directly. The renderer's
	// blend branch reduces to w = d + s*k with a per-channel factor:
	//   multiply: k = 1-da+d ;  screen: k = 1-d   (d, s and w are premultiplied).
	inv := func(w, dPremul float64) (float64, bool) {
		var k float64
		if mode == "multiply" {
			k = 1 - da + dPremul
		} else {
			k = 1 - dPremul
		}
		if k <= 1e-12 {
			// The layer cannot move this channel (e.g. screen over an opaque,
			// value-1 backdrop); want must already equal the backdrop.
			if math.Abs(w-dPremul) > 1e-9 {
				return 0, false
			}
			return 0, true
		}
		return (w - dPremul) / k, true
	}
	r, okR := inv(want.R, dst.R)
	g, okG := inv(want.G, dst.G)
	b, okB := inv(want.B, dst.B)
	if !okR || !okG || !okB {
		return paint.Pixel{}, false
	}
	out := paint.Pixel{R: r, G: g, B: b, A: sa}
	return out, inPremulGamut(out)
}

func inPremulGamut(p paint.Pixel) bool {
	if p.A < -gamutEpsilon || p.A > 1+gamutEpsilon {
		return false
	}
	for _, v := range []float64{p.R, p.G, p.B} {
		if v < -gamutEpsilon || v > p.A+gamutEpsilon {
			return false
		}
	}
	return true
}

// invertChain walks one trace backwards from the wanted final color to the
// premultiplied brush color required of the target shape.
func invertChain(c scene.Compiled, ch pointChain, want paint.Pixel, layerIndex, shapeIndex int) (paint.Pixel, bool) {
	// 1. Layers above the target, topmost first.
	for li := len(c.Layers) - 1; li > layerIndex; li-- {
		if !ch.layerVisible[li] || ch.layerSrc[li].A <= 0 {
			continue
		}
		alpha := ch.layerSrc[li].A * c.Layers[li].Layer.Opacity
		next, ok := unOver(ch.dstBeforeLayer[li], want, c.Layers[li].Layer.Blend, alpha)
		if !ok {
			return paint.Pixel{}, false
		}
		want = next
	}
	// 2. Target layer blend plus group opacity.
	layer := c.Layers[layerIndex]
	if layer.Layer.Opacity <= 0 {
		return paint.Pixel{}, false
	}
	alpha := ch.layerSrc[layerIndex].A * layer.Layer.Opacity
	next, ok := unOver(ch.dstBeforeLayer[layerIndex], want, layer.Layer.Blend, alpha)
	if !ok {
		return paint.Pixel{}, false
	}
	want = next.Scale(1 / layer.Layer.Opacity)
	// 3. Shapes after the target shape inside the same layer, outermost first.
	base := shapeOffset(c, layerIndex, shapeIndex)
	for si := len(layer.Shapes) - 1; si > shapeIndex; si-- {
		off := base + (si - shapeIndex)
		if !ch.shapeHit[off] || ch.shapeBrush[off].A <= 0 {
			continue
		}
		next, ok := unOver(ch.shapePrefixBefore[off], want, "normal", ch.shapeBrush[off].A)
		if !ok {
			return paint.Pixel{}, false
		}
		want = next
	}
	// 4. The target shape itself against the accumulated pre-shape pixel.
	targetOff := base
	if !ch.shapeHit[targetOff] || ch.shapeBrush[targetOff].A <= 0 {
		return paint.Pixel{}, false
	}
	return unOver(ch.shapePrefixBefore[targetOff], want, "normal", ch.shapeBrush[targetOff].A)
}

// fittedTarget is one canvas target after visibility analysis and inversion.
type fittedTarget struct {
	index     int
	canvas    geom.Point
	want      paint.Pixel // wanted premultiplied final color
	brush     paint.Pixel // required brush premultiplied color (subsample average)
	brushT    float64     // averaged gradient parameter
	weight    float64     // effective brush alpha (fitting weight)
	influence float64     // smallest subsample influence of the target brush
}

// locate runs visibility analysis and inversion for every target at the same
// four subsample offsets raster.Render uses, averaging the recovered brush
// colors and gradient parameters over the subsamples that actually hit paint.
func locate(c scene.Compiled, layerIndex, shapeIndex int, targets []Target) ([]fittedTarget, []RejectedTarget) {
	fitted := make([]fittedTarget, 0, len(targets))
	rejected := []RejectedTarget{}
	cs := c.Layers[layerIndex].Shapes[shapeIndex]
	for ti, t := range targets {
		if t.X < 0 || t.Y < 0 || t.X >= float64(c.Document.Width) || t.Y >= float64(c.Document.Height) {
			rejected = append(rejected, RejectedTarget{Index: ti, X: t.X, Y: t.Y, Reason: ReasonOutsideCanvas})
			continue
		}
		var sumBrush, sumWant paint.Pixel
		var sumT, minInfluence float64
		valid := 0
		reasons := map[string]bool{}
		minInfluence = 1
		for sy := 0; sy < 2; sy++ {
			for sx := 0; sx < 2; sx++ {
				p := geom.Point{X: t.X + (float64(sx)+0.5)/2, Y: t.Y + (float64(sy)+0.5)/2}
				ch := traceChain(c, p, layerIndex, shapeIndex)
				off := shapeOffset(c, layerIndex, shapeIndex)
				if !ch.layerVisible[layerIndex] {
					reasons[ReasonLayerMasked] = true
					continue
				}
				if !ch.shapeHit[off] {
					reasons[reasonShapeMiss(cs, cs.Inverse.Apply(p))] = true
					continue
				}
				if ch.shapeBrush[off].A <= 0 {
					reasons[ReasonTransparent] = true
					continue
				}
				inf := ch.influence()
				if inf < influenceFloor {
					reasons[ReasonOccluded] = true
					continue
				}
				want := paint.From(t.Color)
				brush, ok := invertChain(c, ch, want, layerIndex, shapeIndex)
				if !ok {
					reasons[classifyInversion(c, ch, layerIndex, shapeIndex)] = true
					continue
				}
				sumBrush = pixelAdd(sumBrush, brush)
				sumWant = pixelAdd(sumWant, want)
				local := cs.Inverse.Apply(p)
				sumT += gradientParam(cs.Shape.Brush, local)
				minInfluence = math.Min(minInfluence, inf)
				valid++
			}
		}
		if valid == 0 {
			rejected = append(rejected, RejectedTarget{Index: ti, X: t.X, Y: t.Y, Reason: dominantReason(reasons)})
			continue
		}
		n := float64(valid)
		fitted = append(fitted, fittedTarget{
			index:     ti,
			canvas:    geom.Point{X: t.X, Y: t.Y},
			want:      sumWant.Scale(1 / n),
			brush:     sumBrush.Scale(1 / n),
			brushT:    sumT / n,
			weight:    sumBrush.Scale(1 / n).A,
			influence: minInfluence,
		})
	}
	return fitted, rejected
}

func pixelAdd(acc, v paint.Pixel) paint.Pixel {
	acc.R += v.R
	acc.G += v.G
	acc.B += v.B
	acc.A += v.A
	return acc
}

// reasonShapeMiss distinguishes a true outside miss from a fill-rule hole.
func reasonShapeMiss(cs scene.CompiledShape, q geom.Point) string {
	for _, ring := range cs.Path {
		if ringContains(ring, q) {
			return ReasonHole
		}
	}
	return ReasonShapeMiss
}

func ringContains(ring geom.Contour, p geom.Point) bool {
	inside := false
	for i, a := range ring {
		b := ring[(i+1)%len(ring)]
		if (a.Y > p.Y) != (b.Y > p.Y) {
			x := a.X + (p.Y-a.Y)*(b.X-a.X)/(b.Y-a.Y)
			if x > p.X {
				inside = !inside
			}
		}
	}
	return inside
}

func classifyInversion(c scene.Compiled, ch pointChain, layerIndex, shapeIndex int) string {
	base := shapeOffset(c, layerIndex, shapeIndex)
	// Opaque paint above the target makes the wanted color unreachable through it.
	for si := shapeIndex + 1; si < len(c.Layers[layerIndex].Shapes); si++ {
		off := base + (si - shapeIndex)
		if ch.shapeHit[off] && ch.shapeBrush[off].A >= 1-gamutEpsilon {
			return ReasonOccluded
		}
	}
	for li := len(c.Layers) - 1; li > layerIndex; li-- {
		if ch.layerVisible[li] && ch.layerSrc[li].A*c.Layers[li].Layer.Opacity >= 1-gamutEpsilon {
			return ReasonOccluded
		}
	}
	if c.Layers[layerIndex].Layer.Blend != "normal" {
		return ReasonBlendGamut
	}
	for li := len(c.Layers) - 1; li > layerIndex; li-- {
		if ch.layerVisible[li] && ch.layerSrc[li].A > 0 && c.Layers[li].Layer.Blend != "normal" {
			return ReasonBlendGamut
		}
	}
	return ReasonBrushGamut
}

// dominantReason reports the most structural rejection when subsamples disagree.
func dominantReason(reasons map[string]bool) string {
	for _, r := range []string{
		ReasonOutsideCanvas, ReasonLayerMasked, ReasonOccluded,
		ReasonHole, ReasonShapeMiss, ReasonTransparent,
		ReasonBlendGamut, ReasonBrushGamut,
	} {
		if reasons[r] {
			return r
		}
	}
	return ReasonShapeMiss
}

func gradientParam(b scene.Brush, q geom.Point) float64 {
	dx, dy := b.To.X-b.From.X, b.To.Y-b.From.Y
	t := ((q.X-b.From.X)*dx + (q.Y-b.From.Y)*dy) / (dx*dx + dy*dy)
	if t < 0 {
		return 0
	}
	if t > 1 {
		return 1
	}
	return t
}
