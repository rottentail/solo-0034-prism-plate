// Command sweep renders the seam plate at a dense range of horizontal offsets
// through the production validate+raster path and reports, per offset, which
// pixels carry the inner-contour hole.
//
// Usage (from the repository root):
//
//	go run ./analysis/seam-study/sweep -step 0.001 -out analysis/seam-study/sweep.json
//
// A hole pixel is one where all four 2x2 samples lie inside the outer
// silhouette (so the silhouette cannot explain any partial alpha) and at
// least one sample lies inside the inner contour. This is the same rule as
// detect_hole.py, computed here against the production compiled geometry.
// The output is evidence for analysis/seam-study/README.md: the hole's pixel
// footprint is a function of the fixed 2x2 sample lattice, not of the contour
// geometry, which never changes.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"image"
	_ "image/png"
	"math"
	"os"
	"sort"

	"prismplate/internal/geom"
	"prismplate/internal/raster"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
)

func op(kind string, pts ...geom.Point) geom.Op {
	return geom.Op{Kind: kind, Points: pts}
}

func outerOps() []geom.Op {
	return []geom.Op{
		op("M", geom.Point{X: 6, Y: 24}),
		op("C", geom.Point{X: 6, Y: 6}, geom.Point{X: 42, Y: 6}, geom.Point{X: 42, Y: 24}),
		op("C", geom.Point{X: 42, Y: 42}, geom.Point{X: 6, Y: 42}, geom.Point{X: 6, Y: 24}),
		op("Z"),
	}
}

func innerOps() []geom.Op {
	return []geom.Op{
		op("M", geom.Point{X: 22.1, Y: 12}),
		op("L", geom.Point{X: 22.4, Y: 12}),
		op("L", geom.Point{X: 22.4, Y: 36}),
		op("L", geom.Point{X: 22.1, Y: 36}),
		op("Z"),
	}
}

func document(dx float64) scene.Document {
	blue := scene.Color{R: 0, G: 0, B: 1, A: 1}
	shape := scene.Shape{
		ID:        "seam-shape",
		FillRule:  "evenodd",
		Path:      append(outerOps(), innerOps()...),
		Brush:     scene.Brush{Kind: "solid", Color: blue},
		Transform: geom.Matrix{1, 0, 0, 1, dx, 0},
	}
	return scene.Document{
		Width:      48,
		Height:     48,
		Background: scene.Color{},
		Layers: []scene.Layer{{
			ID:      "seam-layer",
			Opacity: 1,
			Blend:   "normal",
			Shapes:  []scene.Shape{shape},
		}},
	}
}

type record struct {
	DX       float64 `json:"dx"`
	Visible  bool    `json:"visible"`
	Count    int     `json:"hole_pixel_count"`
	Columns  []int   `json:"hole_columns"`
	RowsFrom int     `json:"hole_rows_from"`
	RowsTo   int     `json:"hole_rows_to"`
	Alphas   []int   `json:"hole_alphas"`
}

func main() {
	step := flag.Float64("step", 0.001, "horizontal offset step in pixels")
	out := flag.String("out", "", "output JSON path (empty prints to stdout)")
	flag.Parse()

	outer := geom.Flatten(outerOps())
	inner := geom.Flatten(innerOps())

	type span struct {
		Visible bool    `json:"visible"`
		Start   float64 `json:"start"`
		End     float64 `json:"end"`
	}
	var spans []span
	var records []record
	// Multiply rather than accumulate so a given step lands on a reproducible
	// binary value independent of step count.
	n := int(math.Round(1.0 / *step))
	for i := 0; i <= n; i++ {
		dx := float64(i) * *step
		compiled, err := validate.Compile(document(dx))
		if err != nil {
			fmt.Fprintln(os.Stderr, "compile:", err)
			os.Exit(1)
		}
		data, err := raster.Render(context.Background(), compiled)
		if err != nil {
			fmt.Fprintln(os.Stderr, "render:", err)
			os.Exit(1)
		}
		decoded, _, err := image.Decode(bytes.NewReader(data))
		if err != nil {
			fmt.Fprintln(os.Stderr, "decode:", err)
			os.Exit(1)
		}
		img := decoded.(*image.NRGBA)
		rec := record{DX: dx, Columns: []int{}, Alphas: []int{}, RowsFrom: -1, RowsTo: -1}
		cols, alphas := map[int]bool{}, map[int]bool{}
		for y := 0; y < 48; y++ {
			for x := 0; x < 48; x++ {
				allOuter, anyInner := true, false
				for sy := 0; sy < 2; sy++ {
					for sx := 0; sx < 2; sx++ {
						p := geom.Point{X: float64(x) + (float64(sx)+0.5)/2, Y: float64(y) + (float64(sy)+0.5)/2}
						q := geom.Point{X: p.X - dx, Y: p.Y}
						if !outer.Contains(q, "nonzero") {
							allOuter = false
						}
						if inner.Contains(q, "nonzero") {
							anyInner = true
						}
					}
				}
				if allOuter && anyInner {
					a := int(img.NRGBAAt(x, y).A)
					rec.Count++
					cols[x] = true
					alphas[a] = true
					if rec.RowsFrom < 0 || y < rec.RowsFrom {
						rec.RowsFrom = y
					}
					if y > rec.RowsTo {
						rec.RowsTo = y
					}
				}
			}
		}
		for x := range cols {
			rec.Columns = append(rec.Columns, x)
		}
		for a := range alphas {
			rec.Alphas = append(rec.Alphas, a)
		}
		sort.Ints(rec.Columns)
		sort.Ints(rec.Alphas)
		rec.Visible = rec.Count > 0
		records = append(records, rec)
		if n := len(spans); n > 0 && spans[n-1].Visible == rec.Visible {
			spans[n-1].End = dx
		} else {
			spans = append(spans, span{Visible: rec.Visible, Start: dx, End: dx})
		}
	}
	payload, err := json.MarshalIndent(struct {
		Step    float64  `json:"step"`
		Spans   []span   `json:"spans"`
		Records []record `json:"records"`
	}{*step, spans, records}, "", " ")
	if err != nil {
		fmt.Fprintln(os.Stderr, "json:", err)
		os.Exit(1)
	}
	if *out == "" {
		fmt.Println(string(payload))
		return
	}
	if err := os.WriteFile(*out, append(payload, '\n'), 0o644); err != nil {
		fmt.Fprintln(os.Stderr, "write:", err)
		os.Exit(1)
	}
}
