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
	"prismplate/internal/variant"
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
func (s *Service) CreateTemplate(id, title string, def variant.Definition) (repository.Template, error) {
	var t repository.Template
	if !validate.ID(id) || len(title) == 0 || len(title) > 160 {
		return t, fmt.Errorf("invalid template identity")
	}
	if err := variant.ValidateDefinition(def); err != nil {
		return t, err
	}
	t = repository.Template{ID: id, Title: title, State: "active", Revisions: []repository.TemplateRevision{{Number: 1, Digest: repository.DefinitionHash(def), Definition: def, Created: time.Now().UTC()}}}
	err := s.Repo.Update(func(st *repository.State) error {
		if _, ok := st.Templates[id]; ok {
			return repository.ErrConflict
		}
		st.Templates[id] = t
		return nil
	})
	return t, err
}
func (s *Service) Template(id string) (repository.Template, error) {
	t, ok := s.Repo.View().Templates[id]
	if !ok {
		return t, repository.ErrMissing
	}
	return t, nil
}
func (s *Service) ReviseTemplate(id string, expected int, def variant.Definition) (repository.Template, error) {
	var t repository.Template
	if err := variant.ValidateDefinition(def); err != nil {
		return t, err
	}
	err := s.Repo.Update(func(st *repository.State) error {
		var ok bool
		t, ok = st.Templates[id]
		if !ok {
			return repository.ErrMissing
		}
		if t.State != "active" {
			return repository.ErrState
		}
		if len(t.Revisions) != expected {
			return repository.ErrConflict
		}
		if len(t.Revisions) >= 32 {
			return fmt.Errorf("template revision capacity reached")
		}
		t.Revisions = append(t.Revisions, repository.TemplateRevision{Number: expected + 1, Digest: repository.DefinitionHash(def), Definition: def, Created: time.Now().UTC()})
		st.Templates[id] = t
		return nil
	})
	return t, err
}

// ArchiveTemplate mirrors plate archival: revisions freeze, but historical
// template revisions stay usable for new batches.
func (s *Service) ArchiveTemplate(id string) error {
	return s.Repo.Update(func(st *repository.State) error {
		t, ok := st.Templates[id]
		if !ok {
			return repository.ErrMissing
		}
		t.State = "archived"
		st.Templates[id] = t
		return nil
	})
}

type ItemRequest struct {
	Label  string             `json:"label"`
	Params map[string]float64 `json:"params"`
}

// CreateBatch resolves every parameter set against one fixed template
// revision and commits the outcome atomically: each resolved item becomes an
// independent draft plate whose revision 1 freezes the resolved document,
// each rejected item keeps only its reason. Template edits afterwards cannot
// alter the frozen drafts, and replaying the same revision and parameters
// reproduces the same digest.
func (s *Service) CreateBatch(templateID string, revision int, batchID string, items []ItemRequest) (repository.Batch, error) {
	var b repository.Batch
	if !validate.ID(batchID) {
		return b, fmt.Errorf("invalid batch identity")
	}
	if len(items) < 1 || len(items) > 24 {
		return b, fmt.Errorf("batch requires 1..24 items")
	}
	t, err := s.Template(templateID)
	if err != nil {
		return b, err
	}
	if revision < 1 || revision > len(t.Revisions) {
		return b, repository.ErrMissing
	}
	rev := t.Revisions[revision-1]
	labels := map[string]bool{}
	for _, it := range items {
		if !validate.ID(it.Label) || labels[it.Label] {
			return b, fmt.Errorf("invalid or duplicate item label %q", it.Label)
		}
		labels[it.Label] = true
		if len(batchID)+1+len(it.Label) > 64 || len(it.Label)+3+len(t.Title) > 160 {
			return b, fmt.Errorf("item label %q too long for the derived draft identity", it.Label)
		}
	}
	now := time.Now().UTC()
	b = repository.Batch{ID: batchID, Template: t.ID, Revision: revision, TemplateDigest: rev.Digest, Created: now}
	plates := map[string]repository.Plate{}
	for _, it := range items {
		item := repository.BatchItem{Label: it.Label, Params: it.Params}
		doc, err := variant.Resolve(rev.Definition, it.Params)
		if err != nil {
			item.State = "rejected"
			item.Reason = err.Error()
		} else {
			digest := repository.SceneHash(doc)
			plateID := batchID + "-" + it.Label
			item.State = "resolved"
			item.Digest = digest
			item.Plate = plateID
			item.Document = &doc
			plates[plateID] = repository.Plate{
				ID: plateID, Title: it.Label + " / " + t.Title, State: "active",
				Revisions: []repository.Revision{{Number: 1, Digest: digest, Document: doc, Created: now}},
				Origin:    &repository.Origin{Batch: batchID, Template: t.ID, Revision: revision, Digest: rev.Digest, Label: it.Label, Params: it.Params},
			}
		}
		b.Items = append(b.Items, item)
	}
	err = s.Repo.Update(func(st *repository.State) error {
		if _, ok := st.Batches[batchID]; ok {
			return repository.ErrConflict
		}
		tv, ok := st.Templates[t.ID]
		if !ok || revision > len(tv.Revisions) || tv.Revisions[revision-1].Digest != rev.Digest {
			return repository.ErrConflict
		}
		if len(st.Plates)+len(plates) > 128 {
			return fmt.Errorf("plate capacity reached")
		}
		for id, p := range plates {
			if _, ok := st.Plates[id]; ok {
				return repository.ErrConflict
			}
			st.Plates[id] = p
		}
		st.Batches[batchID] = b
		return nil
	})
	return b, err
}
func (s *Service) Batch(id string) (repository.Batch, error) {
	b, ok := s.Repo.View().Batches[id]
	if !ok {
		return b, repository.ErrMissing
	}
	return b, nil
}
