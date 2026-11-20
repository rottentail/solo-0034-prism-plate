package anchor

import (
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"testing"
)

func brush() scene.Brush {
	return scene.Brush{
		Kind: "linear",
		From: geom.Point{X: 10, Y: 20},
		To:   geom.Point{X: 90, Y: 60},
		Stops: []scene.Stop{
			{At: 0, Color: scene.Color{R: 1, A: 1}},
			{At: 1, Color: scene.Color{B: 1, A: 1}},
		},
	}
}

var square = []geom.Op{
	{Kind: "M", Points: []geom.Point{{X: 0, Y: 0}}},
	{Kind: "L", Points: []geom.Point{{X: 100, Y: 0}}},
	{Kind: "L", Points: []geom.Point{{X: 100, Y: 80}}},
	{Kind: "L", Points: []geom.Point{{X: 0, Y: 80}}},
	{Kind: "Z"},
}

// fieldT compares the clamped scalar parameter rendered before and after
// conversion at a grid of points inside the shape.
func fieldT(t *testing.T, b scene.Brush, m geom.Matrix, target string) {
	t.Helper()
	res, err := Convert(b, m, square, "l", "s", target)
	if err != nil {
		t.Fatalf("convert: %v", err)
	}
	for i := 0; i <= 20; i++ {
		for j := 0; j <= 20; j++ {
			local := geom.Point{X: float64(i) * 5, Y: float64(j) * 4}
			canvas := m.Apply(local)
			// Scalar parameter as rendered by the pre-conversion brush.
			oldPoint := local
			if b.EffectiveAnchor() == scene.AnchorCanvas {
				oldPoint = canvas
			}
			want := clamp01(axisT(oldPoint, b.From, b.To))
			// Scalar parameter as rendered by the converted brush.
			newPoint := local
			if target == scene.AnchorCanvas {
				newPoint = canvas
			}
			got := clamp01(axisT(newPoint, res.Brush.From, res.Brush.To))
			if math.Abs(got-want) > 1e-12 {
				t.Fatalf("field mismatch at local=%v: want %.12f got %.12f", local, want, got)
			}
		}
	}
	if res.Brush.EffectiveAnchor() != target {
		t.Fatalf("anchor = %q, want %q", res.Brush.Anchor, target)
	}
}

func TestSimilarityObjectToCanvas(t *testing.T) {
	// Rotation 90deg plus uniform scale 2 plus translation.
	m := geom.Matrix{0, 2, -2, 0, 30, -7}
	fieldT(t, brush(), m, scene.AnchorCanvas)
	res, err := Convert(brush(), m, square, "l", "s", scene.AnchorCanvas)
	if err != nil {
		t.Fatal(err)
	}
	if !res.Report.NaiveExact || res.Report.Reflected {
		t.Fatalf("similarity report wrong: %+v", res.Report)
	}
	if math.Abs(res.Report.ConformalRatio-1) > 1e-9 {
		t.Fatalf("conformal ratio = %v, want 1", res.Report.ConformalRatio)
	}
	if res.Report.NaiveMaxTError > 1e-12 {
		t.Fatalf("naive error = %v on similarity", res.Report.NaiveMaxTError)
	}
}

func TestNonUniformScaleNaiveDrift(t *testing.T) {
	// Diagonal non-uniform scale: gradient axis is not aligned with an
	// eigenvector, so moving endpoints directly must change colors.
	m := geom.Matrix{3, 0, 0, 0.5, 0, 0}
	res, err := Convert(brush(), m, square, "l", "s", scene.AnchorCanvas)
	if err != nil {
		t.Fatal(err)
	}
	if res.Report.NaiveExact {
		t.Fatal("non-uniform scale reported as naive-exact")
	}
	if res.Report.NaiveMaxTError <= 0.01 {
		t.Fatalf("expected material naive error, got %v", res.Report.NaiveMaxTError)
	}
	if res.Report.NaiveAxisDegrees <= 1 {
		t.Fatalf("expected axis drift, got %v deg", res.Report.NaiveAxisDegrees)
	}
	if !res.Report.Exact {
		t.Fatal("explicit reconstruction must always preserve the field")
	}
	fieldT(t, brush(), m, scene.AnchorCanvas)
}

func TestMirrorIsExact(t *testing.T) {
	// Pure horizontal reflection is orthogonal: naive and exact agree.
	m := geom.Matrix{-1, 0, 0, 1, 100, 0}
	res, err := Convert(brush(), m, square, "l", "s", scene.AnchorCanvas)
	if err != nil {
		t.Fatal(err)
	}
	if !res.Report.Reflected || !res.Report.NaiveExact {
		t.Fatalf("mirror report wrong: %+v", res.Report)
	}
	fieldT(t, brush(), m, scene.AnchorCanvas)
}

func TestMirrorWithNonUniformScale(t *testing.T) {
	// Reflection plus anisotropic scale: reflected=true, naive drifts,
	// explicit reconstruction still exact.
	m := geom.Matrix{-2.5, 0, 0, 0.4, 500, 12}
	res, err := Convert(brush(), m, square, "l", "s", scene.AnchorCanvas)
	if err != nil {
		t.Fatal(err)
	}
	if !res.Report.Reflected || res.Report.NaiveExact {
		t.Fatalf("report wrong: %+v", res.Report)
	}
	fieldT(t, brush(), m, scene.AnchorCanvas)
}

func TestRoundTrip(t *testing.T) {
	m := geom.Matrix{2, 0.3, -0.4, 0.7, 15, -9}
	first, err := Convert(brush(), m, square, "l", "s", scene.AnchorCanvas)
	if err != nil {
		t.Fatal(err)
	}
	back, err := Convert(first.Brush, m, square, "l", "s", scene.AnchorObject)
	if err != nil {
		t.Fatal(err)
	}
	if math.Abs(back.Brush.From.X-brush().From.X) > 1e-9 || math.Abs(back.Brush.From.Y-brush().From.Y) > 1e-9 {
		t.Fatalf("round-trip from = %v, want %v", back.Brush.From, brush().From)
	}
	if math.Abs(back.Brush.To.X-brush().To.X) > 1e-9 || math.Abs(back.Brush.To.Y-brush().To.Y) > 1e-9 {
		t.Fatalf("round-trip to = %v, want %v", back.Brush.To, brush().To)
	}
}

func TestCanvasToObjectField(t *testing.T) {
	// Start from an explicitly canvas-anchored brush and convert back.
	m := geom.Matrix{1.5, 0.2, 0.1, 0.8, 40, 25}
	cb := brush()
	cb.Anchor = scene.AnchorCanvas
	cb.From = geom.Point{X: 60, Y: 30}
	cb.To = geom.Point{X: 240, Y: 200}
	fieldT(t, cb, m, scene.AnchorObject)
}

func TestRejections(t *testing.T) {
	m := geom.Identity
	if _, err := Convert(brush(), m, square, "l", "s", scene.AnchorObject); err == nil {
		t.Fatal("object->object must be rejected")
	}
	solid := scene.Brush{Kind: "solid", Color: scene.Color{R: 1, A: 1}}
	if _, err := Convert(solid, m, square, "l", "s", scene.AnchorCanvas); err == nil {
		t.Fatal("solid brush must be rejected")
	}
	singular := geom.Matrix{1, 2, 2, 4, 0, 0}
	if _, err := Convert(brush(), singular, square, "l", "s", scene.AnchorCanvas); err == nil {
		t.Fatal("singular transform must be rejected")
	}
	if _, err := Convert(brush(), m, square, "l", "s", "diagonal"); err == nil {
		t.Fatal("unknown target must be rejected")
	}
}
