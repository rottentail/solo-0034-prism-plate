// Package service joins revision facts, render computation and publication rights.
package service

import (
	"context"
	"fmt"
	"prismplate/internal/bundle"
	"prismplate/internal/exposure"
	"prismplate/internal/raster"
	"prismplate/internal/repository"
	"prismplate/internal/review"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"prismplate/internal/vector"
	"sort"
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
		// A terminal proof is a review fact: its image and annotated regions must
		// stay available for later revisions' review sheets.
		for _, p := range st.Proofs {
			if p.Artifact == id && p.State != "open" {
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
		p.Plate = a.Plate
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

// AddAnnotation records a region on the exact image under review. Annotations are
// only accepted while the proof is open; rejection freezes them with the proof.
func (s *Service) AddAnnotation(id, proofID string, region repository.Region, comment string) (repository.Annotation, error) {
	var n repository.Annotation
	if !validate.ID(id) || len(comment) == 0 || len(comment) > 1000 {
		return n, fmt.Errorf("invalid annotation identity or comment")
	}
	if region.W <= 0 || region.H <= 0 || region.X < 0 || region.Y < 0 {
		return n, fmt.Errorf("annotation region must be a positive rectangle at non-negative coordinates")
	}
	err := s.Repo.Update(func(st *repository.State) error {
		if _, ok := st.Annotations[id]; ok {
			return repository.ErrConflict
		}
		proof, ok := st.Proofs[proofID]
		if !ok {
			return repository.ErrMissing
		}
		if proof.State != "open" {
			return repository.ErrState
		}
		art, ok := st.Artifacts[proof.Artifact]
		if !ok || art.State != "available" {
			return repository.ErrState
		}
		if art.Format != "png" {
			return fmt.Errorf("regional annotations require a raster proof")
		}
		plate, ok := st.Plates[proof.Plate]
		if !ok || art.Revision > len(plate.Revisions) {
			return repository.ErrMissing
		}
		rev := plate.Revisions[art.Revision-1]
		doc := rev.Document
		if region.X+region.W > float64(doc.Width) || region.Y+region.H > float64(doc.Height) {
			return fmt.Errorf("annotation region exceeds proof canvas")
		}
		compiled, err := validate.Compile(doc)
		if err != nil {
			return err
		}
		n = repository.Annotation{
			ID: id, Proof: proofID, Plate: proof.Plate, Revision: art.Revision,
			CanvasWidth: doc.Width, CanvasHeight: doc.Height,
			Region: region, Anchor: review.PickAnchor(compiled, region),
			Comment: comment, State: "open", Created: time.Now().UTC(),
		}
		st.Annotations[id] = n
		return nil
	})
	return n, err
}

// HandleAnnotation closes an open annotation against a later revision of the same plate.
func (s *Service) HandleAnnotation(id, state string, handledBy int) (repository.Annotation, error) {
	var n repository.Annotation
	if state != "resolved" && state != "dismissed" {
		return n, fmt.Errorf("invalid annotation handling state")
	}
	err := s.Repo.Update(func(st *repository.State) error {
		var ok bool
		n, ok = st.Annotations[id]
		if !ok {
			return repository.ErrMissing
		}
		if n.State != "open" {
			return repository.ErrState
		}
		plate, ok := st.Plates[n.Plate]
		if !ok {
			return repository.ErrMissing
		}
		if handledBy <= n.Revision || handledBy > len(plate.Revisions) {
			return fmt.Errorf("handling revision must be a later existing revision")
		}
		n.State = state
		n.HandledBy = handledBy
		st.Annotations[id] = n
		return nil
	})
	return n, err
}

// AnnotationReview returns open annotations for one plate projected onto targetRevision.
func (s *Service) AnnotationReview(plateID string, targetRevision int) (map[string]any, error) {
	st := s.Repo.View()
	plate, ok := st.Plates[plateID]
	if !ok {
		return nil, repository.ErrMissing
	}
	if targetRevision < 1 || targetRevision > len(plate.Revisions) {
		return nil, repository.ErrMissing
	}
	target := plate.Revisions[targetRevision-1].Document
	type source struct {
		ProofID     string              `json:"proof_id"`
		ProofState  string              `json:"proof_state"`
		Revision    int                 `json:"revision"`
		Projections []review.Projection `json:"projections"`
	}
	grouped := map[int]*source{}
	var order []int
	var pending int
	for _, n := range st.Annotations {
		if n.Plate != plateID || n.State != "open" {
			continue
		}
		pending++
		g, ok := grouped[n.Revision]
		if !ok {
			g = &source{ProofID: n.Proof, Revision: n.Revision, ProofState: st.Proofs[n.Proof].State}
			grouped[n.Revision] = g
			order = append(order, n.Revision)
		}
		g.Projections = append(g.Projections, review.Project(n, target))
	}
	sort.Ints(order)
	sources := make([]source, 0, len(order))
	for _, rev := range order {
		g := grouped[rev]
		sort.Slice(g.Projections, func(i, j int) bool {
			return g.Projections[i].Annotation.ID < g.Projections[j].Annotation.ID
		})
		sources = append(sources, *g)
	}
	return map[string]any{
		"plate": plateID, "target_revision": targetRevision,
		"target_canvas":            map[string]int{"width": target.Width, "height": target.Height},
		"pending_open_annotations": pending,
		"sources":                  sources,
	}, nil
}

// ReviewSheet renders the immutable proof images next to the target draft with
// projected annotation regions. Both proof and target are rendered synchronously
// on demand and never replace stored artifacts.
func (s *Service) ReviewSheet(ctx context.Context, plateID string, targetRevision int) ([]byte, error) {
	st := s.Repo.View()
	plate, ok := st.Plates[plateID]
	if !ok {
		return nil, repository.ErrMissing
	}
	if targetRevision < 1 || targetRevision > len(plate.Revisions) {
		return nil, repository.ErrMissing
	}
	targetRev := plate.Revisions[targetRevision-1]
	targetCompiled, err := validate.Compile(targetRev.Document)
	if err != nil {
		return nil, err
	}
	select {
	case s.slots <- struct{}{}:
		defer func() { <-s.slots }()
	case <-ctx.Done():
		return nil, ctx.Err()
	default:
		return nil, exposure.ErrBusy
	}
	targetPNG, err := raster.Render(ctx, targetCompiled)
	if err != nil {
		return nil, err
	}
	type key struct {
		proof string
		rev   int
	}
	comments := map[string]string{}
	// Deterministic ordering by proof id, then annotation id.
	var ids []string
	for id, n := range st.Annotations {
		if n.Plate == plateID && n.State == "open" {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	groupByKey := map[key]*review.SourceGroup{}
	var orderedKeys []key
	for _, id := range ids {
		n := st.Annotations[id]
		comments[id] = n.Comment
		k := key{n.Proof, n.Revision}
		g, ok := groupByKey[k]
		if !ok {
			proof := st.Proofs[n.Proof]
			art := st.Artifacts[proof.Artifact]
			g = &review.SourceGroup{Revision: n.Revision, ProofID: n.Proof, Width: n.CanvasWidth, Height: n.CanvasHeight, PNG: art.Data}
			groupByKey[k] = g
			orderedKeys = append(orderedKeys, k)
		}
		g.Projections = append(g.Projections, review.Project(n, targetRev.Document))
	}
	for _, g := range groupByKey {
		sort.Slice(g.Projections, func(i, j int) bool {
			return g.Projections[i].Annotation.ID < g.Projections[j].Annotation.ID
		})
	}
	groups := make([]review.SourceGroup, 0, len(orderedKeys))
	for _, k := range orderedKeys {
		groups = append(groups, *groupByKey[k])
	}
	return review.Sheet(targetRev.Document, targetRevision, targetPNG, groups, comments), nil
}
