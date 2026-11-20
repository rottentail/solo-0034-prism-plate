package geom

import (
	"math"
	"testing"
)

func TestPathBoundsIncludesHoleRings(t *testing.T) {
	// Outer ring and an inner (hole) ring: the bound is the union, so the hole
	// can never shrink the conservative box.
	p := Path{
		{{X: 0, Y: 0}, {X: 100, Y: 0}, {X: 100, Y: 100}, {X: 0, Y: 100}},
		{{X: 40, Y: 40}, {X: 60, Y: 40}, {X: 60, Y: 60}, {X: 40, Y: 60}},
	}
	b, ok := p.Bounds()
	if !ok {
		t.Fatal("expected bounds for non-empty path")
	}
	if b != (BBox{MinX: 0, MinY: 0, MaxX: 100, MaxY: 100}) {
		t.Fatalf("union bounds wrong: %+v", b)
	}
	if _, ok := (Path{}).Bounds(); ok {
		t.Fatal("empty path has no bounds")
	}
}

func TestTransformBoundsIsExactForRotationAndShear(t *testing.T) {
	// A 90-degree rotation maps the 10x20 box onto an exact 20x10 box.
	rot := Matrix{0, 1, -1, 0, 0, 0}
	b := rot.TransformBounds(BBox{MinX: 0, MinY: 0, MaxX: 10, MaxY: 20})
	if math.Abs(b.MinX+20) > 1e-12 || b.MinY != 0 || math.Abs(b.MaxX) > 1e-12 || b.MaxY != 10 {
		t.Fatalf("rotation bounds wrong: %+v", b)
	}
	// Shear plus translation: corners carry the envelope, no rough clipping.
	// Apply maps (x,y) -> (x+y+5, y-7), so x spans 5..25 and y spans -7..3.
	shear := Matrix{1, 0, 1, 1, 5, -7}
	b = shear.TransformBounds(BBox{MinX: 0, MinY: 0, MaxX: 10, MaxY: 10})
	want := BBox{MinX: 5, MinY: -7, MaxX: 25, MaxY: 3}
	if b != want {
		t.Fatalf("shear bounds wrong: got %+v want %+v", b, want)
	}
}

func TestOutsideViewConservativeBoundary(t *testing.T) {
	const w, h = 100, 80
	cases := []struct {
		name string
		b    BBox
		want bool
	}{
		{"inside", BBox{10, 10, 20, 20}, false},
		{"spanning whole canvas", BBox{-10, -10, 110, 90}, false},
		{"touching left edge", BBox{-5, 10, 0, 20}, false},
		{"touching right edge", BBox{100, 10, 110, 20}, false},
		{"overlapping by a sliver", BBox{99.999, 10, 101, 20}, false},
		{"strictly right", BBox{120, 10, 140, 20}, true},
		{"strictly above", BBox{10, -40, 20, -20}, true},
		{"strictly below", BBox{10, 90, 20, 100}, true},
		{"far away huge box", BBox{999000, 999000, 999999, 999999}, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			pad := 1e-9 + 1e-12*1e6
			if got := tc.b.OutsideView(w, h, pad); got != tc.want {
				t.Fatalf("OutsideView = %v, want %v", got, tc.want)
			}
		})
	}
	// Sub-pixel separation smaller than the margin is treated as visible: near
	// boundary geometry must never be rough-clipped.
	near := BBox{MinX: -1e-10, MinY: 10, MaxX: -1e-11, MaxY: 20}
	if near.OutsideView(w, h, 1e-9) {
		t.Fatal("geometry within the safety margin must stay visible")
	}
}
