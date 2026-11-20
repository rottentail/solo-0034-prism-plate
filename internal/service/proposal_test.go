package service

import (
	"context"
	"prismplate/internal/corridor"
	"prismplate/internal/geom"
	"prismplate/internal/repository"
	"prismplate/internal/scene"
	"testing"
)

func newTestService(t *testing.T) *Service {
	t.Helper()
	repo, err := repository.Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { repo.Close() })
	return New(repo)
}

func shape(id string, ops []geom.Op) scene.Shape {
	return scene.Shape{ID: id, Path: ops, FillRule: "nonzero",
		Brush:     scene.Brush{Kind: "solid", Color: scene.Color{R: 0.1, G: 0.1, B: 0.1, A: 1}},
		Transform: geom.Identity}
}

func rect(x0, y0, x1, y1 float64) []geom.Op {
	return []geom.Op{
		{Kind: "M", Points: []geom.Point{{X: x0, Y: y0}}},
		{Kind: "L", Points: []geom.Point{{X: x1, Y: y0}}},
		{Kind: "L", Points: []geom.Point{{X: x1, Y: y1}}},
		{Kind: "L", Points: []geom.Point{{X: x0, Y: y1}}},
		{Kind: "Z"},
	}
}

func gapDocument() scene.Document {
	return scene.Document{Width: 100, Height: 100, Background: scene.Color{A: 1},
		Layers: []scene.Layer{{ID: "l1", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{
			shape("left", rect(10, 10, 40, 90)),
			shape("right", rect(60, 10, 90, 90)),
		}}}}
}

func channel() corridor.Channel {
	return corridor.Channel{ID: "gap", MinWidth: 10,
		Centerline: []geom.Point{{X: 50, Y: 15}, {X: 50, Y: 85}}}
}

func TestProposalPreviewMatchesCommittedRevision(t *testing.T) {
	s := newTestService(t)
	if _, err := s.Create("plate-1", "t", gapDocument()); err != nil {
		t.Fatal(err)
	}
	moves := []corridor.Move{
		{ShapeID: "left", From: geom.Point{X: 40, Y: 10}, To: geom.Point{X: 44, Y: 10}},
		{ShapeID: "left", From: geom.Point{X: 40, Y: 90}, To: geom.Point{X: 44, Y: 90}},
	}
	rec, err := s.ProposeChannel(context.Background(), "plate-1", 1, moves, []corridor.Channel{channel()})
	if err != nil {
		t.Fatal(err)
	}
	if !rec.Feasible {
		t.Fatal("small move should be fully feasible")
	}
	cand := rec.Candidates[len(rec.Candidates)-1]
	if cand.SceneDigest != repository.SceneHash(cand.Document) {
		t.Fatal("candidate digest mismatch")
	}
	// The preview is a transient render of the candidate with a content digest.
	if len(cand.PreviewPNG.Data) == 0 || cand.PreviewPNG.Digest != repository.Hash(cand.PreviewPNG.Data) {
		t.Fatal("preview png missing or digest mismatch")
	}
	if fetched, err := s.PreviewBytes(rec.ID, len(rec.Candidates)-1, "png"); err != nil || string(fetched.Data) != string(cand.PreviewPNG.Data) {
		t.Fatalf("preview fetch mismatch: %v", err)
	}
	plate, _, err := s.AcceptProposal(rec.ID, len(rec.Candidates)-1)
	if err != nil {
		t.Fatal(err)
	}
	if len(plate.Revisions) != 2 {
		t.Fatalf("revisions = %d, want 2", len(plate.Revisions))
	}
	committed := plate.Revisions[1]
	if committed.Digest != cand.SceneDigest {
		t.Fatal("committed revision must render the exact previewed document")
	}
	// Re-rendering the committed revision through the canonical path must
	// reproduce the preview bytes exactly.
	rendered, err := s.Render(context.Background(), "plate-1", 2, "png")
	if err != nil {
		t.Fatal(err)
	}
	if rendered.Digest != cand.PreviewPNG.Digest {
		t.Fatal("committed render digest differs from preview digest")
	}
}

func TestInfeasibleProposalKeepsOriginalAndReportsLimit(t *testing.T) {
	s := newTestService(t)
	if _, err := s.Create("plate-2", "t", gapDocument()); err != nil {
		t.Fatal(err)
	}
	// Full intent closes the 20-wide gap entirely.
	moves := []corridor.Move{
		{ShapeID: "left", From: geom.Point{X: 40, Y: 10}, To: geom.Point{X: 70, Y: 10}},
		{ShapeID: "left", From: geom.Point{X: 40, Y: 90}, To: geom.Point{X: 70, Y: 90}},
	}
	rec, err := s.ProposeChannel(context.Background(), "plate-2", 1, moves, []corridor.Channel{channel()})
	if err != nil {
		t.Fatal(err)
	}
	if rec.Feasible || rec.Limit == nil {
		t.Fatal("expected infeasible proposal with a limit report")
	}
	if rec.BoundaryAlpha >= 1 {
		t.Fatal("boundary must stay below full intent")
	}
	for _, c := range rec.Candidates {
		if !c.Report.Passes {
			t.Fatal("every offered candidate must satisfy the channel")
		}
	}
	// History is untouched: no revision was appended.
	plate, _ := s.Plate("plate-2")
	if len(plate.Revisions) != 1 {
		t.Fatal("infeasible proposal must not append a revision")
	}
}

func TestStaleExpectedRevisionRejectsAcceptance(t *testing.T) {
	s := newTestService(t)
	if _, err := s.Create("plate-3", "t", gapDocument()); err != nil {
		t.Fatal(err)
	}
	moves := []corridor.Move{
		{ShapeID: "left", From: geom.Point{X: 40, Y: 10}, To: geom.Point{X: 42, Y: 10}},
		{ShapeID: "left", From: geom.Point{X: 40, Y: 90}, To: geom.Point{X: 42, Y: 90}},
	}
	rec, err := s.ProposeChannel(context.Background(), "plate-3", 1, moves, []corridor.Channel{channel()})
	if err != nil {
		t.Fatal(err)
	}
	// A normal revision lands first, moving the latest revision past the
	// proposal's expected_revision.
	if _, err := s.Revise("plate-3", 1, gapDocument()); err != nil {
		t.Fatal(err)
	}
	if _, _, err := s.AcceptProposal(rec.ID, 0); err != repository.ErrConflict {
		t.Fatalf("want ErrConflict, got %v", err)
	}
}

func TestBaselineViolationReturnsErrorWithoutProposal(t *testing.T) {
	s := newTestService(t)
	if _, err := s.Create("plate-4", "t", gapDocument()); err != nil {
		t.Fatal(err)
	}
	tooWide := corridor.Channel{ID: "wide", MinWidth: 40,
		Centerline: []geom.Point{{X: 50, Y: 20}, {X: 50, Y: 80}}}
	moves := []corridor.Move{
		{ShapeID: "left", From: geom.Point{X: 10, Y: 10}, To: geom.Point{X: 9, Y: 10}},
	}
	if _, err := s.ProposeChannel(context.Background(), "plate-4", 1, moves, []corridor.Channel{tooWide}); err == nil {
		t.Fatal("baseline channel violation must be rejected")
	}
	plate, _ := s.Plate("plate-4")
	if len(plate.Revisions) != 1 {
		t.Fatal("rejected proposal must leave the original revision untouched")
	}
}

func TestProposalIsTransientAndUnknownRejected(t *testing.T) {
	s := newTestService(t)
	if _, err := s.Create("plate-5", "t", gapDocument()); err != nil {
		t.Fatal(err)
	}
	if _, err := s.GetProposal("cp-missing"); err != repository.ErrMissing {
		t.Fatalf("want ErrMissing, got %v", err)
	}
	if _, _, err := s.AcceptProposal("cp-missing", 0); err != repository.ErrMissing {
		t.Fatalf("want ErrMissing, got %v", err)
	}
}
