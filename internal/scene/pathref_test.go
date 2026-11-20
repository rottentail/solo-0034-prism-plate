package scene

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"prismplate/internal/geom"
)

const legacyShape = `{
  "id": "s1",
  "path": [
    {"kind":"M","points":[{"x":0,"y":0}]},
    {"kind":"L","points":[{"x":10,"y":0}]},
    {"kind":"L","points":[{"x":10,"y":10}]},
    {"kind":"L","points":[{"x":0,"y":10}]},
    {"kind":"Z"}
  ],
  "fill_rule": "nonzero",
  "brush": {"kind":"solid","color":{"r":1,"g":0,"b":0,"a":1}},
  "transform": [1,0,0,1,0,0]
}`

const svgShape = `{
  "id": "s1",
  "path": {"path_format":"svg-path-1","path_text":"M0 0 L10 0 L10 10 L0 10 Z"},
  "fill_rule": "nonzero",
  "brush": {"kind":"solid","color":{"r":1,"g":0,"b":0,"a":1}},
  "transform": [1,0,0,1,0,0]
}`

func TestShapeLegacyDecodeNormalizes(t *testing.T) {
	var s Shape
	if err := json.Unmarshal([]byte(legacyShape), &s); err != nil {
		t.Fatalf("legacy decode: %v", err)
	}
	if s.Path.Source != PathSourceLegacy {
		t.Fatalf("source = %q", s.Path.Source)
	}
	if len(s.Path.Ops) != 5 || s.Path.Ops[0].Kind != "M" {
		t.Fatalf("normalized ops wrong: %v", s.Path.Ops)
	}
}

func TestShapeSVGDecodeNormalizes(t *testing.T) {
	var s Shape
	if err := json.Unmarshal([]byte(svgShape), &s); err != nil {
		t.Fatalf("svg decode: %v", err)
	}
	if s.Path.Source != PathFormatSVG {
		t.Fatalf("source = %q", s.Path.Source)
	}
	if s.Path.Text != "M0 0 L10 0 L10 10 L0 10 Z" {
		t.Fatalf("text not retained: %q", s.Path.Text)
	}
	legacy := decodeShape(t, legacyShape)
	if len(s.Path.Ops) != len(legacy.Path.Ops) {
		t.Fatalf("normalized op count differs")
	}
	for i := range s.Path.Ops {
		if s.Path.Ops[i].Kind != legacy.Path.Ops[i].Kind {
			t.Fatalf("op %d kind %s vs %s", i, s.Path.Ops[i].Kind, legacy.Path.Ops[i].Kind)
		}
	}
}

func TestShapeBothRepresentationsRejected(t *testing.T) {
	// Unknown fields and dual representations must both fail admission at the
	// JSON layer rather than being silently ignored.
	both := `{
	  "id":"s1",
	  "path":{"path_format":"svg-path-1","path_text":"M0 0 L1 0 L1 1 L0 1 Z"},
	  "fill_rule":"nonzero",
	  "brush":{"kind":"solid","color":{"r":1,"g":0,"b":0,"a":1}},
	  "transform":[1,0,0,1,0,0]
	}`
	var s Shape
	if err := json.Unmarshal([]byte(both), &s); err != nil {
		// The versioned object is accepted at the Shape level because the
		// versioned object itself is a valid PathRef; verify dual-source
		// rejection through PathRef directly below.
	}
	_, err := decodePathRef(`{"path":[{"kind":"Z"}],"path_format":"svg-path-1","path_text":"M0 0 Z"}`)
	if err == nil || !strings.Contains(err.Error(), "mutually exclusive") {
		t.Fatalf("dual representation must be rejected, got %v", err)
	}
	if _, err := decodePathRef(`{"path_format":"svg-path-2","path_text":"M0 0 Z"}`); err == nil {
		t.Fatal("unknown path_format version must be rejected")
	}
	if _, err := decodePathRef(`{"path_format":"svg-path-1"}`); err == nil {
		t.Fatal("missing path_text must be rejected")
	}
	if _, err := decodePathRef(`{"path_text":"M0 0 L1 0 L1 1 L0 1 Z"}`); err == nil {
		t.Fatal("missing path_format must be rejected")
	}
	if _, err := decodePathRef(`{}`); err == nil {
		t.Fatal("empty contour object must be rejected")
	}
	if _, err := decodePathRef(`[]`); err == nil {
		t.Fatal("empty op array must be rejected")
	}
}

func TestShapeUnknownFieldsRejected(t *testing.T) {
	bad := strings.Replace(svgShape, `"id": "s1"`, `"id": "s1", "hax": 1`, 1)
	var s Shape
	if err := json.Unmarshal([]byte(bad), &s); err == nil {
		t.Fatal("unknown shape field must be rejected")
	}
	badPath := `{"path_format":"svg-path-1","path_text":"M0 0 L1 0 L1 1 L0 1 Z","onload":"x"}`
	if _, err := decodePathRef(badPath); err == nil {
		t.Fatal("unknown path object field must be rejected")
	}
}

func TestShapeSVGErrorCarriesLocation(t *testing.T) {
	bad := `{
	  "id":"s1",
	  "path":{"path_format":"svg-path-1","path_text":"M0 0 H10 L10 10 L0 10 Z"},
	  "fill_rule":"nonzero",
	  "brush":{"kind":"solid","color":{"r":1,"g":0,"b":0,"a":1}},
	  "transform":[1,0,0,1,0,0]
	}`
	var s Shape
	err := json.Unmarshal([]byte(bad), &s)
	if err == nil {
		t.Fatal("H verb must be rejected")
	}
	var pe *geom.ParseError
	if !errors.As(err, &pe) {
		t.Fatalf("ParseError must survive encoding/json wrapping, got %T: %v", err, err)
	}
	if pe.Column != 6 { // H at offset 5
		t.Fatalf("column = %d, want 6", pe.Column)
	}
}

func TestLegacyRoundTripBytes(t *testing.T) {
	s := decodeShape(t, legacyShape)
	out, err := json.Marshal(s)
	if err != nil {
		t.Fatal(err)
	}
	var again Shape
	if err := json.Unmarshal(out, &again); err != nil {
		t.Fatalf("re-decode: %v", err)
	}
	if again.Path.Source != PathSourceLegacy {
		t.Fatalf("legacy spelling must stay legacy, got %q", again.Path.Source)
	}
	// Marshal form stays an op array (no path_format key leaks into legacy).
	if strings.Contains(string(out), "path_format") {
		t.Fatalf("legacy marshaling leaked versioned fields: %s", out)
	}
}

func TestSVGRoundTripStaysVersioned(t *testing.T) {
	s := decodeShape(t, svgShape)
	out, err := json.Marshal(s)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(out), `"path_format":"svg-path-1"`) {
		t.Fatalf("versioned spelling lost on marshal: %s", out)
	}
	var again Shape
	if err := json.Unmarshal(out, &again); err != nil {
		t.Fatalf("re-decode: %v", err)
	}
	if again.Path.Source != PathFormatSVG || again.Path.Text != s.Path.Text {
		t.Fatalf("versioned round trip changed: %+v", again.Path)
	}
}

func TestLayerClipMaskTwoSpellings(t *testing.T) {
	layerJSON := `{
	  "id":"L1","opacity":1,"blend":"normal",
	  "clip":{"path_format":"svg-path-1","path_text":"M0 0 L8 0 L8 8 L0 8 Z"},
	  "mask":[{"kind":"M","points":[{"x":1,"y":1}]},{"kind":"L","points":[{"x":2,"y":1}]},{"kind":"L","points":[{"x":2,"y":2}]},{"kind":"L","points":[{"x":1,"y":2}]},{"kind":"Z"}],
	  "shapes":[` + legacyShape + `]
	}`
	var l Layer
	if err := json.Unmarshal([]byte(layerJSON), &l); err != nil {
		t.Fatalf("layer decode: %v", err)
	}
	if l.Clip == nil || l.Clip.Source != PathFormatSVG {
		t.Fatal("clip versioned source lost")
	}
	if l.Mask == nil || l.Mask.Source != PathSourceLegacy {
		t.Fatal("mask legacy source lost")
	}
	out, err := json.Marshal(l)
	if err != nil {
		t.Fatal(err)
	}
	var again Layer
	if err := json.Unmarshal(out, &again); err != nil {
		t.Fatalf("layer round trip: %v", err)
	}
	if again.Clip.Text != l.Clip.Text || len(again.Mask.Ops) != 5 {
		t.Fatal("layer contour round trip changed")
	}
	// A layer without clip/mask must still omit those keys.
	plain, err := json.Marshal(Layer{ID: "x", Shapes: []Shape{decodeShape(t, legacyShape)}})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(plain), "clip") || strings.Contains(string(plain), "mask") {
		t.Fatalf("optional contours leaked: %s", plain)
	}
}

func TestContourSummaryProvenance(t *testing.T) {
	d := Document{
		Width: 16, Height: 16,
		Layers: []Layer{
			{ID: "L1", Clip: ptr(decodePathRefOrDie(t, `{"path_format":"svg-path-1","path_text":"M0 0 L8 0 L8 8 L0 8 Z"}`)), Shapes: []Shape{decodeShape(t, svgShape)}},
		},
	}
	sums := ContourSummary(d)
	if len(sums) != 2 {
		t.Fatalf("summary count = %d", len(sums))
	}
	if sums[0].Role != "clip" || sums[0].Source != PathFormatSVG || sums[0].Subpaths != 1 || sums[0].TextBytes == 0 {
		t.Fatalf("clip summary wrong: %+v", sums[0])
	}
	if sums[1].Role != "path" || sums[1].Node != "L1/s1" || sums[1].Source != PathFormatSVG {
		t.Fatalf("shape summary wrong: %+v", sums[1])
	}
}

func decodeShape(t *testing.T, b string) Shape {
	t.Helper()
	var s Shape
	if err := json.Unmarshal([]byte(b), &s); err != nil {
		t.Fatalf("decode shape: %v", err)
	}
	return s
}

func decodePathRef(b string) (PathRef, error) {
	var p PathRef
	err := json.Unmarshal([]byte(b), &p)
	return p, err
}

func decodePathRefOrDie(t *testing.T, b string) PathRef {
	t.Helper()
	p, err := decodePathRef(b)
	if err != nil {
		t.Fatalf("decode PathRef: %v", err)
	}
	return p
}

func ptr(p PathRef) *PathRef { return &p }
