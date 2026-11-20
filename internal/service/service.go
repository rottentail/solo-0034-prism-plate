// Package service joins revision facts, render computation and publication rights.
package service

import (
	"context"
	"fmt"
	"prismplate/internal/bundle"
	"prismplate/internal/exposure"
	"prismplate/internal/geom"
	"prismplate/internal/raster"
	"prismplate/internal/repository"
	"prismplate/internal/sanitize"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"prismplate/internal/vector"
	"strconv"
	"time"
)

type Service struct {
	Repo  *repository.Repo
	Pool  *exposure.Pool
	slots chan struct{}
}

func New(r *repository.Repo) *Service {
	return &Service{Repo: r, Pool: exposure.New(2, 8), slots: make(chan struct{}, 2)}
}
func (s *Service) Close() { s.Pool.Close() }
func (s *Service) Create(id, title string, d scene.Document) (repository.Plate, error) {
	if !validate.ID(id) || len(title) == 0 || len(title) > 160 {
		return repository.Plate{}, fmt.Errorf("invalid plate identity")
	}
	if _, err := validate.Compile(d); err != nil {
		return repository.Plate{}, err
	}
	p := repository.Plate{ID: id, Title: title, State: "active", Revisions: []repository.Revision{{Number: 1, Digest: repository.SceneHash(d), Document: d, Created: time.Now().UTC()}}}
	err := s.Repo.Update(func(st *repository.State) error {
		if _, ok := st.Plates[id]; ok {
			return repository.ErrConflict
		}
		st.Plates[id] = p
		return nil
	})
	return p, err
}
func (s *Service) Plate(id string) (repository.Plate, error) {
	p, ok := s.Repo.View().Plates[id]
	if !ok {
		return p, repository.ErrMissing
	}
	return p, nil
}
func (s *Service) Revise(id string, expected int, d scene.Document) (repository.Plate, error) {
	if _, err := validate.Compile(d); err != nil {
		return repository.Plate{}, err
	}
	var p repository.Plate
	err := s.Repo.Update(func(st *repository.State) error {
		var ok bool
		p, ok = st.Plates[id]
		if !ok {
			return repository.ErrMissing
		}
		if p.State != "active" {
			return repository.ErrState
		}
		if len(p.Revisions) != expected {
			return repository.ErrConflict
		}
		if len(p.Revisions) >= 64 {
			return fmt.Errorf("revision capacity reached")
		}
		p.Revisions = append(p.Revisions, repository.Revision{Number: expected + 1, Digest: repository.SceneHash(d), Document: d, Created: time.Now().UTC()})
		st.Plates[id] = p
		return nil
	})
	return p, err
}
func (s *Service) Archive(id string) error {
	return s.Repo.Update(func(st *repository.State) error {
		p, ok := st.Plates[id]
		if !ok {
			return repository.ErrMissing
		}
		p.State = "archived"
		st.Plates[id] = p
		return nil
	})
}
func (s *Service) Branch(id string, revision int, target, title string) (repository.Plate, error) {
	p, err := s.Plate(id)
	if err != nil {
		return p, err
	}
	if revision < 1 || revision > len(p.Revisions) {
		return p, repository.ErrMissing
	}
	return s.Create(target, title, p.Revisions[revision-1].Document)
}
func (s *Service) capture(id string, revision int) (repository.Revision, error) {
	p, err := s.Plate(id)
	if err != nil {
		return repository.Revision{}, err
	}
	if revision < 1 || revision > len(p.Revisions) {
		return repository.Revision{}, repository.ErrMissing
	}
	return p.Revisions[revision-1], nil
}
func (s *Service) render(ctx context.Context, id string, r repository.Revision, format string) (repository.Artifact, error) {
	var a repository.Artifact
	select {
	case s.slots <- struct{}{}:
		defer func() { <-s.slots }()
	case <-ctx.Done():
		return a, ctx.Err()
	default:
		return a, exposure.ErrBusy
	}
	c, err := validate.Compile(r.Document)
	if err != nil {
		return a, err
	}
	var data []byte
	switch format {
	case "png":
		data, err = raster.Render(ctx, c)
	case "svg":
		data = vector.Export(c)
	default:
		return a, fmt.Errorf("format must be png or svg")
	}
	if err != nil {
		return a, err
	}
	digest := repository.Hash(data)
	key := repository.Hash([]byte(id + ":" + strconv.Itoa(r.Number) + ":" + r.Digest + ":" + format + ":" + digest))
	a = repository.Artifact{ID: key, Plate: id, Revision: r.Number, SceneDigest: r.Digest, Format: format, Digest: digest, State: "available", Data: data}
	err = s.Repo.Update(func(st *repository.State) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if old, ok := st.Artifacts[key]; ok {
			if old.State != "available" {
				return repository.ErrState
			}
			a = old
			return nil
		}
		st.Artifacts[key] = a
		return nil
	})
	return a, err
}
func (s *Service) Render(ctx context.Context, id string, revision int, format string) (repository.Artifact, error) {
	r, err := s.capture(id, revision)
	if err != nil {
		return repository.Artifact{}, err
	}
	return s.render(ctx, id, r, format)
}
func (s *Service) Submit(id string, revision int, format string) (exposure.Job, error) {
	r, err := s.capture(id, revision)
	if err != nil {
		return exposure.Job{}, err
	}
	if format != "png" && format != "svg" {
		return exposure.Job{}, fmt.Errorf("unknown format")
	}
	return s.Pool.Submit(func(ctx context.Context) (string, error) {
		ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		a, e := s.render(ctx, id, r, format)
		return a.ID, e
	})
}
func (s *Service) Artifact(id string) (repository.Artifact, error) {
	a, ok := s.Repo.View().Artifacts[id]
	if !ok {
		return a, repository.ErrMissing
	}
	if a.State != "available" {
		return a, repository.ErrState
	}
	return a, nil
}
func (s *Service) Revoke(id string) error {
	return s.Repo.Update(func(st *repository.State) error {
		a, ok := st.Artifacts[id]
		if !ok {
			return repository.ErrMissing
		}
		for _, p := range st.Proofs {
			if p.Artifact == id && p.State == "accepted" {
				return repository.ErrState
			}
		}
		a.State = "revoked"
		st.Artifacts[id] = a
		return nil
	})
}
func (s *Service) OpenProof(id, artifact string) (repository.Proof, error) {
	p := repository.Proof{ID: id, Artifact: artifact, State: "open"}
	if !validate.ID(id) {
		return p, fmt.Errorf("invalid proof identity")
	}
	err := s.Repo.Update(func(st *repository.State) error {
		if _, ok := st.Proofs[id]; ok {
			return repository.ErrConflict
		}
		a, ok := st.Artifacts[artifact]
		if !ok {
			return repository.ErrMissing
		}
		if a.State != "available" {
			return repository.ErrState
		}
		st.Proofs[id] = p
		return nil
	})
	return p, err
}
func (s *Service) Decide(id, state, note string) (repository.Proof, error) {
	var p repository.Proof
	if (state != "accepted" && state != "rejected") || len(note) > 1000 {
		return p, fmt.Errorf("invalid proof decision")
	}
	err := s.Repo.Update(func(st *repository.State) error {
		var ok bool
		p, ok = st.Proofs[id]
		if !ok {
			return repository.ErrMissing
		}
		if p.State != "open" {
			return repository.ErrState
		}
		if st.Artifacts[p.Artifact].State != "available" {
			return repository.ErrState
		}
		p.State = state
		p.Note = note
		st.Proofs[id] = p
		return nil
	})
	return p, err
}
func (s *Service) Export(id string) ([]byte, error) {
	p, e := s.Plate(id)
	if e != nil {
		return nil, e
	}
	return bundle.Seal(p)
}

// SanitizePreflight reports which revisions of a plate cannot be converted to a
// delivery-only draft for the requested region. It performs no mutation and no
// persistence, so a sender can learn about blocked revisions before exporting.
func (s *Service) SanitizePreflight(id string, points []geom.Point) (sanitize.Eligibility, error) {
	p, e := s.Plate(id)
	if e != nil {
		return sanitize.Eligibility{}, e
	}
	// A structurally invalid region is a caller error, not a property of any
	// revision; only valid regions can be used to report blocked revisions.
	if _, e := sanitize.NewPolygon(points); e != nil {
		return sanitize.Eligibility{}, e
	}
	revisions := make([]sanitize.Revision, 0, len(p.Revisions))
	for _, r := range p.Revisions {
		revisions = append(revisions, sanitize.Revision{Number: r.Number, Scene: r.Document})
	}
	elig := sanitize.Preflight(revisions, points)
	return elig, nil
}

// SanitizeExport builds a standalone delivery draft for one revision. The
// result is a fresh single-revision bundle envelope reusing the existing
// import contract, so it opens in an empty data root. Nothing is written to
// this repository: the bytes are the entire delivery.
func (s *Service) SanitizeExport(ctx context.Context, id string, revision int, points []geom.Point) ([]byte, sanitize.Report, string, error) {
	// Delivery verification renders the source and the draft, so it shares the
	// same bounded compute slots as ordinary exposures instead of adding an
	// unbounded second rendering path.
	select {
	case s.slots <- struct{}{}:
		defer func() { <-s.slots }()
	case <-ctx.Done():
		return nil, sanitize.Report{}, "", ctx.Err()
	default:
		return nil, sanitize.Report{}, "", exposure.ErrBusy
	}
	r, e := s.capture(id, revision)
	if e != nil {
		return nil, sanitize.Report{}, "", e
	}
	area, e := sanitize.AreaFor(points, r.Document)
	if e != nil {
		return nil, sanitize.Report{}, "", e
	}
	res, e := sanitize.Sanitize(r.Document, area)
	if e != nil {
		return nil, sanitize.Report{}, "", e
	}
	if _, e = validate.Compile(res.Document); e != nil {
		return nil, sanitize.Report{}, "", e
	}
	// The delivery must not leak anything from the source plate: it is one
	// revision, no artifacts, no proofs and no jobs.
	p := repository.Plate{
		ID:    id,
		Title: s.PlateTitle(id),
		State: "active",
		Revisions: []repository.Revision{{
			Number:   1,
			Digest:   repository.SceneHash(res.Document),
			Document: res.Document,
		}},
	}
	raw, e := bundle.Seal(p)
	if e != nil {
		return nil, sanitize.Report{}, "", e
	}
	// Opening the sealed envelope with the real import path proves the
	// delivery is self-contained before it leaves this process.
	if _, e = bundle.Open(raw); e != nil {
		return nil, sanitize.Report{}, "", e
	}
	bnd, e := sanitize.ComputeBound(ctx, r.Document, res.Document, area)
	if e != nil {
		return nil, sanitize.Report{}, "", e
	}
	rep := res.Report
	rep.SampledError = bnd.MaxError
	rep.VerifiedPixels = bnd.Interior
	rep.BoundarySkipped = bnd.Boundary
	rep.OutsidePixels = bnd.Outside
	rep.OutsideError = bnd.OutsideError
	if bnd.OutsideError != 0 {
		return nil, sanitize.Report{}, "", fmt.Errorf("delivery differs from the background in %d pixels outside the region", bnd.Outside)
	}
	return raw, rep, res.Digest, nil
}

func (s *Service) PlateTitle(id string) string {
	p, _ := s.Plate(id)
	return p.Title
}
func (s *Service) Import(raw []byte) (repository.Plate, error) {
	p, e := bundle.Open(raw)
	if e != nil {
		return p, e
	}
	e = s.Repo.Update(func(st *repository.State) error {
		if _, ok := st.Plates[p.ID]; ok {
			return repository.ErrConflict
		}
		st.Plates[p.ID] = p
		return nil
	})
	return p, e
}
