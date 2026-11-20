// Package service joins revision facts, render computation and publication rights.
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"prismplate/internal/bundle"
	"prismplate/internal/exposure"
	"prismplate/internal/raster"
	"prismplate/internal/repository"
	"prismplate/internal/scene"
	"prismplate/internal/sep"
	"prismplate/internal/validate"
	"prismplate/internal/vector"
	"strconv"
	"time"
)

func jsonMarshalIndent(v any) ([]byte, error) {
	return json.MarshalIndent(v, "", "  ")
}

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
	if format == "spotsep" {
		return s.separate(ctx, id, r)
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
		return a, fmt.Errorf("format must be png, svg or spotsep")
	}
	if err != nil {
		return a, err
	}
	return s.commitArtifact(id, r, format, "", data)
}

// commitArtifact stores one fully computed artifact with the stable identity
// derived from plate, revision, scene digest, format, optional ink and content.
func (s *Service) commitArtifact(id string, r repository.Revision, format, ink string, data []byte) (repository.Artifact, error) {
	digest := repository.Hash(data)
	keyMaterial := id + ":" + strconv.Itoa(r.Number) + ":" + r.Digest + ":" + format + ":" + ink + ":" + digest
	key := repository.Hash([]byte(keyMaterial))
	a := repository.Artifact{ID: key, Plate: id, Revision: r.Number, SceneDigest: r.Digest, Format: format, Ink: ink, Digest: digest, State: "available", Data: data}
	err := s.Repo.Update(func(st *repository.State) error {
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

// separate computes the reference composite, every per-ink coverage plate, the
// press-order recomposite and the handover manifest, then publishes all of them
// in one atomic snapshot update: a failed computation leaves no partial plates.
func (s *Service) separate(ctx context.Context, id string, r repository.Revision) (repository.Artifact, error) {
	var manifest repository.Artifact
	c, err := validate.Compile(r.Document)
	if err != nil {
		return manifest, err
	}
	if len(r.Document.Spots) == 0 {
		return manifest, fmt.Errorf("spot separation requires declared spot inks")
	}
	referencePNG, err := raster.Render(ctx, c)
	if err != nil {
		return manifest, err
	}
	result, err := sep.Render(ctx, c, id, r.Number, r.Digest, referencePNG)
	if err != nil {
		return manifest, err
	}
	// Precompute every artifact before the commit point.
	pending := []repository.Artifact{}
	refs := []sep.ArtifactRef{}
	ref, err := s.buildArtifact(id, r, "png", "", result.Reference)
	if err != nil {
		return manifest, err
	}
	pending = append(pending, ref)
	refs = append(refs, sep.ArtifactRef{Format: "png", ID: ref.ID, Digest: ref.Digest})
	for _, p := range result.Plates {
		a, e := s.buildArtifact(id, r, "spotplate", p.Ink, p.Data)
		if e != nil {
			return manifest, e
		}
		pending = append(pending, a)
		refs = append(refs, sep.ArtifactRef{Ink: p.Ink, Format: "spotplate", ID: a.ID, Digest: a.Digest})
	}
	proof, err := s.buildArtifact(id, r, "spotproof", "", result.Recomposited)
	if err != nil {
		return manifest, err
	}
	pending = append(pending, proof)
	refs = append(refs, sep.ArtifactRef{Format: "spotproof", ID: proof.ID, Digest: proof.Digest})
	// Artifact ids are content-addressed and deterministic, so the manifest can
	// reference them before persistence without weakening its own digest.
	result.Manifest.Artifacts = refs
	manifestBytes, err := jsonMarshalIndent(result.Manifest)
	if err != nil {
		return manifest, err
	}
	manifest, err = s.buildArtifact(id, r, "spotsep", "", manifestBytes)
	if err != nil {
		return manifest, err
	}
	pending = append(pending, manifest)
	err = s.Repo.Update(func(st *repository.State) error {
		if e := ctx.Err(); e != nil {
			return e
		}
		for _, a := range pending {
			if old, ok := st.Artifacts[a.ID]; ok {
				if old.State != "available" {
					return repository.ErrState
				}
				continue
			}
			st.Artifacts[a.ID] = a
		}
		return nil
	})
	if err != nil {
		return manifest, err
	}
	if got, ok := s.Repo.View().Artifacts[manifest.ID]; ok {
		manifest = got
	}
	manifest.Data = nil
	return manifest, nil
}

// buildArtifact assembles an artifact without persisting it.
func (s *Service) buildArtifact(id string, r repository.Revision, format, ink string, data []byte) (repository.Artifact, error) {
	digest := repository.Hash(data)
	keyMaterial := id + ":" + strconv.Itoa(r.Number) + ":" + r.Digest + ":" + format + ":" + ink + ":" + digest
	return repository.Artifact{
		ID: repository.Hash([]byte(keyMaterial)), Plate: id, Revision: r.Number,
		SceneDigest: r.Digest, Format: format, Ink: ink, Digest: digest,
		State: "available", Data: data,
	}, nil
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
	if format != "png" && format != "svg" && format != "spotsep" {
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

// separationGroup returns every artifact id belonging to the same spot
// separation run as the target: the manifests of the same plate, revision and
// scene digest list their own reference, plates and recomposite explicitly.
func separationGroup(st *repository.State, target repository.Artifact) map[string]bool {
	group := map[string]bool{target.ID: true}
	for _, a := range st.Artifacts {
		if a.Format != "spotsep" || a.Plate != target.Plate || a.Revision != target.Revision ||
			a.SceneDigest != target.SceneDigest || a.State != "available" {
			continue
		}
		var m sep.Manifest
		if err := json.Unmarshal(a.Data, &m); err != nil {
			continue
		}
		for _, ref := range m.Artifacts {
			group[ref.ID] = true
		}
		group[a.ID] = true
	}
	return group
}

func (s *Service) Revoke(id string) error {
	return s.Repo.Update(func(st *repository.State) error {
		a, ok := st.Artifacts[id]
		if !ok {
			return repository.ErrMissing
		}
		// A separation is an atomic group: an accepted proof on any member
		// (manifest, reference, coverage plate or recomposite) protects them all.
		group := separationGroup(st, a)
		for _, p := range st.Proofs {
			if p.State == "accepted" && group[p.Artifact] {
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
