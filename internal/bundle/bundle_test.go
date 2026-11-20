package bundle

import (
	"encoding/json"
	"testing"
	"time"

	"prismplate/internal/geom"
	"prismplate/internal/repository"
	"prismplate/internal/scene"
)

func fixtureDocument() scene.Document {
	ops := []geom.Op{
		{Kind: "M", Points: []geom.Point{{X: 0, Y: 0}}},
		{Kind: "L", Points: []geom.Point{{X: 10, Y: 0}}},
		{Kind: "L", Points: []geom.Point{{X: 10, Y: 10}}},
		{Kind: "L", Points: []geom.Point{{X: 0, Y: 10}}},
		{Kind: "Z"},
	}
	return scene.Document{
		Width: 16, Height: 16,
		Background: scene.Color{R: 1, G: 1, B: 1, A: 1},
		Layers: []scene.Layer{{
			ID: "L1", Opacity: 1, Blend: "normal",
			Shapes: []scene.Shape{{
				ID:        "s1",
				Path:      scene.PathRef{Source: scene.PathSourceLegacy, Ops: ops},
				FillRule:  "nonzero",
				Brush:     scene.Brush{Kind: "solid", Color: scene.Color{R: 1, A: 1}},
				Transform: geom.Identity,
			}},
		}},
	}
}

func fixturePlate(source string) repository.Plate {
	doc := fixtureDocument()
	if source == scene.PathFormatSVG {
		doc.Layers[0].Shapes[0].Path = scene.PathRef{
			Source: scene.PathFormatSVG,
			Text:   "M0 0 L10 0 L10 10 L0 10 Z",
			Ops:    doc.Layers[0].Shapes[0].Path.Ops,
		}
	}
	return repository.Plate{
		ID: "p1", Title: "t", State: "active",
		Revisions: []repository.Revision{{
			Number: 1, Digest: repository.SceneHash(doc), Document: doc,
			Created: time.Date(2026, 11, 20, 12, 0, 0, 0, time.UTC),
		}},
	}
}

func TestSealOpenRoundTripBothSources(t *testing.T) {
	for _, source := range []string{scene.PathSourceLegacy, scene.PathFormatSVG} {
		plate := fixturePlate(source)
		raw, err := Seal(plate)
		if err != nil {
			t.Fatalf("seal %s: %v", source, err)
		}
		got, err := Open(raw)
		if err != nil {
			t.Fatalf("open %s: %v", source, err)
		}
		if got.ID != plate.ID || len(got.Revisions) != 1 {
			t.Fatalf("round trip changed plate: %+v", got)
		}
		if got.Revisions[0].Document.Layers[0].Shapes[0].Path.Source != source {
			t.Fatalf("%s spelling not preserved", source)
		}
	}
}

func TestLegacyEnvelopeDigestAlgorithmUnchanged(t *testing.T) {
	plate := fixturePlate(scene.PathSourceLegacy)
	raw, err := Seal(plate)
	if err != nil {
		t.Fatal(err)
	}
	var env Envelope
	if err := json.Unmarshal(raw, &env); err != nil {
		t.Fatal(err)
	}
	// The envelope digest must equal the historical hash of the stored plate.
	plateBytes, _ := json.Marshal(plate)
	if env.Digest != repository.Hash(plateBytes) {
		t.Fatal("legacy envelope digest algorithm changed")
	}
}

func TestTamperedEnvelopeRejected(t *testing.T) {
	raw, err := Seal(fixturePlate(scene.PathSourceLegacy))
	if err != nil {
		t.Fatal(err)
	}
	var env Envelope
	if err := json.Unmarshal(raw, &env); err != nil {
		t.Fatal(err)
	}
	env.Digest = "0000000000000000000000000000000000000000000000000000000000000000"
	tampered, _ := json.Marshal(env)
	if _, err := Open(tampered); err == nil {
		t.Fatal("tampered envelope must be rejected")
	}
}

func TestNormalizedDigestAcceptsRepresentationRewrite(t *testing.T) {
	// A compliant tool rewrites the path spelling but keeps the geometry; the
	// raw digest changes but the normalized digest still verifies.
	legacy := fixturePlate(scene.PathSourceLegacy)
	rewritten := fixturePlate(scene.PathFormatSVG)
	env := Envelope{
		Schema: "prism-plate-bundle-v1",
		Digest: normalizedDigest(rewritten),
		Plate:  rewritten,
	}
	raw, _ := json.Marshal(env)
	if _, err := Open(raw); err != nil {
		t.Fatalf("normalized-verified envelope rejected: %v", err)
	}
	// The normalized digests of both spellings coincide even though the raw
	// plate bytes (and thus raw seal digests) differ.
	if normalizedDigest(legacy) != normalizedDigest(rewritten) {
		t.Fatal("normalized digest depends on representation")
	}
	if sealDigest(legacy) == sealDigest(rewritten) {
		t.Fatal("raw seal digest unexpectedly ignored the spelling difference")
	}
}
