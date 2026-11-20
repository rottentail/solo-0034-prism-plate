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
	"strings"
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

// ArchiveRequest is the designer's explicit finalization selection. The chosen
// revision is never assumed to be the latest. Artifact and proof ids are pinned
// and re-validated against the chosen revision. OnMissing selects how absent
// deliverables are treated: "reject" (default) refuses an incomplete handover;
// "exclude" archives with null rows that remain visible in the manifest.
type ArchiveRequest struct {
	Revision  int      `json:"revision"`
	Artifacts []string `json:"artifacts"`
	Proofs    []string `json:"proofs"`
	OnMissing string   `json:"on_missing"`
}

// Archive finalizes a plate. The designer explicitly selects the final revision
// together with its PNG, SVG and accepted review records. The selection is
// cross-checked against stored lineage so a higher revision number can never be
// mistaken for the approved design, and an SVG can never be passed off as the
// pixel-promised PNG. The whole decision and the handover manifest are committed
// in one atomic snapshot update.
func (s *Service) Archive(id string, req ArchiveRequest) (repository.Handover, error) {
	if req.OnMissing == "" {
		req.OnMissing = "reject"
	}
	if req.OnMissing != "reject" && req.OnMissing != "exclude" {
		return repository.Handover{}, fmt.Errorf("%w: on_missing must be reject or exclude", repository.ErrInvalid)
	}
	var h repository.Handover
	err := s.Repo.Update(func(st *repository.State) error {
		p, ok := st.Plates[id]
		if !ok {
			return repository.ErrMissing
		}
		if p.State != "active" {
			return repository.ErrState
		}
		if req.Revision < 1 || req.Revision > len(p.Revisions) {
			return fmt.Errorf("%w: revision %d is outside this plate history", repository.ErrConflict, req.Revision)
		}
		rev := p.Revisions[req.Revision-1]

		var png, svg *repository.HandoverRef
		seenArtifact := map[string]bool{}
		for _, aid := range req.Artifacts {
			if seenArtifact[aid] {
				return fmt.Errorf("%w: duplicate artifact selection", repository.ErrInvalid)
			}
			seenArtifact[aid] = true
			a, ok := st.Artifacts[aid]
			if !ok {
				return repository.ErrMissing
			}
			// A selected deliverable must belong to this exact plate and chosen
			// revision and must still be available. Wrong revision or revoked
			// artifacts are hard errors regardless of the missing policy.
			if a.Plate != id || a.Revision != req.Revision || a.SceneDigest != rev.Digest {
				return repository.ErrConflict
			}
			if a.State != "available" {
				return repository.ErrState
			}
			switch a.Format {
			case "png":
				if png != nil {
					return fmt.Errorf("%w: multiple png selections", repository.ErrInvalid)
				}
				png = repository.Ref(a)
			case "svg":
				if svg != nil {
					return fmt.Errorf("%w: multiple svg selections", repository.ErrInvalid)
				}
				svg = repository.Ref(a)
			default:
				return fmt.Errorf("%w: unknown artifact format", repository.ErrInvalid)
			}
		}

		proofRows := []repository.ProofRef{}
		seenProof := map[string]bool{}
		for _, pid := range req.Proofs {
			if seenProof[pid] {
				return fmt.Errorf("%w: duplicate proof selection", repository.ErrInvalid)
			}
			seenProof[pid] = true
			pr, ok := st.Proofs[pid]
			if !ok {
				return repository.ErrMissing
			}
			if pr.State != "accepted" {
				return repository.ErrConflict
			}
			a, ok := st.Artifacts[pr.Artifact]
			if !ok || a.Plate != id || a.Revision != req.Revision || a.State != "available" {
				return repository.ErrConflict
			}
			proofRows = append(proofRows, repository.RefProof(pr, a))
		}

		complete := png != nil && svg != nil && len(proofRows) > 0
		if req.OnMissing == "reject" && !complete {
			// Reject policy: every promised deliverable must be present. The
			// error text names the gap so the caller knows what to produce.
			missing := []string{}
			if png == nil {
				missing = append(missing, "png")
			}
			if svg == nil {
				missing = append(missing, "svg")
			}
			if len(proofRows) == 0 {
				missing = append(missing, "accepted proof")
			}
			return fmt.Errorf("%w: handover incomplete, missing %s", repository.ErrState, strings.Join(missing, ", "))
		}

		h = repository.Handover{
			Plate:       id,
			Revision:    req.Revision,
			SceneDigest: rev.Digest,
			PNG:         png,
			SVG:         svg,
			Proofs:      proofRows,
			OnMissing:   req.OnMissing,
			Complete:    complete,
			Created:     time.Now().UTC(),
		}
		h.Digest = repository.HandoverHash(h)

		p.State = "archived"
		st.Plates[id] = p
		st.Handovers[id] = h
		return nil
	})
	return h, err
}

// Handover returns the finalized handover manifest for an archived plate.
func (s *Service) Handover(id string) (repository.Handover, error) {
	h, ok := s.Repo.View().Handovers[id]
	if !ok {
		return h, repository.ErrMissing
	}
	return h, nil
}

// HandoverReport is the receiver-side re-check of a handover manifest. Every
// field is recomputed from live plate, artifact and proof facts rather than
// trusted from the stored manifest, so a receiver gets an independent verdict on
// whether the listed PNG/SVG and review records still match the chosen source.
type HandoverReport struct {
	Plate          string `json:"plate"`
	Revision       int    `json:"revision"`
	DigestValid    bool   `json:"digest_valid"`
	LineageValid   bool   `json:"lineage_valid"`
	PNGVerified    bool   `json:"png_verified"`
	SVGVerified    bool   `json:"svg_verified"`
	ProofsVerified int    `json:"proofs_verified"`
	Complete       bool   `json:"complete"`
}

// VerifyHandover independently re-derives the manifest digest and every lineage
// row from current state. It reports per-deliverable verdicts instead of a
// single boolean so the receiver can see exactly which part fails to reconcile.
func (s *Service) VerifyHandover(id string) (HandoverReport, error) {
	st := s.Repo.View()
	h, ok := st.Handovers[id]
	if !ok {
		return HandoverReport{}, repository.ErrMissing
	}
	rep := HandoverReport{Plate: id, Revision: h.Revision, Complete: h.Complete}
	rep.DigestValid = repository.HandoverHash(h) == h.Digest

	p, plateOK := st.Plates[id]
	lineage := plateOK && p.State == "archived" && h.Revision >= 1 && h.Revision <= len(p.Revisions)
	var rev repository.Revision
	if lineage {
		rev = p.Revisions[h.Revision-1]
		lineage = h.SceneDigest == rev.Digest
	}
	checkArtifact := func(r *repository.HandoverRef, format string) bool {
		if r == nil {
			return false
		}
		a, ok := st.Artifacts[r.ID]
		return ok && a.Plate == id && a.Revision == h.Revision && a.Format == format &&
			a.State == "available" && a.Digest == r.Digest && a.SceneDigest == rev.Digest &&
			repository.Hash(a.Data) == a.Digest
	}
	if lineage {
		rep.PNGVerified = checkArtifact(h.PNG, "png")
		rep.SVGVerified = checkArtifact(h.SVG, "svg")
		// A present row that no longer reconciles (for example a finalized SVG
		// revoked afterwards) invalidates the overall lineage verdict. Explicit
		// null gaps are not rows, so an exclude-policy manifest with missing
		// deliverables can still have a valid lineage for the rows it does list.
		if h.PNG != nil && !rep.PNGVerified {
			lineage = false
		}
		if h.SVG != nil && !rep.SVGVerified {
			lineage = false
		}
		for _, pr := range h.Proofs {
			proof, ok := st.Proofs[pr.ID]
			if !ok || proof.State != "accepted" || proof.Artifact != pr.Artifact {
				lineage = false
				continue
			}
			a, ok := st.Artifacts[pr.Artifact]
			if !ok || a.Plate != id || a.Revision != h.Revision || a.State != "available" ||
				a.Digest != pr.Digest || a.SceneDigest != rev.Digest || proof.Note != pr.Note {
				lineage = false
				continue
			}
			rep.ProofsVerified++
		}
	}
	rep.LineageValid = lineage
	return rep, nil
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
