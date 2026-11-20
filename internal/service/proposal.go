package service

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"prismplate/internal/corridor"
	"prismplate/internal/exposure"
	"prismplate/internal/raster"
	"prismplate/internal/repository"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"prismplate/internal/vector"
	"sync"
	"time"
)

// ErrBusy is re-exported so proposal rendering reports the same overload
// condition as the standard render workflow.
var ErrBusy = exposure.ErrBusy

// ErrProposalRecheck means a candidate preview can no longer be committed:
// the plate moved on or the protected channel would not survive re-verification.
var ErrProposalRecheck = errors.New("candidate no longer satisfies protected channels")

func compileCandidate(d scene.Document) (scene.Compiled, error) { return validate.Compile(d) }
func renderPNG(ctx context.Context, c scene.Compiled) ([]byte, error) {
	return raster.Render(ctx, c)
}
func exportSVG(c scene.Compiled) []byte { return vector.Export(c) }

// Preview is a render of an uncommitted candidate. It lives only in the
// transient proposal; it never enters repository lineage. Digest is the hash of
// the rendered bytes, so callers can prove that a later committed render matches
// the preview they inspected.
type Preview struct {
	Format string `json:"format"`
	Digest string `json:"digest"`
	Data   []byte `json:"-"`
}

// CandidatePreview pairs a solved candidate with its canonical-path previews.
type CandidatePreview struct {
	corridor.Candidate
	PreviewPNG Preview `json:"preview_png"`
	PreviewSVG Preview `json:"preview_svg"`
}

// ProposalRecord is a transient, in-memory editing proposal. It never enters a
// snapshot and disappears with the process, matching exposure task semantics.
type ProposalRecord struct {
	ID               string             `json:"id"`
	Plate            string             `json:"plate"`
	Revision         int                `json:"revision"`
	ExpectedRevision int                `json:"expected_revision"`
	Channels         []corridor.Channel `json:"channels"`
	Feasible         bool               `json:"feasible"`
	BoundaryAlpha    float64            `json:"boundary_alpha"`
	Baseline         corridor.Report    `json:"baseline"`
	Limit            *corridor.Report   `json:"limit,omitempty"`
	Candidates       []CandidatePreview `json:"candidates"`
	Created          time.Time          `json:"created"`
}

const maxProposalRecords = 128

type proposalStore struct {
	mu      sync.Mutex
	records map[string]*ProposalRecord
	order   []string
}

func newProposalStore() *proposalStore {
	return &proposalStore{records: map[string]*ProposalRecord{}}
}

func (s *proposalStore) put(rec *ProposalRecord) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.records) >= maxProposalRecords {
		return fmt.Errorf("corridor proposal capacity reached")
	}
	s.records[rec.ID] = rec
	s.order = append(s.order, rec.ID)
	return nil
}

func (s *proposalStore) get(id string) (*ProposalRecord, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	rec, ok := s.records[id]
	return rec, ok
}

func newProposalID() string {
	var b [12]byte
	_, _ = rand.Read(b[:])
	return "cp-" + hex.EncodeToString(b[:])
}

// ProposeChannel solves a protected negative-space move against a committed
// revision and renders every returned candidate through the same render
// functions as production. Nothing is appended to revision history here.
func (s *Service) ProposeChannel(ctx context.Context, plateID string, revision int, moves []corridor.Move, channels []corridor.Channel) (ProposalRecord, error) {
	rev, err := s.capture(plateID, revision)
	if err != nil {
		return ProposalRecord{}, err
	}
	plate, err := s.Plate(plateID)
	if err != nil {
		return ProposalRecord{}, err
	}
	proposal, err := corridor.Solve(rev.Document, moves, channels, repository.SceneHash)
	if err != nil {
		return ProposalRecord{}, err
	}
	rec := ProposalRecord{
		ID:               newProposalID(),
		Plate:            plateID,
		Revision:         revision,
		ExpectedRevision: len(plate.Revisions),
		Channels:         channels,
		Feasible:         proposal.Feasible,
		BoundaryAlpha:    proposal.BoundaryAlpha,
		Baseline:         proposal.Baseline,
		Limit:            proposal.Limit,
		Created:          time.Now().UTC(),
	}
	for _, cand := range proposal.Candidates {
		png, e := s.renderCandidate(ctx, cand.Document, "png")
		if e != nil {
			return ProposalRecord{}, e
		}
		svg, e := s.renderCandidate(ctx, cand.Document, "svg")
		if e != nil {
			return ProposalRecord{}, e
		}
		rec.Candidates = append(rec.Candidates, CandidatePreview{Candidate: cand, PreviewPNG: png, PreviewSVG: svg})
	}
	if err := s.proposals.put(&rec); err != nil {
		return ProposalRecord{}, err
	}
	return rec, nil
}

// renderCandidate renders an uncommitted candidate document by acquiring the
// same compute slot and invoking the same raster/SVG code as production
// renders, returning a transient preview with a content digest.
func (s *Service) renderCandidate(ctx context.Context, d scene.Document, format string) (Preview, error) {
	select {
	case s.slots <- struct{}{}:
		defer func() { <-s.slots }()
	case <-ctx.Done():
		return Preview{}, ctx.Err()
	default:
		return Preview{}, ErrBusy
	}
	c, err := compileCandidate(d)
	if err != nil {
		return Preview{}, err
	}
	var data []byte
	switch format {
	case "png":
		data, err = renderPNG(ctx, c)
	case "svg":
		data = exportSVG(c)
	default:
		return Preview{}, fmt.Errorf("format must be png or svg")
	}
	if err != nil {
		return Preview{}, err
	}
	return Preview{Format: format, Digest: repository.Hash(data), Data: data}, nil
}

// GetProposal returns a transient proposal or ErrMissing.
func (s *Service) GetProposal(id string) (ProposalRecord, error) {
	rec, ok := s.proposals.get(id)
	if !ok {
		return ProposalRecord{}, repository.ErrMissing
	}
	return *rec, nil
}

// PreviewBytes returns the rendered preview bytes for one candidate. The
// digest returned alongside lets the caller verify a committed render later.
func (s *Service) PreviewBytes(proposalID string, candidateIndex int, format string) (Preview, error) {
	rec, ok := s.proposals.get(proposalID)
	if !ok {
		return Preview{}, repository.ErrMissing
	}
	if candidateIndex < 0 || candidateIndex >= len(rec.Candidates) {
		return Preview{}, fmt.Errorf("candidate index out of range")
	}
	switch format {
	case "png":
		return rec.Candidates[candidateIndex].PreviewPNG, nil
	case "svg":
		return rec.Candidates[candidateIndex].PreviewSVG, nil
	default:
		return Preview{}, fmt.Errorf("format must be png or svg")
	}
}

// AcceptProposal re-verifies the candidate against current history, then appends
// it as the next immutable revision. Preview and commit render the same document
// and therefore share scene digest and rendered bytes.
func (s *Service) AcceptProposal(proposalID string, candidateIndex int) (repository.Plate, ProposalRecord, error) {
	rec, ok := s.proposals.get(proposalID)
	if !ok {
		return repository.Plate{}, ProposalRecord{}, repository.ErrMissing
	}
	if candidateIndex < 0 || candidateIndex >= len(rec.Candidates) {
		return repository.Plate{}, *rec, fmt.Errorf("candidate index out of range")
	}
	cand := rec.Candidates[candidateIndex]
	var p repository.Plate
	err := s.Repo.Update(func(st *repository.State) error {
		var ok bool
		p, ok = st.Plates[rec.Plate]
		if !ok {
			return repository.ErrMissing
		}
		if p.State != "active" {
			return repository.ErrState
		}
		if len(p.Revisions) != rec.ExpectedRevision {
			return repository.ErrConflict
		}
		if len(p.Revisions) >= 64 {
			return fmt.Errorf("revision capacity reached")
		}
		// Recompile and re-run every protected channel; a failed recheck never
		// appends a revision, so a squeezed channel cannot reach history.
		rep, err := corridor.Check(cand.Document, rec.Channels)
		if err != nil {
			return err
		}
		if !rep.Passes || repository.SceneHash(cand.Document) != cand.SceneDigest {
			return ErrProposalRecheck
		}
		p.Revisions = append(p.Revisions, repository.Revision{Number: len(p.Revisions) + 1, Digest: cand.SceneDigest, Document: cand.Document, Created: time.Now().UTC()})
		st.Plates[rec.Plate] = p
		return nil
	})
	if err != nil {
		return repository.Plate{}, *rec, err
	}
	return p, *rec, nil
}
