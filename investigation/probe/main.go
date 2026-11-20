// Command probe drives the repository's real validate/geom/vector code paths
// over perturbed transforms, so the affected-range conclusions use the exact
// admission rules and the exact "%.9g" serialization the service ships.
//
// Output is a CSV written to stdout; columns are documented in the header.
package main

import (
	"fmt"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"prismplate/internal/vector"
	"regexp"
)

var matRe = regexp.MustCompile(`matrix\(([^)]+)\)`)
var pathRe = regexp.MustCompile(`<path d="([^"]+)"`)
var verbRe = regexp.MustCompile(`[ML](-?[0-9.eE+-]+)\s+(-?[0-9.eE+-]+)`)

func basePath() []geom.Op {
	return []geom.Op{
		{Kind: "M", Points: []geom.Point{{X: 0.000008000002708286047, Y: 0}}},
		{Kind: "L", Points: []geom.Point{{X: 319999.99988, Y: -319999.999872}}},
		{Kind: "L", Points: []geom.Point{{X: 0.000040000013541430235, Y: 0}}},
		{Kind: "L", Points: []geom.Point{{X: -319999.999832, Y: 319999.999872}}},
		{Kind: "Z"},
	}
}

func docWith(m geom.Matrix) scene.Document {
	return scene.Document{
		Width: 48, Height: 48,
		Background: scene.Color{A: 0},
		Layers: []scene.Layer{{
			ID: "l", Opacity: 1, Blend: "normal",
			Shapes: []scene.Shape{{
				ID: "s", FillRule: "nonzero", Path: basePath(), Transform: m,
				Brush: scene.Brush{Kind: "solid", Color: scene.Color{R: 0.1, G: 0.3, B: 0.95, A: 1}},
			}},
		}},
	}
}

func area(pts []geom.Point) float64 {
	var s float64
	for i := range pts {
		a, b := pts[i], pts[(i+1)%len(pts)]
		s += a.X*b.Y - a.Y*b.X
	}
	return math.Abs(s) / 2
}

func extractTokens(svg string) (geom.Matrix, [][2]float64) {
	var mt geom.Matrix
	if _, e := fmt.Sscanf(matRe.FindStringSubmatch(svg)[1],
		"%f %f %f %f %f %f", &mt[0], &mt[1], &mt[2], &mt[3], &mt[4], &mt[5]); e != nil {
		panic(e)
	}
	var local [][2]float64
	for _, g := range verbRe.FindAllStringSubmatch(pathRe.FindStringSubmatch(svg)[1], -1) {
		var x, y float64
		fmt.Sscanf(g[1], "%g", &x)
		fmt.Sscanf(g[2], "%g", &y)
		local = append(local, [2]float64{x, y})
	}
	return mt, local
}

// result classifies one matrix through the real pipeline.
type result struct {
	admitted   bool
	reason     string
	areaSvc    float64
	areaView   float64
	drift      float64
	degenerate bool // viewer area <= 1% of service area
}

func probe(m geom.Matrix) result {
	c, err := validate.Compile(docWith(m))
	if err != nil {
		return result{admitted: false, reason: err.Error()}
	}
	cs := c.Layers[0].Shapes[0]
	svc := make([]geom.Point, len(cs.Path[0]))
	for i, v := range cs.Path[0] {
		svc[i] = m.Apply(v)
	}
	mt, local := extractTokens(string(vector.Export(c)))
	viewer := make([]geom.Point, len(local))
	for i, v := range local {
		viewer[i] = mt.Apply(geom.Point{X: v[0], Y: v[1]})
	}
	var drift float64
	for i := range svc {
		if d := math.Hypot(viewer[i].X-svc[i].X, viewer[i].Y-svc[i].Y); d > drift {
			drift = d
		}
	}
	aS, aV := area(svc), area(viewer)
	return result{
		admitted: true, areaSvc: aS, areaView: aV, drift: drift,
		degenerate: aV <= aS/100,
	}
}

func main() {
	fmt.Println("section,case,det,admission,area_service_f64,area_viewer_f64,max_vertex_drift_px,viewer_degenerate")
	emit := func(section, name string, m geom.Matrix) {
		r := probe(m)
		if !r.admitted {
			fmt.Printf("%s,%s,%.6g,REJECTED(%s),,,,,\n", section, name,
				m[0]*m[3]-m[1]*m[2], r.reason)
			return
		}
		fmt.Printf("%s,%s,%.6g,OK,%.9g,%.9g,%.6g,%v\n", section, name,
			m[0]*m[3]-m[1]*m[2], r.areaSvc, r.areaView, r.drift, r.degenerate)
	}

	// --- Section 1: the reported matrix and exact neighboring m2 values.
	// Uniform [s,s,s,s] is singular by construction (determinant 0), so the
	// legal family is m2 differing slightly from the other three entries.
	fmt.Println("# m2 sweep around the reported 999999.9995; m0=m1=m3=999999.9996")
	for _, d := range []float64{-1, -0.1, -0.01, -0.005, -0.001, -0.0005, -0.0001, 0, 0.0001, 0.0005, 0.001, 0.005, 0.01, 0.1, 1} {
		m2 := 999999.9995 + d
		m := geom.Matrix{999999.9996, 999999.9996, m2, 999999.9996, 0, 0}
		emit("m2_sweep", fmt.Sprintf("m2=%.10g(d%+.4g)", m2, d), m)
	}

	// --- Section 2: same %.9g rounding grid (all entries round to 1000000),
	// but varying entry spread. Shows the whole grid shares the defect.
	fmt.Println("# same rounding grid: every entry in [999999.9995,1000000.0005)")
	for _, gap := range []float64{0, 1e-4, 1e-3, 1e-2, 0.1, 10, 50, 99, 100, 500} {
		m := geom.Matrix{999999.9996, 999999.9996, 999999.9996 - gap, 999999.9996, 0, 0}
		emit("same_grid", fmt.Sprintf("gap=%.4g", gap), m)
	}

	// --- Section 3: scale magnitude sweep with a fixed tiny m2 offset so the
	// matrix stays invertible; isolates at which magnitudes %.9g bites.
	fmt.Println("# magnitude sweep: m0=m1=m3=s, m2=s-1e-4")
	for _, s := range []float64{1e2, 1e3, 1e4, 1e5, 316227.766, 1e6 - 1, 999999.9996, 1e6, 1e6 + 1, 1e6 + 100} {
		m := geom.Matrix{s, s, s - 1e-4, s, 0, 0}
		emit("magnitude", fmt.Sprintf("s=%.10g", s), m)
	}

	// --- Section 4: exact reported case, fingerprint for the report.
	fmt.Println("# fingerprint cases")
	emit("fingerprint", "reported_matrix", geom.Matrix{999999.9996, 999999.9996, 999999.9995, 999999.9996, 0, 0})
}
