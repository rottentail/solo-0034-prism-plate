package repository

import (
	"encoding/json"
	"testing"

	"prismplate/internal/scene"
)

func TestSceneHashRepresentationIndependent(t *testing.T) {
	doc := documentFixture()
	legacy := doc
	svg := doc
	svg.Layers[0].Shapes[0].Path = scene.PathRef{
		Source: scene.PathFormatSVG,
		Text:   "M0 0 L10 0 L10 10 L0 10 Z",
		Ops:    legacy.Layers[0].Shapes[0].Path.Ops,
	}
	if SceneHash(legacy) != SceneHash(svg) {
		t.Fatal("digest differs by representation")
	}
	// Normalized marshaling uses the op-array spelling.
	b, _ := json.Marshal(NormalizedDocument(svg))
	if !contains(string(b), `"path":[`) {
		t.Fatalf("normalized document lost op array: %s", b)
	}
	if contains(string(b), "path_format") {
		t.Fatalf("normalized document leaked svg spelling: %s", b)
	}
	// Input must not be mutated by normalization.
	if svg.Layers[0].Shapes[0].Path.Source != scene.PathFormatSVG {
		t.Fatal("normalization mutated the source document")
	}
}

func TestLegacyPlateMarshalStable(t *testing.T) {
	doc := documentFixture()
	plate := Plate{
		ID: "amber-plate", Title: "t", State: "active",
		Revisions: []Revision{{Number: 1, Digest: SceneHash(doc), Document: doc}},
	}
	b, err := json.Marshal(plate)
	if err != nil {
		t.Fatal(err)
	}
	s := string(b)
	// The historical wire form: path is an array, no path_format/path_sources.
	if !contains(s, `"path":[{"kind":"M"`) {
		t.Fatalf("legacy plate marshal changed shape: %s", s[:300])
	}
	if contains(s, "path_format") || contains(s, "path_sources") {
		t.Fatalf("new fields leaked into plate marshal: %s", s[:400])
	}
}

func TestNormalizedDocumentClipMaskAndBundle(t *testing.T) {
	doc := documentFixture()
	clipRef := scene.PathRef{Source: scene.PathFormatSVG, Text: "M0 0 L8 0 L8 8 L0 8 Z",
		Ops: doc.Layers[0].Shapes[0].Path.Ops}
	doc.Layers[0].Clip = &clipRef
	legacy := doc
	svg := doc
	svg.Layers[0].Shapes[0].Path = scene.PathRef{
		Source: scene.PathFormatSVG, Text: "M0 0 L10 0 L10 10 L0 10 Z",
		Ops: legacy.Layers[0].Shapes[0].Path.Ops,
	}
	if SceneHash(legacy) != SceneHash(svg) {
		t.Fatal("digest differs with clip and mixed representations")
	}
	norm := NormalizedDocument(svg)
	if norm.Layers[0].Clip == nil || norm.Layers[0].Clip.Source != scene.PathSourceLegacy {
		t.Fatal("clip not normalized")
	}
	if norm.Layers[0].Clip == svg.Layers[0].Clip {
		t.Fatal("normalized clip aliases source pointer")
	}
	if svg.Layers[0].Clip.Source != scene.PathFormatSVG {
		t.Fatal("source clip mutated")
	}
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (indexOf(s, sub) >= 0)
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}
