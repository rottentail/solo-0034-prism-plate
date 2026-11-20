// Package compare renders two immutable revisions into one shared coordinate
// frame and reports which local regions actually changed. Comparison is purely
// pixel based: object renames or equivalent path reorderings render
// identically and therefore raise nothing, while a transparent-gradient or
// tiny cutout change surfaces as its own region instead of being averaged
// away. Nothing is persisted; the compared revisions, artifacts and proofs
// stay untouched.
package compare

import (
	"bytes"
	"context"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/paint"
	"prismplate/internal/raster"
	"prismplate/internal/scene"
	"sort"
)

// NoiseFloor is the largest delta explainable by a single 8-bit rounding
// step. Deltas at or below it are quantization noise, not design changes.
// The metric works on premultiplied 8-bit channels, and premultiplication can
// only shrink a non-premultiplied 1-LSB wobble, so one output rounding step
// never exceeds 1/255 here.
const NoiseFloor = 1.0

const (
	maxRegions   = 64 // reported in detail; the rest is counted only
	maxSamples   = 8  // attribution sample points per region
	maxObjects   = 32 // object references per region
	heatSaturate = 96.0
)

// Side is one rendered revision under comparison.
type Side struct {
	Revision int
	Digest   string
	Compiled scene.Compiled
}

type Canvas struct {
	Width  int `json:"width"`
	Height int `json:"height"`
}

type Endpoint struct {
	Revision int    `json:"revision"`
	Digest   string `json:"scene_digest"`
	Canvas   Canvas `json:"canvas"`
}

// Framing explains the comparison coordinates and background semantics,
// which matter most when the two canvases differ in size.
type Framing struct {
	Origin            string  `json:"origin"`
	Width             int     `json:"width"`
	Height            int     `json:"height"`
	OutsideCanvas     string  `json:"outside_canvas"`
	Background        string  `json:"background"`
	Metric            string  `json:"metric"`
	NoiseFloor        float64 `json:"noise_floor"`
	NoiseFloorMeaning string  `json:"noise_floor_meaning"`
	Legend            string  `json:"legend"`
}

type Summary struct {
	TotalPixels      int     `json:"total_pixels"`
	ChangedPixels    int     `json:"changed_pixels"`
	NoisePixels      int     `json:"noise_pixels"`
	ChangedFraction  float64 `json:"changed_fraction"`
	MaxDelta         float64 `json:"max_delta"`
	Regions          int     `json:"regions"`
	RegionsTruncated int     `json:"regions_truncated"`
	Verdict          string  `json:"verdict"`
}

type Bounds struct {
	X      int `json:"x"`
	Y      int `json:"y"`
	Width  int `json:"width"`
	Height int `json:"height"`
}

type ObjectRef struct {
	Revision int    `json:"revision"`
	Layer    string `json:"layer"`
	Shape    string `json:"shape"`
}

// Region is one connected cluster of pixels whose delta exceeds the noise
// floor. Origin attributes the change: added/removed mean content exists on
// only one side, modified means both sides have content there, backdrop means
// only the canvas background (or canvas extent) differs.
type Region struct {
	Bounds           Bounds      `json:"bounds"`
	Pixels           int         `json:"pixels"`
	MaxDelta         float64     `json:"max_delta"`
	MeanDelta        float64     `json:"mean_delta"`
	Origin           string      `json:"origin"`
	Objects          []ObjectRef `json:"objects"`
	ObjectsTruncated bool        `json:"objects_truncated"`
}

type Report struct {
	Plate     string   `json:"plate"`
	Base      Endpoint `json:"base"`
	Candidate Endpoint `json:"candidate"`
	Framing   Framing  `json:"framing"`
	Summary   Summary  `json:"summary"`
	Regions   []Region `json:"regions"`
}

// premult returns premultiplied 8-bit channels (0..255) of an NRGBA pixel.
// Fully transparent pixels collapse to zero, so invisible RGB plays no role.
func premult(c color.NRGBA) (float64, float64, float64, float64) {
	a := float64(c.A)
	s := a / 255
	return float64(c.R) * s, float64(c.G) * s, float64(c.B) * s, a
}

func delta(a, b color.NRGBA) float64 {
	ar, ag, ab, aa := premult(a)
	br, bg, bb, ba := premult(b)
	d := math.Abs(ar - br)
	if v := math.Abs(ag - bg); v > d {
		d = v
	}
	if v := math.Abs(ab - bb); v > d {
		d = v
	}
	if v := math.Abs(aa - ba); v > d {
		d = v
	}
	return d
}

func at(img *image.NRGBA, x, y int) color.NRGBA {
	if x < 0 || y < 0 || x >= img.Rect.Dx() || y >= img.Rect.Dy() {
		return color.NRGBA{}
	}
	return img.NRGBAAt(x, y)
}

func backgroundPixel(d scene.Document) color.NRGBA {
	r, g, b, a := paint.From(d.Background).RGBA8()
	return color.NRGBA{R: r, G: g, B: b, A: a}
}

// present reports whether a side shows visible content at (x,y): inside its
// own canvas and beyond the noise floor from its own background.
func present(img *image.NRGBA, bg color.NRGBA, x, y int) bool {
	if x < 0 || y < 0 || x >= img.Rect.Dx() || y >= img.Rect.Dy() {
		return false
	}
	return delta(img.NRGBAAt(x, y), bg) > NoiseFloor
}

// covering lists shapes whose fill covers plate point p, honoring layer clip
// and mask exactly like the rasterizer. Points outside the canvas cover nothing.
func covering(c scene.Compiled, p geom.Point) []ObjectRef {
	d := c.Document
	if p.X < 0 || p.Y < 0 || p.X >= float64(d.Width) || p.Y >= float64(d.Height) {
		return nil
	}
	var out []ObjectRef
	for _, l := range c.Layers {
		if len(l.Clip) > 0 && !l.Clip.Contains(p, "nonzero") {
			continue
		}
		if len(l.Mask) > 0 && !l.Mask.Contains(p, "nonzero") {
			continue
		}
		for _, s := range l.Shapes {
			if s.Path.Contains(s.Inverse.Apply(p), s.Shape.FillRule) {
				out = append(out, ObjectRef{Layer: l.Layer.ID, Shape: s.Shape.ID})
			}
		}
	}
	return out
}

type region struct {
	pixels []int // flat indices into the comparison frame
	sum    float64
	max    float64
	minX   int
	minY   int
	maxX   int
	maxY   int
}

// Diff renders both sides, classifies every pixel of the shared frame, and
// returns the region report plus a viewable diff PNG. The comparison frame is
// the bounding union of both canvases anchored at the shared top-left origin;
// a pixel outside one side's own canvas is fully transparent on that side.
func Diff(ctx context.Context, plate string, base, cand Side) (Report, []byte, error) {
	rep := Report{Plate: plate, Regions: []Region{}}
	bd, cd := base.Compiled.Document, cand.Compiled.Document
	rep.Base = Endpoint{Revision: base.Revision, Digest: base.Digest, Canvas: Canvas{Width: bd.Width, Height: bd.Height}}
	rep.Candidate = Endpoint{Revision: cand.Revision, Digest: cand.Digest, Canvas: Canvas{Width: cd.Width, Height: cd.Height}}
	w, h := bd.Width, bd.Height
	if cd.Width > w {
		w = cd.Width
	}
	if cd.Height > h {
		h = cd.Height
	}
	rep.Framing = Framing{
		Origin:            "比较坐标即版面坐标：原点左上，1 单位 = 1 像素；两稿本画布按同一原点对齐，不做缩放或平移",
		Width:             w,
		Height:            h,
		OutsideCanvas:     "比较画布取两稿本画布的并集；超出某一稿本自身画布的像素在该侧按完全透明处理，表示该稿本在此处没有校样内容",
		Background:        "每个稿本的背景只铺满它自己的画布，不外溢到并集区域；差异图中透明区域衬白色仅供查看，不参与差异度量",
		Metric:            "预乘 8 位 RGBA 逐通道最大绝对差（0..255）；预乘使全透明像素的 RGB 不参与比较",
		NoiseFloor:        NoiseFloor,
		NoiseFloorMeaning: "不超过 1/255 的逐像素差异处于单次 8 位量化步长之内，判为量化噪声；只有超过该地板的连通像素簇才判为设计变化",
		Legend:            "差异图：淡化底图取候选稿外观（仅一侧存在的区域取该侧）；黄→红为设计变化，颜色越红差异越大；浅蓝为量化噪声级像素；品红框为报告区域",
	}

	baseImg, err := raster.Image(ctx, base.Compiled)
	if err != nil {
		return rep, nil, err
	}
	candImg, err := raster.Image(ctx, cand.Compiled)
	if err != nil {
		return rep, nil, err
	}

	total := w * h
	deltas := make([]float64, total)
	changed := make([]bool, total)
	sum := rep.Summary
	for y := 0; y < h; y++ {
		if err := ctx.Err(); err != nil {
			return rep, nil, err
		}
		for x := 0; x < w; x++ {
			d := delta(at(baseImg, x, y), at(candImg, x, y))
			i := y*w + x
			deltas[i] = d
			if d > NoiseFloor {
				changed[i] = true
				sum.ChangedPixels++
				if d > sum.MaxDelta {
					sum.MaxDelta = d
				}
			} else if d > 0 {
				sum.NoisePixels++
			}
		}
	}
	sum.TotalPixels = total
	sum.ChangedFraction = float64(sum.ChangedPixels) / float64(total)

	// Connected components (8-neighbourhood) over changed pixels: a tiny
	// cutout stays its own region and cannot be diluted by the whole canvas.
	visited := make([]bool, total)
	var regions []region
	for start := 0; start < total; start++ {
		if !changed[start] || visited[start] {
			continue
		}
		if err := ctx.Err(); err != nil {
			return rep, nil, err
		}
		visited[start] = true
		r := region{minX: w, minY: h, maxX: -1, maxY: -1}
		stack := []int{start}
		for len(stack) > 0 {
			i := stack[len(stack)-1]
			stack = stack[:len(stack)-1]
			r.pixels = append(r.pixels, i)
			d := deltas[i]
			r.sum += d
			if d > r.max {
				r.max = d
			}
			x, y := i%w, i/w
			if x < r.minX {
				r.minX = x
			}
			if x > r.maxX {
				r.maxX = x
			}
			if y < r.minY {
				r.minY = y
			}
			if y > r.maxY {
				r.maxY = y
			}
			for dy := -1; dy <= 1; dy++ {
				for dx := -1; dx <= 1; dx++ {
					nx, ny := x+dx, y+dy
					if nx < 0 || ny < 0 || nx >= w || ny >= h {
						continue
					}
					ni := ny*w + nx
					if changed[ni] && !visited[ni] {
						visited[ni] = true
						stack = append(stack, ni)
					}
				}
			}
		}
		regions = append(regions, r)
	}
	// Rank by total visible-change energy (pixels × mean delta = summed
	// delta); ranking only orders regions, it never hides one.
	sort.Slice(regions, func(a, b int) bool { return regions[a].sum > regions[b].sum })
	sum.Regions = len(regions)
	kept := regions
	if len(kept) > maxRegions {
		kept = kept[:maxRegions]
		sum.RegionsTruncated = len(regions) - maxRegions
	}

	baseBG, candBG := backgroundPixel(bd), backgroundPixel(cd)
	for _, r := range kept {
		if err := ctx.Err(); err != nil {
			return rep, nil, err
		}
		n := len(r.pixels)
		out := Region{
			Bounds:    Bounds{X: r.minX, Y: r.minY, Width: r.maxX - r.minX + 1, Height: r.maxY - r.minY + 1},
			Pixels:    n,
			MaxDelta:  math.Round(r.max*1000) / 1000,
			MeanDelta: math.Round(r.sum/float64(n)*1000) / 1000,
			Objects:   []ObjectRef{},
		}
		inBase, inCand := 0, 0
		for _, i := range r.pixels {
			x, y := i%w, i/w
			if present(baseImg, baseBG, x, y) {
				inBase++
			}
			if present(candImg, candBG, x, y) {
				inCand++
			}
		}
		a, b := inBase*2 > n, inCand*2 > n
		switch {
		case a && b:
			out.Origin = "modified"
		case a:
			out.Origin = "removed"
		case b:
			out.Origin = "added"
		default:
			out.Origin = "backdrop"
		}
		// Attribute the region to covering objects on both sides, sampled at
		// a bounded number of changed pixels.
		seen := map[ObjectRef]bool{}
		stride := (n + maxSamples - 1) / maxSamples
		for k := 0; k < n && len(out.Objects) < maxObjects; k += stride {
			i := r.pixels[k]
			p := geom.Point{X: float64(i%w) + 0.5, Y: float64(i/w) + 0.5}
			for _, side := range []struct {
				rev int
				c   scene.Compiled
			}{{base.Revision, base.Compiled}, {cand.Revision, cand.Compiled}} {
				for _, ref := range covering(side.c, p) {
					ref.Revision = side.rev
					if !seen[ref] {
						seen[ref] = true
						out.Objects = append(out.Objects, ref)
						if len(out.Objects) >= maxObjects {
							out.ObjectsTruncated = true
							break
						}
					}
				}
			}
		}
		sort.Slice(out.Objects, func(a, b int) bool {
			if out.Objects[a].Revision != out.Objects[b].Revision {
				return out.Objects[a].Revision < out.Objects[b].Revision
			}
			if out.Objects[a].Layer != out.Objects[b].Layer {
				return out.Objects[a].Layer < out.Objects[b].Layer
			}
			return out.Objects[a].Shape < out.Objects[b].Shape
		})
		rep.Regions = append(rep.Regions, out)
	}

	switch {
	case sum.ChangedPixels > 0:
		sum.Verdict = "design_changes"
	case sum.NoisePixels > 0:
		sum.Verdict = "quantization_noise_only"
	default:
		sum.Verdict = "identical"
	}
	rep.Summary = sum

	img, err := diffImage(ctx, baseImg, candImg, w, h, deltas, changed, kept)
	if err != nil {
		return rep, nil, err
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		return rep, nil, fmt.Errorf("encode diff image: %w", err)
	}
	return rep, buf.Bytes(), nil
}

// diffImage paints the candidate appearance washed out as context, overlays
// changed pixels in a yellow→red heat scale, tints quantization-level pixels
// light blue, and frames every reported region in magenta. Transparent areas
// sit on a white backdrop for viewing only; the metric never uses a backdrop.
func diffImage(ctx context.Context, baseImg, candImg *image.NRGBA, w, h int, deltas []float64, changed []bool, regions []region) (*image.NRGBA, error) {
	img := image.NewNRGBA(image.Rect(0, 0, w, h))
	mix := func(a, b, t float64) uint8 { return uint8(math.Round(a*(1-t) + b*t)) }
	for y := 0; y < h; y++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		for x := 0; x < w; x++ {
			c := at(candImg, x, y)
			if x >= candImg.Rect.Dx() || y >= candImg.Rect.Dy() {
				c = at(baseImg, x, y)
			}
			a := float64(c.A) / 255
			cr := float64(c.R)*a + 255*(1-a)
			cg := float64(c.G)*a + 255*(1-a)
			cb := float64(c.B)*a + 255*(1-a)
			px := color.NRGBA{R: mix(cr, 255, 0.45), G: mix(cg, 255, 0.45), B: mix(cb, 255, 0.45), A: 255}
			i := y*w + x
			if d := deltas[i]; d > NoiseFloor {
				t := math.Min(1, (d-NoiseFloor)/heatSaturate)
				px = color.NRGBA{R: mix(255, 224, t), G: mix(214, 16, t), B: mix(32, 16, t), A: 255}
			} else if d > 0 {
				px = color.NRGBA{R: mix(float64(px.R), 115, 0.55), G: mix(float64(px.G), 175, 0.55), B: mix(float64(px.B), 255, 0.55), A: 255}
			}
			img.SetNRGBA(x, y, px)
		}
	}
	magenta := color.NRGBA{R: 255, G: 0, B: 255, A: 255}
	for _, r := range regions {
		for x := r.minX; x <= r.maxX; x++ {
			img.SetNRGBA(x, r.minY, magenta)
			img.SetNRGBA(x, r.maxY, magenta)
		}
		for y := r.minY; y <= r.maxY; y++ {
			img.SetNRGBA(r.minX, y, magenta)
			img.SetNRGBA(r.maxX, y, magenta)
		}
	}
	return img, nil
}
