// Package service joins revision facts, render computation and publication rights.
package service

import (
	"context"
	"fmt"
	"prismplate/internal/bundle"
	"prismplate/internal/compile"
	"prismplate/internal/exposure"
	"prismplate/internal/raster"
	"prismplate/internal/repository"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"prismplate/internal/vector"
	"strconv"
	"time"
)

// Draft cache limits. Drafts are shared across formats and repeated proofs of the
// same revision; eviction never touches in-flight readers because every reader
// holds an immutable value. The byte budget is below the 128MiB snapshot bound
// so expanded contours cannot dominate process memory.
const (
	cacheCapacity  = 16
	cacheByteLimit = 16 << 20
)

type Service struct {
	Repo  *repository.Repo
	Pool  *exposure.Pool
	Draft *compile.Cache
	slots chan struct{}
}

func New(r *repository.Repo) *Service {
	return &Service{Repo: r, Pool: exposure.New(2, 8), Draft: compile.NewCache(cacheCapacity, cacheByteLimit), slots: make(chan struct{}, 2)}
}
func (s *Service) Close() { s.Pool.Close() }

// CompileStats reports bounded compiled-draft reuse for diagnostics. It is
// process-scoped and never becomes part of persisted state or exported content.
func (s *Service) CompileStats() compile.Stats { return s.Draft.Stats() }
func (s *Service) Create(id, title string, d scene.Document) (repository.Plate, error) {
	if !validate.ID(id) || len(title) == 0 || len(title) > 160 {
		return repository.Plate{}, fmt.Errorf("invalid plate identity")
	}
	if _, err := compile.Compile(d); err != nil {
		return repository.Plate{}, err
	}
	// The revision owns its document value. The caller keeps its own copy and may
	// edit it freely; persistence takes another independent snapshot.
	p := repository.Plate{ID: id, Title: title, State: "active", Revisions: []repository.Revision{{Number: 1, Digest: repository.SceneHash(d), Document: d.Clone(), Created: time.Now().UTC()}}}
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
	if _, err := compile.Compile(d); err != nil {
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
		p.Revisions = append(p.Revisions, repository.Revision{Number: expected + 1, Digest: repository.SceneHash(d), Document: d.Clone(), Created: time.Now().UTC()})
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

// capture freezes one revision before rendering. The returned document is an
// independent value, so a concurrent Revise appending a later revision cannot
// change what this render will compile.
func (s *Service) capture(id string, revision int) (repository.Revision, error) {
	return s.Repo.Revision(id, revision)
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
	// The revision was captured before the compute slot was taken; editing the
	// plate afterwards cannot change which document this draft is built from.
	draft, err := compile.Resolve(s.Draft, compile.Source{Number: r.Number, Digest: r.Digest, Document: r.Document})
	if err != nil {
		return a, err
	}
	var data []byte
	switch format {
	case "png":
		// Raster reads the shared draft by value and samples canvas coordinates.
		data, err = raster.Render(ctx, draft)
	case "svg":
		// Vector reads the same expanded contours and keeps its own transform.
		data = vector.Export(draft)
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
func (s *Service) Import(raw []byte) (repository.Plate, error) {
	p, e := bundle.Open(raw)
	if e != nil {
		return p, e
	}
	e = s.Repo.Update(func(st *repository.State) error {
		if _, ok := st.Plates[p.ID]; ok {
			return repository.ErrConflict
		}
		st.Plates[p.ID] = p.Clone()
		return nil
	})
	return p, e
}
