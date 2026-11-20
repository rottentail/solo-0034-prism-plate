// Package service joins revision facts, render computation and publication rights.
package service

import (
	"context"
	"fmt"
	"prismplate/internal/bundle"
	"prismplate/internal/exposure"
	"prismplate/internal/raster"
	"prismplate/internal/repository"
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

// normalizeRegion reports the effective canvas-space region and whether the
// request is a whole-canvas render. A nil region and a region equal to the full
// canvas both mean "whole canvas", so both keep the historical artifact
// identity, metadata and pixels. Anything else must be a non-empty rectangle
// fully inside the canvas; it is never silently clipped.
func normalizeRegion(d scene.Document, region *scene.Region) (scene.Region, bool, error) {
	full := scene.Region{X: 0, Y: 0, Width: d.Width, Height: d.Height}
	if region == nil || *region == full {
		return full, true, nil
	}
	if !region.Within(d.Width, d.Height) {
		return full, false, fmt.Errorf("region must be a non-empty rectangle inside the %dx%d canvas", d.Width, d.Height)
	}
	return *region, false, nil
}

func renderKey(id string, r repository.Revision, format string, region scene.Region, whole bool, digest string) string {
	key := id + ":" + strconv.Itoa(r.Number) + ":" + r.Digest + ":" + format + ":" + digest
	if !whole {
		key += ":" + strconv.Itoa(region.X) + "," + strconv.Itoa(region.Y) + "," + strconv.Itoa(region.Width) + "," + strconv.Itoa(region.Height)
	}
	return repository.Hash([]byte(key))
}

func (s *Service) render(ctx context.Context, id string, r repository.Revision, format string, region *scene.Region) (repository.Artifact, error) {
	var a repository.Artifact
	if format != "png" && format != "svg" {
		return a, fmt.Errorf("format must be png or svg")
	}
	if format == "svg" && region != nil {
		return a, fmt.Errorf("region is only supported for png")
	}
	scope, whole, err := normalizeRegion(r.Document, region)
	if err != nil {
		return a, err
	}
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
		data, err = raster.Render(ctx, c, scope)
	case "svg":
		data = vector.Export(c)
	}
	if err != nil {
		return a, err
	}
	digest := repository.Hash(data)
	key := renderKey(id, r, format, scope, whole, digest)
	a = repository.Artifact{ID: key, Plate: id, Revision: r.Number, SceneDigest: r.Digest, Format: format, Digest: digest, State: "available", Data: data}
	if !whole {
		regionCopy := scope
		a.Region = &regionCopy
	}
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
func (s *Service) Render(ctx context.Context, id string, revision int, format string, region *scene.Region) (repository.Artifact, error) {
	r, err := s.capture(id, revision)
	if err != nil {
		return repository.Artifact{}, err
	}
	return s.render(ctx, id, r, format, region)
}
func (s *Service) Submit(id string, revision int, format string, region *scene.Region) (exposure.Job, error) {
	r, err := s.capture(id, revision)
	if err != nil {
		return exposure.Job{}, err
	}
	if format != "png" && format != "svg" {
		return exposure.Job{}, fmt.Errorf("unknown format")
	}
	// Validate the region against the captured revision now so an asynchronous
	// submission fails at the entry instead of later in a worker.
	if _, _, err = normalizeRegion(r.Document, region); err != nil {
		return exposure.Job{}, err
	}
	if format == "svg" && region != nil {
		return exposure.Job{}, fmt.Errorf("region is only supported for png")
	}
	return s.Pool.Submit(func(ctx context.Context) (string, error) {
		ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		a, e := s.render(ctx, id, r, format, region)
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
