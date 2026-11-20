package sep

import (
	"bytes"
	"context"
	"image"
	"image/color"
	"image/png"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/paint"
	"prismplate/internal/repository"
	"prismplate/internal/scene"
	"sort"
)

// Plate is one rendered spot-ink coverage plate plus its transfer metadata.
type Plate struct {
	Ink            string  `json:"ink"`
	Name           string  `json:"name"`
	Format         string  `json:"format"`
	Width          int     `json:"width"`
	Height         int     `json:"height"`
	Digest         string  `json:"digest"`
	SamplesCovered int     `json:"samples_covered"`
	MeanCoverage   float64 `json:"mean_coverage"`
	Data           []byte  `json:"-"`
}

// Overlap records a pair of inks that meet on at least one supersample point.
type Overlap struct {
	TopInk      string  `json:"top_ink"`
	UnderInk    string  `json:"under_ink"`
	Samples     int     `json:"samples"`
	Overprinted bool    `json:"overprinted"`
	MeanTop     float64 `json:"mean_top_coverage"`
}

// Semantics is the explicit, bounded overprint contract of the separation.
type Semantics struct {
	Knockout      string `json:"knockout"`
	Overprint     string `json:"overprint"`
	CoverageBasis string `json:"coverage_basis"`
	PlateEncoding string `json:"plate_encoding"`
	BlendModel    string `json:"blend_model"`
	Unsupported   string `json:"unsupported"`
}

// InkRecord is the shared color definition handed to the screen shop.
type InkRecord struct {
	ID         string      `json:"id"`
	Name       string      `json:"name"`
	ProofColor scene.Color `json:"proof_color"`
	PrintOrder int         `json:"print_order"`
}

// Recomposite explains how plates folded back together compare with the
// reference composite PNG rendered from the same revision.
type Recomposite struct {
	ReferenceDigest   string `json:"reference_png_digest"`
	RecompositeDigest string `json:"recomposited_png_digest"`
	DifferentPixels   int    `json:"different_pixels"`
	TotalPixels       int    `json:"total_pixels"`
	OverprintPixels   int    `json:"overprint_pixels"`
	KnockoutSamples   int    `json:"knockout_samples"`
	OverprintSamples  int    `json:"overprint_samples"`
	// Identity holds when no overprinted ink exists and every spot film is
	// opaque: the knockout plates fold back into the exact reference bytes.
	Identity    bool   `json:"byte_identical_without_overprint"`
	Explanation string `json:"difference_explanation"`
}

// ArtifactRef links a manifest entry to its persisted, content-addressed artifact.
type ArtifactRef struct {
	Ink    string `json:"ink,omitempty"`
	Format string `json:"format"`
	ID     string `json:"artifact_id"`
	Digest string `json:"digest"`
}

// Manifest is the screen-proofing handover list for one separation run.
type Manifest struct {
	Schema        string        `json:"schema"`
	Plate         string        `json:"plate"`
	Revision      int           `json:"revision"`
	SceneDigest   string        `json:"scene_digest"`
	Width         int           `json:"width"`
	Height        int           `json:"height"`
	Semantics     Semantics     `json:"semantics"`
	Inks          []InkRecord   `json:"inks"`
	Plates        []Plate       `json:"plates"`
	Overlaps      []Overlap     `json:"overlaps"`
	Recomposite   Recomposite   `json:"recomposite"`
	Artifacts     []ArtifactRef `json:"artifacts"`
	ProcessShapes int           `json:"process_shapes"`
}

// Result is the complete, not-yet-persisted output of one separation run.
type Result struct {
	Manifest     Manifest
	Plates       []Plate
	Recomposited []byte
	Reference    []byte
}

type inkFilm struct {
	index     int
	alpha     float64
	overprint bool
}

// Render builds every coverage plate, the press-order recomposite and the
// handover manifest structure. referencePNG is the canonical composite of the
// same revision; its digest is embedded so differences stay traceable. The
// caller fills Manifest.Artifacts with storage ids before marshalling.
func Render(ctx context.Context, c scene.Compiled, plateID string, revision int, sceneDigest string, referencePNG []byte) (*Result, error) {
	d := c.Document
	order := inkOrder(d)
	n := len(order)
	indexOf := map[string]int{}
	for i, id := range order {
		indexOf[id] = i
	}

	totalSamples := d.Width * d.Height * 4
	cov := make([]float64, totalSamples*n)
	overprintMark := make([]bool, totalSamples*n)

	processShapes := 0
	knockoutSamples, overprintSamples := 0, 0

	for y := 0; y < d.Height; y++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		for x := 0; x < d.Width; x++ {
			for sy := 0; sy < 2; sy++ {
				for sx := 0; sx < 2; sx++ {
					sample := (y*d.Width+x)*4 + sy*2 + sx
					p := geom.Point{X: float64(x) + (float64(sx)+0.5)/2, Y: float64(y) + (float64(sy)+0.5)/2}
					films := collectFilms(c, p, indexOf)
					for _, f := range films {
						base := sample*n + f.index
						underlying := 0.0
						for k := 0; k < n; k++ {
							if k != f.index {
								underlying += cov[sample*n+k]
							}
						}
						if f.overprint {
							cov[base] = math.Min(1, cov[base]+f.alpha)
							overprintMark[base] = true
							if underlying > 0 {
								overprintSamples++
							}
						} else {
							// Knockout: this ink punches every underlying ink film.
							for k := 0; k < n; k++ {
								if k != f.index && cov[sample*n+k] > 0 {
									cov[sample*n+k] *= 1 - f.alpha
								}
							}
							cov[base] = math.Min(1, cov[base]+f.alpha*(1-cov[base]))
							if underlying > 0 {
								knockoutSamples++
							}
						}
					}
					if hasProcessFilm(c, p) {
						processShapes++
					}
				}
			}
		}
	}

	plates, err := encodePlates(d, order, cov)
	if err != nil {
		return nil, err
	}
	overlaps := collectOverlaps(order, cov, overprintMark, totalSamples)
	recomp, diffPixels, overprintPixels, identity, err := recomposite(d, order, cov, overprintMark, referencePNG)
	if err != nil {
		return nil, err
	}

	inks := make([]InkRecord, 0, n)
	for i, id := range order {
		sp := spotByID(d, id)
		inks = append(inks, InkRecord{ID: id, Name: sp.Name, ProofColor: sp.ProofColor, PrintOrder: i})
	}

	m := Manifest{
		Schema:      "prism-plate-separation-v1",
		Plate:       plateID,
		Revision:    revision,
		SceneDigest: sceneDigest,
		Width:       d.Width,
		Height:      d.Height,
		Semantics: Semantics{
			Knockout:      "later inks erase underlying spot coverage where they print; plates stay mutually exclusive at knockout boundaries",
			Overprint:     "overprint shapes keep underlying coverage; overlap films stack and are simulated by premultiplied linear-light multiply",
			CoverageBasis: "each plate is geometry-derived supersampled alpha (4 samples per pixel), not an RGB channel decomposition",
			PlateEncoding: "8-bit grayscale PNG, value 0..255 equals round(coverage*255), black (0) means no ink",
			BlendModel:    "sRGB proof colors are converted to linear light before multiply and back to sRGB on encode",
			Unsupported:   "process RGB shapes, gradient hue ramps and non-normal layer blends are excluded from plates; only brush alpha is carried for spot gradients",
		},
		Inks:          inks,
		Plates:        plates,
		Overlaps:      overlaps,
		Artifacts:     []ArtifactRef{},
		ProcessShapes: processShapes,
		Recomposite: Recomposite{
			ReferenceDigest:   repository.Hash(referencePNG),
			RecompositeDigest: repository.Hash(recomp),
			DifferentPixels:   diffPixels,
			TotalPixels:       d.Width * d.Height,
			OverprintPixels:   overprintPixels,
			KnockoutSamples:   knockoutSamples,
			OverprintSamples:  overprintSamples,
			Identity:          identity,
			Explanation:       diffExplanation(identity, overprintPixels, processShapes),
		},
	}
	return &Result{Manifest: m, Plates: plates, Recomposited: recomp, Reference: referencePNG}, nil
}

// collectFilms evaluates clip/mask/shape coverage at one supersample point and
// returns the spot films in document (press) order.
func collectFilms(c scene.Compiled, p geom.Point, indexOf map[string]int) []inkFilm {
	films := []inkFilm{}
	for _, l := range c.Layers {
		if len(l.Clip) > 0 && !l.Clip.Contains(p, "nonzero") {
			continue
		}
		if len(l.Mask) > 0 && !l.Mask.Contains(p, "nonzero") {
			continue
		}
		layerInk := map[string]inkFilm{}
		layerOrder := []string{}
		for _, s := range l.Shapes {
			q := s.Inverse.Apply(p)
			if !s.Path.Contains(q, s.Shape.FillRule) {
				continue
			}
			ink := s.Shape.Brush.Spot
			if ink == "" {
				continue
			}
			a := paint.SpotAlpha(s.Shape.Brush, q) * l.Layer.Opacity
			if a <= 0 {
				continue
			}
			op := s.Shape.Overprint || l.Layer.Overprint
			if prev, ok := layerInk[ink]; ok {
				prev.alpha = prev.alpha + a*(1-prev.alpha)
				prev.overprint = prev.overprint || op
				layerInk[ink] = prev
			} else {
				layerInk[ink] = inkFilm{index: indexOf[ink], alpha: a, overprint: op}
				layerOrder = append(layerOrder, ink)
			}
		}
		for _, ink := range layerOrder {
			films = append(films, layerInk[ink])
		}
	}
	return films
}

// hasProcessFilm reports whether a non-spot (process RGB) shape covers p.
func hasProcessFilm(c scene.Compiled, p geom.Point) bool {
	for _, l := range c.Layers {
		if len(l.Clip) > 0 && !l.Clip.Contains(p, "nonzero") {
			continue
		}
		if len(l.Mask) > 0 && !l.Mask.Contains(p, "nonzero") {
			continue
		}
		for _, s := range l.Shapes {
			if s.Shape.Brush.Spot != "" {
				continue
			}
			q := s.Inverse.Apply(p)
			if s.Path.Contains(q, s.Shape.FillRule) {
				return true
			}
		}
	}
	return false
}

func collectOverlaps(order []string, cov []float64, mark []bool, totalSamples int) []Overlap {
	n := len(order)
	type pairKey struct{ top, under int }
	pair := map[pairKey]*Overlap{}
	topSum := map[pairKey]float64{}
	for s := 0; s < totalSamples; s++ {
		active := []int{}
		for k := 0; k < n; k++ {
			if cov[s*n+k] > 0 {
				active = append(active, k)
			}
		}
		for _, top := range active {
			for _, under := range active {
				if top <= under {
					continue // top must print later (higher press order)
				}
				key := pairKey{top, under}
				o := pair[key]
				if o == nil {
					o = &Overlap{TopInk: order[top], UnderInk: order[under]}
					pair[key] = o
				}
				o.Samples++
				topSum[key] += cov[s*n+top]
				if mark[s*n+top] {
					o.Overprinted = true
				}
			}
		}
	}
	// Deterministic press-order ordering keeps the manifest byte-stable across runs.
	keys := make([]pairKey, 0, len(pair))
	for k := range pair {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].top != keys[j].top {
			return keys[i].top < keys[j].top
		}
		return keys[i].under < keys[j].under
	})
	overlaps := make([]Overlap, 0, len(keys))
	for _, key := range keys {
		o := pair[key]
		o.MeanTop = topSum[key] / float64(o.Samples)
		overlaps = append(overlaps, *o)
	}
	return overlaps
}

func encodePlates(d scene.Document, order []string, cov []float64) ([]Plate, error) {
	n := len(order)
	plates := make([]Plate, n)
	totalSamples := d.Width * d.Height * 4
	for k, id := range order {
		img := image.NewGray(image.Rect(0, 0, d.Width, d.Height))
		samplesCovered := 0
		meanSum := 0.0
		for y := 0; y < d.Height; y++ {
			for x := 0; x < d.Width; x++ {
				sum := 0.0
				for s := 0; s < 4; s++ {
					sample := (y*d.Width+x)*4 + s
					v := cov[sample*n+k]
					sum += v
					if v > 0 {
						samplesCovered++
					}
					meanSum += v
				}
				img.SetGray(x, y, color.Gray{Y: uint8(math.Round(math.Min(1, sum/4) * 255))})
			}
		}
		var buf bytes.Buffer
		if err := png.Encode(&buf, img); err != nil {
			return nil, err
		}
		sp := spotByID(d, id)
		plates[k] = Plate{
			Ink: id, Name: sp.Name, Format: "spotplate", Width: d.Width, Height: d.Height,
			Digest: repository.Hash(buf.Bytes()), SamplesCovered: samplesCovered,
			MeanCoverage: meanSum / float64(totalSamples), Data: buf.Bytes(),
		}
	}
	return plates, nil
}

func inkOrder(d scene.Document) []string {
	order := []string{}
	for _, sp := range d.Spots {
		order = append(order, sp.ID)
	}
	return order
}

func spotByID(d scene.Document, id string) scene.Spot {
	for _, sp := range d.Spots {
		if sp.ID == id {
			return sp
		}
	}
	return scene.Spot{}
}

func diffExplanation(identity bool, overprintPixels, processShapes int) string {
	switch {
	case identity:
		return "no overprinted ink: every film stacks with source-over, so the plates recompose byte-identically to the reference PNG"
	case overprintPixels > 0 && processShapes > 0:
		return "differences are confined to overprint overlap (linear-light multiply vs source-over) and to process RGB shapes that have no spot plate"
	case overprintPixels > 0:
		return "differences are confined to declared overprint overlap, where physical ink stacking uses multiply instead of the reference source-over blend"
	default:
		return "differences come from process RGB shapes that have no spot plate; spot coverage geometry itself matches the reference supersampling"
	}
}
