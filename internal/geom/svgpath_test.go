package geom

import (
	"errors"
	"math"
	"testing"
)

func TestParseSVGPathAbsolute(t *testing.T) {
	text := "M 0 0 L 10 0 L 10 10 L 0 10 Z"
	ops, err := ParseSVGPath(text)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	want := []Op{
		{Kind: "M", Points: []Point{{0, 0}}},
		{Kind: "L", Points: []Point{{10, 0}}},
		{Kind: "L", Points: []Point{{10, 10}}},
		{Kind: "L", Points: []Point{{0, 10}}},
		{Kind: "Z"},
	}
	if !opsEqual(ops, want) {
		t.Fatalf("ops mismatch:\n got %v\nwant %v", ops, want)
	}
}

func TestParseSVGPathRelative(t *testing.T) {
	// m/l relative coordinates resolve to the same absolute square.
	ops, err := ParseSVGPath("m0 0 l10 0 l0 10 l-10 0 z")
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	want := []Op{
		{Kind: "M", Points: []Point{{0, 0}}},
		{Kind: "L", Points: []Point{{10, 0}}},
		{Kind: "L", Points: []Point{{10, 10}}},
		{Kind: "L", Points: []Point{{0, 10}}},
		{Kind: "Z"},
	}
	if !opsEqual(ops, want) {
		t.Fatalf("relative ops mismatch:\n got %v\nwant %v", ops, want)
	}
}

func TestParseSVGPathNumericSpellings(t *testing.T) {
	// Sign, leading dot, no-space comma separators and exponents must all be
	// accepted and equal the plain absolute spelling.
	plain := "M 0 0 L 10 0 L 10 10 L 0 10 Z"
	variants := []string{
		"M0,0L10,0L10,10L0,10Z",
		"M.0 .0 L1e1 0 L10 1e1 L0 10 Z",
		"M+0 -0 L 10.0 0.0 L 1.0e1 10 L 0 10 Z",
		"M0 0 L10. 0 L10. 10. L0 10 Z", // SVG permits trailing decimal points
		"  M 0 0 L 10 0 L 10 10 L 0 10 Z  ",
	}
	want, err := ParseSVGPath(plain)
	if err != nil {
		t.Fatalf("baseline: %v", err)
	}
	for _, v := range variants {
		got, err := ParseSVGPath(v)
		if err != nil {
			t.Errorf("variant %q: %v", v, err)
			continue
		}
		if !opsEqual(got, want) {
			t.Errorf("variant %q mismatch:\n got %v\nwant %v", v, got, want)
		}
	}
}

func TestParseSVGPathCurvesAndMultiSubpath(t *testing.T) {
	text := "M0 0 Q5 -2 10 0 C12 4 8 12 10 10 L0 10 Z M20 20 L30 20 L30 30 Z"
	ops, err := ParseSVGPath(text)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(ops) != 9 {
		t.Fatalf("op count = %d, want 9", len(ops))
	}
	if ops[0].Kind != "M" || ops[1].Kind != "Q" || ops[2].Kind != "C" || ops[4].Kind != "Z" || ops[5].Kind != "M" {
		t.Fatalf("verb sequence wrong: %v", ops)
	}
	// Lowercase q/c resolve controls and endpoints against the running current
	// point; spell the same square-with-curve contour relatively and expect the
	// same absolute ops as the uppercase text above.
	rel, err := ParseSVGPath("m0 0 q5 -2 10 0 c2 4 -2 12 0 10 l-10 0 z")
	if err != nil {
		t.Fatalf("relative curves: %v", err)
	}
	wantRel := []Op{
		{Kind: "M", Points: []Point{{0, 0}}},
		{Kind: "Q", Points: []Point{{5, -2}, {10, 0}}},
		{Kind: "C", Points: []Point{{12, 4}, {8, 12}, {10, 10}}},
		{Kind: "L", Points: []Point{{0, 10}}},
		{Kind: "Z"},
	}
	if !opsEqual(rel, wantRel) {
		t.Fatalf("relative curve mismatch:\n got %v\nwant %v", rel, wantRel)
	}
	// The absolute text must equal the resolved relative contour.
	if !opsEqual(ops[:5], wantRel) {
		t.Fatalf("absolute/relative contours differ:\n got %v\nwant %v", ops[:5], wantRel)
	}
}

func TestParseSVGPathRejectsUnsupportedVerbs(t *testing.T) {
	bad := []string{
		"M0 0 H10 L10 10 L0 10 Z",     // H is outside M/L/Q/C/Z
		"M0 0 A10 10 0 0 1 10 10 Z",   // arc unsupported
		"M0 0 L10 0 L10 10 L0 10 S",   // S unsupported
		"M0 0 L10 0 L10 10 L0 10",     // no Z
		"L0 0 L1 1 L2 2 Z",            // must begin with M
		"M0 0 L10 0 Z",                // only two vertices
		"M0 0 M1 1 L2 2 L3 3 Z",       // second M before Z
		"M0 0 L10 0 L10 10 L0 10 Z X", // trailing non-grammar
		"M0 0 L. 0 L10 10 L0 10 Z",    // bare decimal point
		"M0 0 LNaN 0 L10 10 L0 10 Z",  // NaN is not a grammar number
		"M0 0 L1e 0 L10 10 L0 10 Z",   // broken exponent
		"M0 0 L10 0 L10 10 L0 10 ZZ",  // double Z
	}
	for _, b := range bad {
		if _, err := ParseSVGPath(b); err == nil {
			t.Errorf("expected rejection for %q", b)
		}
	}
}

func TestParseSVGPathErrorLocation(t *testing.T) {
	_, err := ParseSVGPath("M0 0 H10 L10 10 Z")
	var pe *ParseError
	if !errors.As(err, &pe) {
		t.Fatalf("want *ParseError, got %T: %v", err, err)
	}
	// 'H' is at byte offset 5, i.e. column 6.
	if pe.Offset != 5 || pe.Column != 6 {
		t.Fatalf("location = offset %d col %d, want 5/6: %v", pe.Offset, pe.Column, err)
	}
	_, err = ParseSVGPath("M0 0 L. 0 L10 10 L0 10 Z")
	if !errors.As(err, &pe) {
		t.Fatalf("want *ParseError, got %T", err)
	}
	if pe.Offset != 6 {
		t.Fatalf("number error offset = %d, want 6: %v", pe.Offset, err)
	}
}

func TestParseSVGPathFlattenEqualsLegacy(t *testing.T) {
	// The exact amber-proof shape contour, as text, must flatten to the same
	// ring the legacy op array produces.
	legacy := []Op{
		{Kind: "M", Points: []Point{{14, 110}}},
		{Kind: "C", Points: []Point{{32, -10}, {154, 0}, {178, 110}}},
		{Kind: "L", Points: []Point{{14, 110}}},
		{Kind: "Z"},
	}
	text := SVGPathText(legacy)
	parsed, err := ParseSVGPath(text)
	if err != nil {
		t.Fatalf("parse canonical text: %v", err)
	}
	if !opsEqual(parsed, legacy) {
		t.Fatalf("round trip changed ops:\n got %v\nwant %v", parsed, legacy)
	}
	a := Flatten(legacy)
	b := Flatten(parsed)
	if len(a) != len(b) {
		t.Fatalf("ring count %d vs %d", len(a), len(b))
	}
	for i := range a {
		if len(a[i]) != len(b[i]) {
			t.Fatalf("ring %d vertices %d vs %d", i, len(a[i]), len(b[i]))
		}
		for j := range a[i] {
			if a[i][j] != b[i][j] {
				t.Fatalf("ring %d vertex %d: %v vs %v", i, j, a[i][j], b[i][j])
			}
		}
	}
}

func TestParseSVGPathFillSemanticsPreserved(t *testing.T) {
	// Outer ring plus inner ring; even-odd must keep the inner ring a hole
	// regardless of which spelling supplied the contour.
	legacy := []Op{
		{Kind: "M", Points: []Point{{0, 0}}},
		{Kind: "L", Points: []Point{{100, 0}}},
		{Kind: "L", Points: []Point{{100, 100}}},
		{Kind: "L", Points: []Point{{0, 100}}},
		{Kind: "Z"},
		{Kind: "M", Points: []Point{{25, 25}}},
		{Kind: "L", Points: []Point{{75, 25}}},
		{Kind: "L", Points: []Point{{75, 75}}},
		{Kind: "L", Points: []Point{{25, 75}}},
		{Kind: "Z"},
	}
	text := "m0 0 l100 0 l0 100 l-100 0 z m25 25 l50 0 l0 50 l-50 0 z"
	parsed, err := ParseSVGPath(text)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if !opsEqual(parsed, legacy) {
		t.Fatalf("multi-subpath mismatch")
	}
	p := Flatten(parsed)
	if !p.Contains(Point{X: 50, Y: 10}, "evenodd") {
		t.Error("outer point should be inside")
	}
	if p.Contains(Point{X: 50, Y: 50}, "evenodd") {
		t.Error("inner ring must remain a hole under evenodd")
	}
	// Nonzero on same-direction rings fills the interior; orientation is
	// preserved because we never reorder or rewrite vertices.
	if !p.Contains(Point{X: 50, Y: 50}, "nonzero") {
		t.Error("nonzero should fill same-orientation interior")
	}
}

func TestParseSVGPathFiniteBound(t *testing.T) {
	if _, err := ParseSVGPath("M0 0 L1e400 0 L10 10 L0 10 Z"); err == nil {
		t.Fatal("infinity from exponent overflow must be rejected")
	}
	if _, err := ParseSVGPath("M0 0 L-1e400 0 L10 10 L0 10 Z"); err == nil {
		t.Fatal("-infinity must be rejected")
	}
	long := "M0 0 " + stringRepeat("L1 1 ", 300) + "Z"
	if _, err := ParseSVGPath(long); err == nil {
		t.Fatal("contour over 256 ops must be rejected")
	}
}

func TestSVGPathTextCanonical(t *testing.T) {
	ops := []Op{
		{Kind: "M", Points: []Point{{1.5, -2.25}}},
		{Kind: "L", Points: []Point{{math.Pi, 0}}},
		{Kind: "L", Points: []Point{{0, math.Pi}}},
		{Kind: "Z"},
	}
	text := SVGPathText(ops)
	// Canonical spelling: absolute verbs, spaces, %.9g, explicit Z.
	if text != "M 1.5 -2.25 L 3.14159265 0 L 0 3.14159265 Z" {
		t.Fatalf("canonical text = %q", text)
	}
	back, err := ParseSVGPath(text)
	if err != nil {
		t.Fatalf("reparse: %v", err)
	}
	if len(back) != len(ops) || back[0].Points[0] != ops[0].Points[0] ||
		back[1].Points[0].X != 3.14159265 || back[2].Points[0].Y != 3.14159265 {
		t.Fatalf("canonical round trip mismatch: %v", back)
	}
}

func stringRepeat(s string, n int) string {
	out := make([]byte, 0, len(s)*n)
	for i := 0; i < n; i++ {
		out = append(out, s...)
	}
	return string(out)
}

func opsEqual(a, b []Op) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i].Kind != b[i].Kind || len(a[i].Points) != len(b[i].Points) {
			return false
		}
		for j := range a[i].Points {
			if a[i].Points[j] != b[i].Points[j] {
				return false
			}
		}
	}
	return true
}

func (o Op) String() string {
	pts := ""
	for _, p := range o.Points {
		pts += " (" + floatStr(p.X) + "," + floatStr(p.Y) + ")"
	}
	return o.Kind + pts
}

func floatStr(x float64) string {
	return formatNumber(x)
}
