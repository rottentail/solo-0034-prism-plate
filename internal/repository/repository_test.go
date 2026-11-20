package repository

import (
	"encoding/json"
	"prismplate/internal/scene"
	"testing"
)

func simpleDoc(w, h int) scene.Document {
	return scene.Document{Width: w, Height: h, Background: scene.Color{A: 1}, Layers: []scene.Layer{{
		ID: "sheet", Opacity: 1, Blend: "normal",
		Shapes: []scene.Shape{{
			ID: "box", FillRule: "nonzero", Transform: [6]float64{1, 0, 0, 1, 0, 0},
			Path:  nil,
			Brush: scene.Brush{Kind: "solid", Color: scene.Color{R: 1, A: 1}},
		}},
	}}}
}

func plateWith(revisions ...Revision) State {
	s := Fresh()
	s.Plates["p"] = Plate{ID: "p", Title: "t", State: "active", Revisions: revisions}
	return s
}

func rev(n int, d scene.Document, origin *Origin) Revision {
	return Revision{Number: n, Digest: SceneHash(d), Document: d, Origin: origin}
}

// Snapshots and bundles written before Origin existed carry no origin field
// and must keep validating unchanged.
func TestLegacyRevisionWithoutOrigin(t *testing.T) {
	d := simpleDoc(10, 10)
	raw := []byte(`{"number":1,"digest":"` + SceneHash(d) + `","document":` + mustJSON(d) + `,"created":"2024-01-01T00:00:00Z"}`)
	var r Revision
	if err := json.Unmarshal(raw, &r); err != nil {
		t.Fatal(err)
	}
	if r.Origin != nil {
		t.Fatal("absent origin must stay nil")
	}
	if err := Check(plateWith(r)); err != nil {
		t.Fatalf("legacy revision rejected: %v", err)
	}
}

func TestValidOriginAccepted(t *testing.T) {
	src := simpleDoc(20, 10)
	dst := simpleDoc(10, 10)
	o := &Origin{Kind: "reframe", SourceRevision: 1, SourceDigest: SceneHash(src), Mode: "fit", TargetWidth: 10, TargetHeight: 10, Scale: 0.5, PreviewDigest: SceneHash(dst)}
	if err := Check(plateWith(rev(1, src, nil), rev(2, dst, o))); err != nil {
		t.Fatalf("valid origin rejected: %v", err)
	}
}

func TestInvalidOriginRejected(t *testing.T) {
	src := simpleDoc(20, 10)
	dst := simpleDoc(10, 10)
	cases := map[string]*Origin{
		"future source":     {Kind: "reframe", SourceRevision: 2, SourceDigest: SceneHash(src), Mode: "fit", TargetWidth: 10, TargetHeight: 10, PreviewDigest: SceneHash(dst)},
		"wrong source hash": {Kind: "reframe", SourceRevision: 1, SourceDigest: "x", Mode: "fit", TargetWidth: 10, TargetHeight: 10, PreviewDigest: SceneHash(dst)},
		"unknown mode":      {Kind: "reframe", SourceRevision: 1, SourceDigest: SceneHash(src), Mode: "stretch", TargetWidth: 10, TargetHeight: 10, PreviewDigest: SceneHash(dst)},
		"size mismatch":     {Kind: "reframe", SourceRevision: 1, SourceDigest: SceneHash(src), Mode: "fit", TargetWidth: 11, TargetHeight: 10, PreviewDigest: SceneHash(dst)},
		"preview mismatch":  {Kind: "reframe", SourceRevision: 1, SourceDigest: SceneHash(src), Mode: "fit", TargetWidth: 10, TargetHeight: 10, PreviewDigest: "y"},
		"wrong kind":        {Kind: "branch", SourceRevision: 1, SourceDigest: SceneHash(src), Mode: "fit", TargetWidth: 10, TargetHeight: 10, PreviewDigest: SceneHash(dst)},
	}
	for name, o := range cases {
		if err := Check(plateWith(rev(1, src, nil), rev(2, dst, o))); err == nil {
			t.Fatalf("%s: expected rejection", name)
		}
	}
}

// The scene digest is untouched by editorial provenance: adding origin must not
// change the digest of the very same document.
func TestOriginDoesNotAffectSceneHash(t *testing.T) {
	d := simpleDoc(10, 10)
	withOrigin := rev(1, d, &Origin{Kind: "reframe", SourceRevision: 1, SourceDigest: "s", Mode: "crop", TargetWidth: 10, TargetHeight: 10, PreviewDigest: SceneHash(d)})
	if withOrigin.Digest != SceneHash(d) {
		t.Fatal("origin leaked into the scene digest")
	}
}

func mustJSON(v any) string {
	b, _ := json.Marshal(v)
	return string(b)
}
