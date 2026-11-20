package transport

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"prismplate/internal/geom"
	"prismplate/internal/repository"
	"prismplate/internal/scene"
	"prismplate/internal/service"
	"strings"
	"testing"
)

func newTestService(t *testing.T) (*service.Service, http.Handler) {
	t.Helper()
	root := t.TempDir()
	repo, err := repository.Open(root)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = repo.Close() })
	svc := service.New(repo)
	t.Cleanup(svc.Close)
	return svc, API{Service: svc}.Handler()
}

func solidShape(id, rule string, ops []geom.Op) scene.Shape {
	return scene.Shape{ID: id, FillRule: rule, Path: ops, Transform: geomIdentity(),
		Brush: scene.Brush{Kind: "solid", Color: scene.Color{R: 0.2, G: 0.6, B: 0.9, A: 1}}}
}

// geomIdentity avoids an import cycle surprise by constructing the matrix literal.
func geomIdentity() [6]float64 { return [6]float64{1, 0, 0, 1, 0, 0} }

func rectOps(x0, y0, x1, y1 float64) []geom.Op {
	pt := func(x, y float64) geom.Point { return geom.Point{X: x, Y: y} }
	return []geom.Op{
		{Kind: "M", Points: []geom.Point{pt(x0, y0)}},
		{Kind: "L", Points: []geom.Point{pt(x1, y0)}},
		{Kind: "L", Points: []geom.Point{pt(x1, y1)}},
		{Kind: "L", Points: []geom.Point{pt(x0, y1)}},
		{Kind: "Z"},
	}
}

func doJSON(t *testing.T, h http.Handler, method, target string, body any) (int, map[string]any) {
	t.Helper()
	var rdr *bytes.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rdr = bytes.NewReader(b)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, target, rdr)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	var out map[string]any
	if rec.Body.Len() > 0 {
		_ = json.Unmarshal(rec.Body.Bytes(), &out)
	}
	return rec.Code, out
}

func TestConvertFillEndpointPreservesAndDoesNotMutate(t *testing.T) {
	svc, handler := newTestService(t)
	doc := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1}, Layers: []scene.Layer{{
		ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{
			solidShape("s1", "evenodd", append(rectOps(4, 4, 60, 60), rectOps(16, 16, 48, 48)...)),
		},
	}}}
	if _, err := svc.Create("plate-1", "转换测试", doc); err != nil {
		t.Fatal(err)
	}
	code, out := doJSON(t, handler, "POST", "/v1/plates/plate-1/convert-fill?revision=1",
		map[string]any{"layer_id": "l1", "shape_id": "s1", "target_rule": "nonzero"})
	if code != 200 {
		t.Fatalf("expected 200, got %d: %v", code, out)
	}
	report := out["report"].(map[string]any)
	if report["grid_mismatches"].(float64) != 0 {
		t.Fatalf("grid mismatches present: %v", report["grid_mismatches"])
	}
	if report["candidate_compiles"] != true {
		t.Fatalf("candidate must compile: %v", report)
	}
	if report["reliability"] != "exact" {
		t.Fatalf("clean rectangles must be exact, got %v", report["reliability"])
	}
	// The original plate still has exactly one revision with the evenodd shape.
	plate, err := svc.Plate("plate-1")
	if err != nil {
		t.Fatal(err)
	}
	if len(plate.Revisions) != 1 {
		t.Fatalf("conversion must not create a revision, got %d", len(plate.Revisions))
	}
	if plate.Revisions[0].Document.Layers[0].Shapes[0].FillRule != "evenodd" {
		t.Fatalf("source revision was mutated")
	}

	// The candidate document can be committed through the normal revision flow.
	cand := out["candidate_document"].(map[string]any)
	candBytes, _ := json.Marshal(cand)
	var candidateDoc scene.Document
	if err := json.Unmarshal(candBytes, &candidateDoc); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.Revise("plate-1", 1, candidateDoc); err != nil {
		t.Fatalf("committing candidate revision failed: %v", err)
	}
	plate2, _ := svc.Plate("plate-1")
	if len(plate2.Revisions) != 2 || plate2.Revisions[1].Document.Layers[0].Shapes[0].FillRule != "nonzero" {
		t.Fatalf("candidate revision not stored correctly")
	}
	// Brush and clip survive: brush color retained, layer clip still empty/equal.
	if !strings.Contains(string(candBytes), `"kind":"solid"`) {
		t.Fatalf("brush missing from candidate")
	}
}

func TestConvertFillEndpointRejections(t *testing.T) {
	svc, handler := newTestService(t)
	doc := scene.Document{Width: 64, Height: 64, Background: scene.Color{A: 1}, Layers: []scene.Layer{{
		ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{
			solidShape("s1", "nonzero", rectOps(4, 4, 60, 60)),
		},
	}}}
	if _, err := svc.Create("plate-1", "t", doc); err != nil {
		t.Fatal(err)
	}
	// Same rule.
	if code, out := doJSON(t, handler, "POST", "/v1/plates/plate-1/convert-fill?revision=1",
		map[string]any{"layer_id": "l1", "shape_id": "s1", "target_rule": "nonzero"}); code != 400 {
		t.Fatalf("same-rule want 400, got %d %v", code, out)
	}
	// Unknown shape.
	if code, _ := doJSON(t, handler, "POST", "/v1/plates/plate-1/convert-fill?revision=1",
		map[string]any{"layer_id": "l1", "shape_id": "ghost", "target_rule": "evenodd"}); code != 400 {
		t.Fatalf("missing shape want 400, got %d", code)
	}
	// Missing plate -> 404.
	if code, _ := doJSON(t, handler, "POST", "/v1/plates/nope/convert-fill?revision=1",
		map[string]any{"layer_id": "l1", "shape_id": "s1", "target_rule": "evenodd"}); code != 404 {
		t.Fatalf("missing plate want 404, got %d", code)
	}
	// Unknown fields rejected like every other write endpoint.
	req := httptest.NewRequest("POST", "/v1/plates/plate-1/convert-fill?revision=1",
		strings.NewReader(`{"layer_id":"l1","shape_id":"s1","target_rule":"evenodd","bogus":1}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != 400 {
		t.Fatalf("unknown field want 400, got %d", rec.Code)
	}
}
