package sanitize

import (
	"context"
	"encoding/json"
	"math"
	"os"
	"testing"

	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
)

func loadExample(t *testing.T) scene.Document {
	t.Helper()
	b, err := os.ReadFile("../../examples/amber-proof.json")
	if err != nil {
		t.Fatal(err)
	}
	var q struct {
		Document scene.Document `json:"document"`
	}
	if err := json.Unmarshal(b, &q); err != nil {
		t.Fatal(err)
	}
	return q.Document
}

func rect(points ...float64) []geom.Point {
	out := make([]geom.Point, 0, len(points)/2)
	for i := 0; i+1 < len(points); i += 2 {
		out = append(out, geom.Point{X: points[i], Y: points[i+1]})
	}
	return out
}

func TestClipSquare(t *testing.T) {
	sq := rect(0, 0, 10, 0, 10, 10, 0, 10)
	clip := rect(2, 2, 8, 2, 8, 8, 2, 8)
	out, err := clipToConvex([][]geom.Point{sq}, clip, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 1 || math.Abs(areaOf(out)-36) > 1e-9 {
		t.Fatalf("want one piece of area 36, got %v", out)
	}
}

func areaOf(rs [][]geom.Point) float64 {
	a := 0.0
	for _, r := range rs {
		a += math.Abs(polygonArea(r))
	}
	return a
}

// Clipping a concave U can split it into two disconnected pieces.
func TestClipConcaveSplits(t *testing.T) {
	u := rect(0, 0, 10, 0, 10, 10, 7, 10, 7, 3, 3, 3, 3, 10, 0, 10)
	clip := rect(-1, 6, 11, 6, 11, 11, -1, 11)
	out, err := clipToConvex([][]geom.Point{u}, clip, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 2 || math.Abs(areaOf(out)-24) > 1e-9 {
		t.Fatalf("want two pieces of area 24, got %v", out)
	}
}

// A hole inside an even-odd fill must keep its reversed orientation.
func TestClipKeepsHoleOrientation(t *testing.T) {
	outer := rect(0, 0, 10, 0, 10, 10, 0, 10)
	hole := rect(3, 3, 3, 7, 7, 7, 7, 3)
	clip := rect(1, 1, 9, 1, 9, 9, 1, 9)
	out, err := clipToConvex([][]geom.Point{outer, hole}, clip, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(out) != 2 || polygonArea(out[0])*polygonArea(out[1]) >= 0 {
		t.Fatalf("hole orientation lost: %v", out)
	}
}

// Every delivery must recompose exactly like the source inside the region and
// be background-only outside it, for several region shapes.
func TestDeliveryMatchesSource(t *testing.T) {
	d := loadExample(t)
	regions := [][]geom.Point{
		rect(20, 20, 172, 20, 172, 128, 20, 128),
		rect(0, 30, 192, 30, 160, 140, 30, 140),
		rect(40, 40, 150, 40, 150, 130, 40, 130),
	}
	for _, pts := range regions {
		area, err := NewArea(pts, d.Width, d.Height)
		if err != nil {
			t.Fatal(err)
		}
		res, err := Sanitize(d, area)
		if err != nil {
			t.Fatalf("region %v: %v", pts, err)
		}
		if _, err := validate.Compile(res.Document); err != nil {
			t.Fatalf("delivery is not admissible: %v", err)
		}
		b, err := ComputeBound(context.Background(), d, res.Document, area)
		if err != nil {
			t.Fatal(err)
		}
		t.Logf("region %v bound=%+v", pts, b)
		if b.MaxError != 0 || b.OutsideError != 0 || b.Interior == 0 {
			t.Fatalf("region %v: %+v", pts, b)
		}
	}
}

// A large enclosing fill must be replaced by the region, not dropped (dropping
// would erase interior color) and not kept (keeping would leak outside).
func TestEnclosingFillReplaced(t *testing.T) {
	d := loadExample(t)
	d.Layers = d.Layers[:1]
	d.Layers[0].Clip = nil
	d.Layers[0].Shapes = []scene.Shape{{
		ID: "big",
		Path: []geom.Op{
			{Kind: "M", Points: rect(-100, -100)},
			{Kind: "L", Points: rect(300, -100)},
			{Kind: "L", Points: rect(300, 300)},
			{Kind: "L", Points: rect(-100, 300)},
			{Kind: "Z"},
		},
		FillRule:  "nonzero",
		Brush:     scene.Brush{Kind: "solid", Color: scene.Color{R: .5, G: .5, B: .5, A: 1}},
		Transform: geom.Identity,
	}}
	pts := rect(50, 50, 140, 50, 140, 100, 50, 100)
	area, err := NewArea(pts, d.Width, d.Height)
	if err != nil {
		t.Fatal(err)
	}
	res, err := Sanitize(d, area)
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Document.Layers[0].Shapes) != 1 {
		t.Fatal("enclosing fill dropped")
	}
	for _, op := range res.Document.Layers[0].Shapes[0].Path {
		for _, p := range op.Points {
			if !area.InsideOrOn(p) {
				t.Fatalf("retained vertex %v outside delivery area", p)
			}
		}
	}
	b, err := ComputeBound(context.Background(), d, res.Document, area)
	if err != nil {
		t.Fatal(err)
	}
	if b.MaxError != 0 || b.OutsideError != 0 {
		t.Fatalf("bound %+v", b)
	}
}

// A local non-similarity transform must be cut in its own frame, be preserved
// for further editing, and leave nothing visible outside the region.
func TestLocalTransformCrossing(t *testing.T) {
	d := loadExample(t)
	d.Layers[1].Shapes[0].Transform = geom.Matrix{0.866, 0.5, -0.5, 0.866, 40, 10}
	pts := rect(60, 40, 150, 40, 120, 110, 50, 100)
	area, err := NewArea(pts, d.Width, d.Height)
	if err != nil {
		t.Fatal(err)
	}
	res, err := Sanitize(d, area)
	if err != nil {
		t.Fatal(err)
	}
	if res.Report.BakedShapes == 0 {
		t.Fatalf("expected a cut shape: %+v", res.Report)
	}
	if res.Document.Layers[1].Shapes[0].Transform != d.Layers[1].Shapes[0].Transform {
		t.Fatal("local transform must be preserved")
	}
	gotBrush, _ := json.Marshal(res.Document.Layers[1].Shapes[0].Brush)
	wantBrush, _ := json.Marshal(d.Layers[1].Shapes[0].Brush)
	if string(gotBrush) != string(wantBrush) {
		t.Fatal("brush must be preserved")
	}
	b, err := ComputeBound(context.Background(), d, res.Document, area)
	if err != nil {
		t.Fatal(err)
	}
	if b.MaxError != 0 || b.OutsideError != 0 {
		t.Fatalf("bound %+v", b)
	}
}

// Masks and clips larger than the region are replaced, not retained as a
// hiding device: every retained vertex must be inside the delivery area.
func TestMaskShrinksToRegion(t *testing.T) {
	d := loadExample(t)
	pts := rect(20, 20, 100, 20, 100, 100, 20, 100)
	area, err := NewArea(pts, d.Width, d.Height)
	if err != nil {
		t.Fatal(err)
	}
	res, err := Sanitize(d, area)
	if err != nil {
		t.Fatal(err)
	}
	for _, l := range res.Document.Layers {
		for _, op := range l.Mask {
			for _, p := range op.Points {
				if !area.InsideOrOn(p) {
					t.Fatalf("mask vertex %v outside delivery area", p)
				}
			}
		}
		for _, op := range l.Clip {
			for _, p := range op.Points {
				if !area.InsideOrOn(p) {
					t.Fatalf("clip vertex %v outside delivery area", p)
				}
			}
		}
	}
}

// Contours that cannot be cut with a simple-polygon boolean are reported before
// any conversion is attempted.
func TestPreflightRejectsSelfIntersecting(t *testing.T) {
	d := loadExample(t)
	d.Layers = d.Layers[:1]
	d.Layers[0].Clip = nil
	d.Layers[0].Shapes = []scene.Shape{{
		ID: "bowtie",
		Path: []geom.Op{
			{Kind: "M", Points: rect(0, 0)},
			{Kind: "L", Points: rect(180, 120)},
			{Kind: "L", Points: rect(180, 0)},
			{Kind: "L", Points: rect(0, 120)},
			{Kind: "Z"},
		},
		FillRule:  "nonzero",
		Brush:     scene.Brush{Kind: "solid", Color: scene.Color{R: .5, A: 1}},
		Transform: geom.Identity,
	}}
	pts := rect(80, 50, 100, 50, 100, 60, 80, 60)
	elig := Preflight([]Revision{{Number: 1, Scene: d}}, pts)
	if elig.Convertible || elig.Blockers[0].Shape != "bowtie" {
		t.Fatalf("expected a named blocker, got %+v", elig)
	}
	area, _ := NewArea(pts, d.Width, d.Height)
	if _, err := Sanitize(d, area); err == nil {
		t.Fatal("export must refuse a blocked revision")
	}
}

func TestRegionValidation(t *testing.T) {
	for _, tc := range []struct {
		name   string
		points []geom.Point
	}{
		{"too few", rect(0, 0, 10, 0)},
		{"collinear", rect(0, 0, 5, 0, 10, 0)},
		{"concave", rect(0, 0, 10, 0, 10, 10, 5, 2, 0, 10)},
		{"zero area", rect(0, 0, 4, 0, 8, 0)},
		{"degenerate edge", rect(0, 0, 10, 0, 10, 0, 5, 9)},
	} {
		if _, err := NewPolygon(tc.points); err == nil {
			t.Errorf("%s: expected rejection", tc.name)
		}
	}
	clockwise := rect(0, 0, 0, 10, 10, 10, 10, 0)
	if a, err := NewPolygon(clockwise); err != nil {
		t.Fatalf("clockwise input should normalize: %v", err)
	} else if polygonArea(a.Pts) <= 0 {
		t.Fatal("winding was not normalized")
	}
}

// A delivery produced from the same input twice must be byte-identical.
func TestDeterministic(t *testing.T) {
	d := loadExample(t)
	pts := rect(25, 25, 160, 25, 160, 120, 25, 120)
	area, err := NewArea(pts, d.Width, d.Height)
	if err != nil {
		t.Fatal(err)
	}
	a, err := Sanitize(d, area)
	if err != nil {
		t.Fatal(err)
	}
	b, err := Sanitize(d, area)
	if err != nil {
		t.Fatal(err)
	}
	ab, _ := json.Marshal(a.Document)
	bb, _ := json.Marshal(b.Document)
	if string(ab) != string(bb) || a.Digest != b.Digest {
		t.Fatal("delivery is not deterministic")
	}
}

// A shape with one contour inside the region and one crossing it keeps the
// authored contour verbatim (curves included) while cutting only the crossing
// contour; even-odd holes must still render as holes.
func TestMixedContoursInOneShape(t *testing.T) {
	d := loadExample(t)
	d.Layers = d.Layers[:1]
	d.Layers[0].Clip = nil
	// Outer arc crosses the right edge of the region; inner square is inside.
	d.Layers[0].Shapes = []scene.Shape{{
		ID: "mixed",
		Path: []geom.Op{
			{Kind: "M", Points: rect(20, 20)},
			{Kind: "C", Points: []geom.Point{{X: 40, Y: -40}, {X: 160, Y: -40}, {X: 180, Y: 20}}},
			{Kind: "C", Points: []geom.Point{{X: 200, Y: 80}, {X: 160, Y: 120}, {X: 20, Y: 120}}},
			{Kind: "Z"},
			{Kind: "M", Points: rect(60, 60)},
			{Kind: "L", Points: rect(60, 90)},
			{Kind: "L", Points: rect(90, 90)},
			{Kind: "L", Points: rect(90, 60)},
			{Kind: "Z"},
		},
		FillRule:  "evenodd",
		Brush:     scene.Brush{Kind: "solid", Color: scene.Color{R: .3, G: .2, B: .9, A: 1}},
		Transform: geom.Identity,
	}}
	pts := rect(30, 30, 110, 30, 110, 110, 30, 110)
	area, err := NewArea(pts, d.Width, d.Height)
	if err != nil {
		t.Fatal(err)
	}
	res, err := Sanitize(d, area)
	if err != nil {
		t.Fatal(err)
	}
	// The untouched inner contour must be present verbatim, so its authored
	// winding and vertex order survive for further editing.
	got, _ := json.Marshal(res.Document.Layers[0].Shapes[0].Path[5:])
	want, _ := json.Marshal(d.Layers[0].Shapes[0].Path[4:])
	if string(got) != string(want) {
		t.Fatalf("authored inner contour changed:\n got %s\nwant %s", got, want)
	}
	b, err := ComputeBound(context.Background(), d, res.Document, area)
	if err != nil {
		t.Fatal(err)
	}
	if b.MaxError != 0 || b.OutsideError != 0 {
		t.Fatalf("bound %+v", b)
	}
}
