package correct

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/paint"
	"prismplate/internal/raster"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
)

// ProbeRequest selects one existing linear gradient and supplies canvas targets.
type ProbeRequest struct {
	LayerID string   `json:"layer_id"`
	ShapeID string   `json:"shape_id"`
	Targets []Target `json:"targets"`
}

// CommitRequest repeats the probe inputs plus the expected revision and the
// candidate mode the designer confirmed.
type CommitRequest struct {
	ExpectedRevision int      `json:"expected_revision"`
	LayerID          string   `json:"layer_id"`
	ShapeID          string   `json:"shape_id"`
	Mode             string   `json:"mode"`
	Targets          []Target `json:"targets"`
}

// findBrush locates the selected linear brush and its compiled coordinates.
func findBrush(c scene.Compiled, layerID, shapeID string) (int, int, scene.Brush, error) {
	for li, l := range c.Layers {
		if l.Layer.ID != layerID {
			continue
		}
		for si, s := range l.Shapes {
			if s.Shape.ID == shapeID {
				if s.Shape.Brush.Kind != "linear" {
					return 0, 0, scene.Brush{}, fmt.Errorf("selected shape brush is %q, not linear", s.Shape.Brush.Kind)
				}
				return li, si, s.Shape.Brush, nil
			}
		}
		return 0, 0, scene.Brush{}, fmt.Errorf("shape %q not found in layer %q", shapeID, layerID)
	}
	return 0, 0, scene.Brush{}, fmt.Errorf("layer %q not found", layerID)
}

// Solve runs the full stateless correction analysis against one revision.
func Solve(d scene.Document, req ProbeRequest) (Proposal, error) {
	if err := ValidateTargets(req.Targets); err != nil {
		return Proposal{}, err
	}
	c, err := validate.Compile(d)
	if err != nil {
		return Proposal{}, err
	}
	li, si, brush, err := findBrush(c, req.LayerID, req.ShapeID)
	if err != nil {
		return Proposal{}, err
	}
	fitted, rejected := locate(c, li, si, req.Targets)
	if len(fitted) == 0 {
		return Proposal{Rejected: rejected, UsableCount: 0, Candidates: []Candidate{}}, nil
	}
	conflicts := detectConflicts(fitted)

	origStops := brush.Stops
	origPos := stopPositions(origStops)
	origVars, _ := premulStops(origStops)

	colorVars := solveColors(fitted, origStops, origPos, origVars)
	posVars, movedPos := solvePositions(fitted, origStops)

	candColors, err := buildCandidate(c, li, si, brush, origStops, colorVars, origPos, fitted, conflicts, ModeColorsOnly, origPos)
	if err != nil {
		return Proposal{}, err
	}
	candMoved, err := buildCandidate(c, li, si, brush, origStops, posVars, movedPos, fitted, conflicts, ModeColorsPositions, origPos)
	if err != nil {
		return Proposal{}, err
	}

	candidates := []Candidate{candColors, candMoved}
	recommended := ModeColorsOnly
	note := "colors_only achieves the same fit while preserving every stop position"
	if candMoved.WeightedRMSE < candColors.WeightedRMSE-1e-5 {
		recommended = ModeColorsPositions
		note = "moving interior stops reduces weighted linear-light RMSE; the smaller-error candidate is recommended"
	}
	return Proposal{
		LayerID:       req.LayerID,
		ShapeID:       req.ShapeID,
		UsableCount:   len(fitted),
		Rejected:      rejected,
		Candidates:    candidates,
		Recommended:   recommended,
		SelectionNote: note,
	}, nil
}

// detectConflicts flags targets that cannot hold simultaneously: either
// identical canvas positions demanding different colors, or targets collapsing
// onto the same gradient parameter with different required brush colors.
func detectConflicts(fitted []fittedTarget) map[int]bool {
	flag := map[int]bool{}
	for i := 0; i < len(fitted); i++ {
		for j := i + 1; j < len(fitted); j++ {
			a, b := fitted[i], fitted[j]
			samePoint := math.Abs(a.canvas.X-b.canvas.X) < 1e-9 && math.Abs(a.canvas.Y-b.canvas.Y) < 1e-9
			sameT := math.Abs(a.brushT-b.brushT) < 1e-3
			if !samePoint && !sameT {
				continue
			}
			if brushColorDistance(a.brush, b.brush) > conflictLinearLevel {
				flag[a.index] = true
				flag[b.index] = true
			}
		}
	}
	return flag
}

func brushColorDistance(a, b paint.Pixel) float64 {
	ar, ag, ab := unpremul(a)
	br, bg, bb := unpremul(b)
	return math.Sqrt(((ar-br)*(ar-br) + (ag-bg)*(ag-bg) + (ab-bb)*(ab-bb)) / 3)
}

func unpremul(p paint.Pixel) (float64, float64, float64) {
	if p.A <= 0 {
		return 0, 0, 0
	}
	return p.R / p.A, p.G / p.A, p.B / p.A
}

// buildCandidate assembles one candidate, verifies it through the real PNG
// renderer and fills every per-point deviation field.
func buildCandidate(c scene.Compiled, li, si int, brush scene.Brush, origStops []scene.Stop, vars [][3]float64, pos []float64, fitted []fittedTarget, conflicts map[int]bool, mode string, origPos []float64) (Candidate, error) {
	newStops := buildStops(origStops, vars, pos)
	doc, err := replaceBrush(c.Document, li, si, newStops)
	if err != nil {
		return Candidate{}, err
	}
	compiled, err := validate.Compile(doc)
	if err != nil {
		return Candidate{}, fmt.Errorf("correction produced an invalid document: %w", err)
	}
	pngBytes, err := raster.Render(context.Background(), compiled)
	if err != nil {
		return Candidate{}, fmt.Errorf("correction render failed: %w", err)
	}
	img, err := decodePNG(pngBytes)
	if err != nil {
		return Candidate{}, err
	}

	model := newGradientModelWith(brush, newStops)
	devs := make([]PointDeviation, 0, len(fitted))
	sse, wsum := 0.0, 0.0
	maxRMSE, pngMax, pngSum := 0.0, 0, 0.0
	conflictList := []int{}
	for _, t := range fitted {
		// Analytic prediction through the full compositing chain.
		pred := predictVisible(compiled, li, si, t.canvas, model)
		wantSRGB := colorFromPixel(t.want)
		predSRGB := colorFromPixel(pred)
		lrmse := linearDistance(pred, t.want)
		sMean, sMax := srgbDistance(predSRGB, wantSRGB)
		// Authoritative PNG observation at the integer pixel under the point.
		pngColor := samplePNG(img, int(math.Floor(t.canvas.X)), int(math.Floor(t.canvas.Y)))
		pMean, pMax := png8Distance(pngColor, wantSRGB)
		isConflict := conflicts[t.index] || lrmse > conflictLinearLevel || pMax > conflictPNGLevel
		if isConflict {
			conflictList = append(conflictList, t.index)
		}
		w := math.Max(t.weight, 1e-4)
		sse += w * lrmse * lrmse
		wsum += w
		maxRMSE = math.Max(maxRMSE, lrmse)
		pngMax = max(pngMax, pMax)
		pngSum += pMean
		devs = append(devs, PointDeviation{
			Index: t.index, X: t.canvas.X, Y: t.canvas.Y, GradientT: round9(t.brushT),
			Target: wantSRGB, Predicted: predSRGB,
			LinearRMSE: round9(lrmse), SRGBMean: round9(sMean), SRGBMax: round9(sMax),
			PNG: pngColor, PNGMean: round9(pMean), PNGMax: pMax, Conflict: isConflict,
		})
	}
	return Candidate{
		Mode:            mode,
		Stops:           newStops,
		WeightedRMSE:    round9(math.Sqrt(sse / wsum)),
		MaxLinearRMSE:   round9(maxRMSE),
		Deviations:      devs,
		ConflictIndexes: conflictList,
		PNGMaxAbs8:      pngMax,
		PNGMeanAbs8:     round9(pngSum / float64(len(fitted))),
		PositionDrift:   round9(positionDrift(origPos, pos)),
	}, nil
}

func newGradientModelWith(brush scene.Brush, stops []scene.Stop) gradientModel {
	b := brush
	b.Stops = stops
	return newGradientModel(b)
}

// predictVisible recomposites the document with the corrected brush and returns
// the premultiplied visible pixel averaged over the renderer's four subsamples.
func predictVisible(c scene.Compiled, li, si int, canvas geom.Point, model gradientModel) paint.Pixel {
	var sum paint.Pixel
	for sy := 0; sy < 2; sy++ {
		for sx := 0; sx < 2; sx++ {
			p := geom.Point{X: canvas.X + (float64(sx)+0.5)/2, Y: canvas.Y + (float64(sy)+0.5)/2}
			sum = pixelAdd(sum, traceWithModel(c, p, li, si, model))
		}
	}
	return sum.Scale(0.25)
}

// traceWithModel mirrors traceChain but substitutes the candidate brush for the
// target shape so the prediction reflects the proposed gradient exactly.
func traceWithModel(c scene.Compiled, p geom.Point, li, si int, model gradientModel) paint.Pixel {
	dst := paint.From(c.Document.Background)
	for lix, l := range c.Layers {
		if len(l.Clip) > 0 && !l.Clip.Contains(p, "nonzero") {
			continue
		}
		if len(l.Mask) > 0 && !l.Mask.Contains(p, "nonzero") {
			continue
		}
		src := paint.Pixel{}
		for six, s := range l.Shapes {
			q := s.Inverse.Apply(p)
			if s.Path.Contains(q, s.Shape.FillRule) {
				brush := paint.Sample(s.Shape.Brush, q)
				if lix == li && six == si {
					brush = model.at(gradientParam(model.brush, q))
				}
				src = paint.Over(src, brush, "normal")
			}
		}
		dst = paint.Over(dst, src.Scale(l.Layer.Opacity), l.Layer.Blend)
	}
	return dst
}

func replaceBrush(d scene.Document, li, si int, stops []scene.Stop) (scene.Document, error) {
	out, err := cloneDocument(d)
	if err != nil {
		return scene.Document{}, err
	}
	b := out.Layers[li].Shapes[si].Brush
	b.Stops = stops
	out.Layers[li].Shapes[si].Brush = b
	return out, nil
}

func cloneDocument(d scene.Document) (scene.Document, error) {
	// JSON round-trip gives a deep copy of the value-only scene contract.
	var out scene.Document
	buf, err := json.Marshal(d)
	if err != nil {
		return scene.Document{}, fmt.Errorf("scene document must be json-encodable: %w", err)
	}
	if err := json.Unmarshal(buf, &out); err != nil {
		return scene.Document{}, fmt.Errorf("scene document must be json-decodable: %w", err)
	}
	return out, nil
}

func decodePNG(b []byte) (image.Image, error) {
	img, err := png.Decode(bytes.NewReader(b))
	if err != nil {
		return nil, fmt.Errorf("correction produced undecodable png: %w", err)
	}
	return img, nil
}

func samplePNG(img image.Image, x, y int) scene.Color {
	bounds := img.Bounds()
	if x < bounds.Min.X {
		x = bounds.Min.X
	}
	if y < bounds.Min.Y {
		y = bounds.Min.Y
	}
	if x >= bounds.Max.X {
		x = bounds.Max.X - 1
	}
	if y >= bounds.Max.Y {
		y = bounds.Max.Y - 1
	}
	// The canonical renderer writes NRGBA; compare in non-premultiplied sRGB so
	// partial-alpha pixels use the same color semantics as the request targets.
	nrgba := color.NRGBAModel.Convert(img.At(x, y)).(color.NRGBA)
	return scene.Color{R: float64(nrgba.R) / 255, G: float64(nrgba.G) / 255, B: float64(nrgba.B) / 255, A: float64(nrgba.A) / 255}
}

func colorFromPixel(p paint.Pixel) scene.Color { return srgbColor(p) }

func linearDistance(a, b paint.Pixel) float64 {
	ar, ag, ab := unpremul(a)
	br, bg, bb := unpremul(b)
	return math.Sqrt(((ar-br)*(ar-br) + (ag-bg)*(ag-bg) + (ab-bb)*(ab-bb)) / 3)
}

func srgbDistance(a, b scene.Color) (mean, max float64) {
	d := []float64{math.Abs(a.R - b.R), math.Abs(a.G - b.G), math.Abs(a.B - b.B)}
	for _, v := range d {
		mean += v
		if v > max {
			max = v
		}
	}
	return mean / 3, max
}

func png8Distance(observed, wanted scene.Color) (mean float64, max int) {
	d := []int{
		int(math.Abs(float64(level8(observed.R) - level8(wanted.R)))),
		int(math.Abs(float64(level8(observed.G) - level8(wanted.G)))),
		int(math.Abs(float64(level8(observed.B) - level8(wanted.B)))),
	}
	sum := 0
	for _, v := range d {
		sum += v
		if v > max {
			max = v
		}
	}
	return float64(sum) / 3, max
}

func level8(v float64) int {
	return int(math.Round(clamp01(v) * 255))
}
