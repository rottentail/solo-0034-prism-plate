// Package coverage measures how much visible canvas each requested color
// family actually occupies. Measurement shares the canonical paint evaluator
// with raster rendering, so occlusion, clipping, masks, low alpha and
// multiply/screen blending are attributed under the same imaging rules as the
// authoritative PNG rather than from nominal shape areas.
package coverage

import (
	"context"
	"fmt"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/paint"
	"prismplate/internal/scene"
)

// VisibleAlphaFloor is the premultiplied-alpha contribution below which a
// sample is treated as not visibly present. 1/255 matches one 8-bit PNG level,
// i.e. anything weaker cannot be seen in the authoritative proof.
const VisibleAlphaFloor = 1.0 / 255.0

// SampleStep is the sub-pixel sampling pitch of the primary grid. The
// canonical PNG renders 2x2 cover samples per pixel; coverage uses a 4x4
// grid (pitch 0.25 canvas units) so each pixel contributes sixteen votes and
// the reported error bound stays tight without exceeding the render budget.
const SampleStep = 0.25

// subsamples is the number of primary-grid samples along one pixel axis.
const subsamples = int(1 / SampleStep)

// coarseStep is the pitch of the independent lower-resolution re-sample used
// to detect conclusions that silently flip when the inspection grid changes.
const coarseStep = 1.0

// Warning codes surfaced in the report.
const (
	WarnResolutionFlip    = "resolution_flip"
	WarnTinyFeature       = "tiny_feature"
	WarnInvisibleGeometry = "invisible_geometry"
)

// CellRegion is one aggregated area of contribution, in canvas pixels.
type CellRegion struct {
	X      int     `json:"x"`
	Y      int     `json:"y"`
	Weight float64 `json:"weight"`
}

// FamilyStat is the measured coverage of one requested color family.
type FamilyStat struct {
	Name string `json:"name"`
	// Area is the alpha-weighted visible canvas area attributed to the family
	// (canvas units squared); AreaMin/AreaMax bracket it with the sampling
	// envelope derived from per-pixel agreement.
	Area             float64            `json:"area"`
	AreaMin          float64            `json:"area_min"`
	AreaMax          float64            `json:"area_max"`
	Fraction         float64            `json:"fraction"`
	FractionMin      float64            `json:"fraction_min"`
	FractionMax      float64            `json:"fraction_max"`
	Reference        scene.Color        `json:"reference"`
	MaxDeltaE        float64            `json:"max_delta_e"`
	BlendAttribution map[string]float64 `json:"blend_attribution"`
	// Regions holds the hottest 8x8-pixel blocks of this family, strongest
	// first, so a PNG viewer can be directed at the main contributors.
	Regions []CellRegion `json:"regions"`
}

// LayerArea is a layer's gross canvas-area footprint before occlusion is
// removed, and its net visible alpha-area after the full compositing chain.
type LayerArea struct {
	ID          string  `json:"id"`
	Blend       string  `json:"blend"`
	GrossArea   float64 `json:"gross_area"`
	VisibleArea float64 `json:"visible_area"`
}

// ShapeArea is the geometric (non-sampling) area of one compiled shape.
type ShapeArea struct {
	LayerID       string  `json:"layer_id"`
	ShapeID       string  `json:"shape_id"`
	LocalArea     float64 `json:"local_area"`
	CanvasArea    float64 `json:"canvas_area"`
	FillRule      string  `json:"fill_rule"`
	OutsideCanvas bool    `json:"outside_canvas"`
}

// Warning explains a confidence caveat about the measurement.
type Warning struct {
	Code     string  `json:"code"`
	Family   string  `json:"family,omitempty"`
	Detail   string  `json:"detail"`
	Strength float64 `json:"strength,omitempty"`
}

// Report is the explainable coverage result.
type Report struct {
	Basis           string       `json:"basis"`
	CanvasWidth     int          `json:"canvas_width"`
	CanvasHeight    int          `json:"canvas_height"`
	CanvasArea      float64      `json:"canvas_area"`
	SamplePitch     float64      `json:"sample_pitch"`
	SampleCount     int          `json:"sample_count"`
	VisibleArea     float64      `json:"visible_area"`
	ArtworkArea     float64      `json:"artwork_area"`
	BackgroundArea  float64      `json:"background_area"`
	UnmatchedArea   float64      `json:"unmatched_area"`
	Families        []FamilyStat `json:"families"`
	Layers          []LayerArea  `json:"layers"`
	GeometricShapes []ShapeArea  `json:"geometric_shapes"`
	Warnings        []Warning    `json:"warnings"`
}

type grid struct {
	w, h       int
	step       float64
	areaW      float64
	nf         int
	famW       [][]float64 // per-pixel, nf entries each: family alpha weight
	vis        [][]float64 // per-pixel visible alpha weight
	art        [][]float64 // per-pixel artwork (non-background) coverage weight
	layerGross []float64   // per-layer summed gross footprint
	blend      [][]map[string]float64
}

func newGrid(w, h int, step float64, nf, nlayers int) *grid {
	g := &grid{w: w, h: h, step: step, areaW: step * step, nf: nf, layerGross: make([]float64, nlayers)}
	g.famW = make([][]float64, h)
	g.vis = make([][]float64, h)
	g.art = make([][]float64, h)
	g.blend = make([][]map[string]float64, h)
	for y := 0; y < h; y++ {
		g.famW[y] = make([]float64, w*nf)
		g.vis[y] = make([]float64, w)
		g.art[y] = make([]float64, w)
		g.blend[y] = make([]map[string]float64, w)
	}
	return g
}

// Measure runs the bounded sampling measurement over a compiled scene. The
// same evaluate call feeds both the rendered color and the attribution
// weights, so the numbers cannot disagree with the PNG imaging rules.
func Measure(ctx context.Context, c scene.Compiled, families []Family) (Report, error) {
	classifier, err := NewClassifier(families)
	if err != nil {
		return Report{}, err
	}
	d := c.Document
	nf := len(families)
	g := newGrid(d.Width, d.Height, SampleStep, nf, len(c.Layers))
	nx := int(math.Round(float64(d.Width) / SampleStep))
	ny := int(math.Round(float64(d.Height) / SampleStep))

	layerVisible := make([]float64, len(c.Layers))
	for iy := 0; iy < ny; iy++ {
		if err := ctx.Err(); err != nil {
			return Report{}, err
		}
		for ix := 0; ix < nx; ix++ {
			px := (float64(ix) + 0.5) * SampleStep
			py := (float64(iy) + 0.5) * SampleStep
			p := geom.Point{X: px, Y: py}
			// The sub-sample grid is 4 per pixel axis; integer division of
			// the grid index maps each sample back to its owning pixel.
			px0 := ix / subsamples
			py0 := iy / subsamples

			dst, layerSrc := paint.Evaluate(d.Background, c.Layers, p)

			// Gross layer footprint: a layer whose scaled source reaches the
			// sample counts once, independent of layers stacked above it.
			artHit := 0.0
			for li, lr := range layerSrc {
				if lr.Src.A >= VisibleAlphaFloor {
					artHit = 1
					layerVisible[li] += lr.Src.A * g.areaW
					g.layerGross[li] += g.areaW
				}
			}
			g.art[py0][px0] += artHit * g.areaW

			visibleWeight := 0.0
			if dst.A >= VisibleAlphaFloor {
				visibleWeight = dst.A
			}
			g.vis[py0][px0] += visibleWeight * g.areaW
			if visibleWeight == 0 {
				continue
			}

			idx := classifier.classify(straightColor(dst))
			if idx < 0 {
				continue
			}
			g.famW[py0][px0*nf+idx] += visibleWeight * g.areaW

			// Attribution of the observed pixel to its contributing layers by
			// premultiplied alpha mass. multiply/screen layers keep their own
			// identity: the weight is keyed by the layer's blend mode instead
			// of being folded into "normal".
			contrib := map[string]float64{}
			var mass float64
			for li, lr := range layerSrc {
				if lr.Src.A < VisibleAlphaFloor {
					continue
				}
				mass += lr.Src.A
				contrib[c.Layers[li].Layer.ID+"|"+lr.Blend] += lr.Src.A
			}
			if mass > 0 {
				if g.blend[py0][px0] == nil {
					g.blend[py0][px0] = map[string]float64{}
				}
				for k, w := range contrib {
					g.blend[py0][px0][k] += (w / mass) * visibleWeight * g.areaW
				}
			}
		}
	}

	rep := buildReport(d, g, classifier)
	rep.GeometricShapes = geometricShapes(c)
	rep.Layers = layerAreas(g, c.Layers, layerVisible)
	rep.Warnings = append(rep.Warnings, invisibleGeometryWarnings(c, g)...)
	fine := make([]float64, nf)
	for i, s := range rep.Families {
		fine[i] = s.Area
	}
	rep.Warnings = append(rep.Warnings, resolutionWarnings(ctx, c, classifier, fine)...)
	rep.Warnings = append(rep.Warnings, tinyFeatureWarnings(g)...)
	return rep, nil
}

// invisibleGeometryWarnings flags layers whose shapes have positive polygon
// area but never cross the visibility floor in any sample. The geometric
// footprint is still listed in geometric_shapes, so the distinction between
// "drawn" and "visible" is auditable rather than silently collapsed.
func invisibleGeometryWarnings(c scene.Compiled, g *grid) []Warning {
	var out []Warning
	for li, l := range c.Layers {
		var geo float64
		for _, s := range l.Shapes {
			geo += s.Path.FillArea(s.Shape.FillRule) * affineDeterminant(s.Shape.Transform)
		}
		if geo > 0 && g.layerGross[li] == 0 {
			out = append(out, Warning{
				Code:   WarnInvisibleGeometry,
				Family: l.Layer.ID,
				Detail: fmt.Sprintf("layer %q has geometric footprint %.4f but no sample reaches the visibility floor %.5f; it contributes zero visible area", l.Layer.ID, geo, VisibleAlphaFloor),
			})
		}
	}
	return out
}

func buildReport(d scene.Document, g *grid, cl *Classifier) Report {
	nf := len(cl.families)
	canvasArea := float64(d.Width * d.Height)
	rep := Report{
		Basis:        "supersampled_coverage",
		CanvasWidth:  d.Width,
		CanvasHeight: d.Height,
		CanvasArea:   canvasArea,
		SamplePitch:  g.step,
		SampleCount:  int(math.Round(float64(d.Width)/g.step)) * int(math.Round(float64(d.Height)/g.step)),
	}
	var visible, unmatched, artwork float64
	famTotal := make([]float64, nf)
	for y := 0; y < g.h; y++ {
		for x := 0; x < g.w; x++ {
			visible += g.vis[y][x]
			artwork += g.art[y][x]
			var assigned float64
			for f := 0; f < nf; f++ {
				v := g.famW[y][x*nf+f]
				famTotal[f] += v
				assigned += v
			}
			unmatched += math.Max(0, g.vis[y][x]-assigned)
		}
	}
	rep.VisibleArea = visible
	rep.UnmatchedArea = unmatched
	// Background area is canvas no artwork layer reaches, regardless of the
	// document background color; an opaque background still leaves real
	// uncovered area measurable rather than reporting zero.
	rep.BackgroundArea = math.Max(0, canvasArea-artwork)
	rep.ArtworkArea = artwork

	for f, fam := range cl.families {
		stat := FamilyStat{
			Name:             fam.Name,
			Reference:        fam.Reference,
			MaxDeltaE:        cl.tolerance(f),
			BlendAttribution: map[string]float64{},
			Regions:          topRegions(g, f),
		}
		areaMin, areaMax, blend := boundsAndBlend(g, f)
		stat.Area = famTotal[f]
		stat.AreaMin = areaMin
		stat.AreaMax = areaMax
		stat.Fraction = famTotal[f] / canvasArea
		stat.FractionMin = areaMin / canvasArea
		stat.FractionMax = areaMax / canvasArea
		var total float64
		for _, v := range blend {
			total += v
		}
		for k, v := range blend {
			if total > 0 {
				stat.BlendAttribution[k] = v / total
			}
		}
		rep.Families = append(rep.Families, stat)
	}
	return rep
}

// boundsAndBlend derives a rigorous sampling envelope per family. Pixels
// whose sixteen sub-samples all classify into the family are known covered;
// pixels that partially classify are boundary pixels whose whole area may
// swing either way, which yields min/max without assuming smooth geometry.
func boundsAndBlend(g *grid, f int) (float64, float64, map[string]float64) {
	var minA, maxA float64
	blend := map[string]float64{}
	for y := 0; y < g.h; y++ {
		for x := 0; x < g.w; x++ {
			v := g.famW[y][x*g.nf+f]
			vis := g.vis[y][x]
			if vis == 0 {
				continue
			}
			frac := v / vis
			if frac >= 0.999999 {
				minA += v
				maxA += v
			} else if frac > 0 {
				// Boundary pixel: at least partially covered, whole pixel is
				// the worst-case swing.
				maxA += 1.0
			}
			if v > 0 {
				for k, w := range g.blend[y][x] {
					blend[k] += w * (v / vis)
				}
			}
		}
	}
	return minA, maxA, blend
}

// topRegions aggregates family weight into 8x8 blocks and returns the eight
// strongest blocks with their canvas-space pixel coordinates.
func topRegions(g *grid, f int) []CellRegion {
	const block = 8
	type acc struct {
		bx, by, w float64
	}
	var accs []acc
	for by := 0; by < g.h; by += block {
		for bx := 0; bx < g.w; bx += block {
			var sum float64
			for y := by; y < minInt(g.h, by+block); y++ {
				for x := bx; x < minInt(g.w, bx+block); x++ {
					sum += g.famW[y][x*g.nf+f]
				}
			}
			if sum > 0 {
				accs = append(accs, acc{float64(bx), float64(by), sum})
			}
		}
	}
	for i := 1; i < len(accs); i++ {
		for j := i; j > 0 && (accs[j].w > accs[j-1].w || (accs[j].w == accs[j-1].w && (accs[j].bx < accs[j-1].bx || (accs[j].bx == accs[j-1].bx && accs[j].by < accs[j-1].by)))); j-- {
			accs[j], accs[j-1] = accs[j-1], accs[j]
		}
	}
	if len(accs) > 8 {
		accs = accs[:8]
	}
	out := make([]CellRegion, 0, len(accs))
	for _, a := range accs {
		out = append(out, CellRegion{X: int(a.bx), Y: int(a.by), Weight: a.w})
	}
	return out
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
