// Package repository commits complete immutable snapshots under a single writer.
package repository

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"prismplate/internal/scene"
	"sync"
	"time"
)

var ErrConflict = errors.New("revision conflict")
var ErrMissing = errors.New("not found")
var ErrState = errors.New("invalid lifecycle state")
var ErrInvalid = errors.New("invalid request")

type Revision struct {
	Number   int            `json:"number"`
	Digest   string         `json:"digest"`
	Document scene.Document `json:"document"`
	Created  time.Time      `json:"created"`
}
type Plate struct {
	ID        string     `json:"id"`
	Title     string     `json:"title"`
	State     string     `json:"state"`
	Revisions []Revision `json:"revisions"`
}
type Artifact struct {
	ID          string `json:"id"`
	Plate       string `json:"plate"`
	Revision    int    `json:"revision"`
	SceneDigest string `json:"scene_digest"`
	Format      string `json:"format"`
	Digest      string `json:"digest"`
	State       string `json:"state"`
	Data        []byte `json:"data"`
}
type Proof struct {
	ID       string `json:"id"`
	Artifact string `json:"artifact"`
	State    string `json:"state"`
	Note     string `json:"note"`
}

// HandoverRef is one artifact lineage row inside a finalization handover
// manifest. A nil pointer serializes to null so a missing deliverable is an
// explicit, auditable gap rather than an omitted row. Every present reference
// carries the digests a receiver recomputes to verify the bytes and the scene.
type HandoverRef struct {
	ID          string `json:"id"`
	Format      string `json:"format"`
	Digest      string `json:"digest"`
	SceneDigest string `json:"scene_digest"`
	State       string `json:"state"`
}

// ProofRef is an accepted review record row. It pins both the proof identity and
// the artifact the review decided on, plus the artifact and scene digests, so the
// review-to-artifact-to-source-revision chain is independently re-checkable.
type ProofRef struct {
	ID          string `json:"id"`
	Artifact    string `json:"artifact"`
	Format      string `json:"format"`
	Digest      string `json:"digest"`
	SceneDigest string `json:"scene_digest"`
	Note        string `json:"note"`
	State       string `json:"state"`
}

// Handover is the finalized design handover manifest produced at archive time.
// It names the chosen revision (not assumed to be the latest), the explicitly
// selected PNG/SVG and accepted review records, and a policy describing how
// missing deliverables were handled. Complete is true only when every row is
// present. Digest covers the manifest content excluding the digest field itself.
type Handover struct {
	Plate       string       `json:"plate"`
	Revision    int          `json:"revision"`
	SceneDigest string       `json:"scene_digest"`
	PNG         *HandoverRef `json:"png"`
	SVG         *HandoverRef `json:"svg"`
	Proofs      []ProofRef   `json:"proofs"`
	OnMissing   string       `json:"on_missing"`
	Complete    bool         `json:"complete"`
	Created     time.Time    `json:"created"`
	Digest      string       `json:"digest"`
}

type State struct {
	Schema    string              `json:"schema"`
	Plates    map[string]Plate    `json:"plates"`
	Artifacts map[string]Artifact `json:"artifacts"`
	Proofs    map[string]Proof    `json:"proofs"`
	Handovers map[string]Handover `json:"handovers"`
}
type Repo struct {
	mu    sync.RWMutex
	root  string
	state State
	lock  *os.File
}

func Hash(b []byte) string              { v := sha256.Sum256(b); return hex.EncodeToString(v[:]) }
func SceneHash(d scene.Document) string { b, _ := json.Marshal(d); return Hash(b) }
func Fresh() State {
	return State{Schema: "prism-plate-v1", Plates: map[string]Plate{}, Artifacts: map[string]Artifact{}, Proofs: map[string]Proof{}, Handovers: map[string]Handover{}}
}

// HandoverHash is the self-check digest of a manifest. It hashes the canonical
// JSON of all manifest fields except Digest, so a receiver can recompute it
// without the value it is validating.
func HandoverHash(h Handover) string {
	h.Digest = ""
	b, _ := json.Marshal(h)
	return Hash(b)
}

// Ref builds a manifest artifact row from a stored artifact, pinning both the
// byte digest and the source scene digest.
func Ref(a Artifact) *HandoverRef {
	return &HandoverRef{ID: a.ID, Format: a.Format, Digest: a.Digest, SceneDigest: a.SceneDigest, State: a.State}
}

// RefProof builds a manifest review row from an accepted proof and the artifact
// it decided on, carrying the full proof-to-artifact-to-scene digest chain.
func RefProof(p Proof, a Artifact) ProofRef {
	return ProofRef{ID: p.ID, Artifact: a.ID, Format: a.Format, Digest: a.Digest, SceneDigest: a.SceneDigest, Note: p.Note, State: p.State}
}
func Open(root string) (*Repo, error) {
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, err
	}
	lock, err := os.OpenFile(filepath.Join(root, "writer.lock"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return nil, fmt.Errorf("data root already locked; after an unclean exit verify no writer remains before removing writer.lock: %w", err)
	}
	r := &Repo{root: root, state: Fresh(), lock: lock}
	fmt.Fprintln(lock, os.Getpid())
	b, err := os.ReadFile(filepath.Join(root, "state.json"))
	if err == nil {
		if len(b) > 128<<20 {
			err = fmt.Errorf("snapshot too large")
		} else {
			err = json.Unmarshal(b, &r.state)
		}
		if err == nil {
			// Snapshots sealed before handover manifests existed omit the map;
			// normalize to an empty one so later writes never hit a nil map.
			if r.state.Handovers == nil {
				r.state.Handovers = map[string]Handover{}
			}
			err = Check(r.state)
		}
	}
	if err != nil && !os.IsNotExist(err) {
		r.Close()
		return nil, err
	}
	return r, nil
}

// Check verifies internal lineage before a restored state can become authoritative.
func Check(s State) error {
	if s.Schema != "prism-plate-v1" || s.Plates == nil || s.Artifacts == nil || s.Proofs == nil {
		return fmt.Errorf("unsupported snapshot schema")
	}
	for id, p := range s.Plates {
		if p.ID != id || len(p.Revisions) == 0 || (p.State != "active" && p.State != "archived") {
			return fmt.Errorf("invalid plate lineage")
		}
		for i, r := range p.Revisions {
			if r.Number != i+1 || r.Digest != SceneHash(r.Document) {
				return fmt.Errorf("invalid revision digest")
			}
		}
	}
	for id, a := range s.Artifacts {
		p, ok := s.Plates[a.Plate]
		if !ok || a.ID != id || a.Revision < 1 || a.Revision > len(p.Revisions) || a.SceneDigest != p.Revisions[a.Revision-1].Digest || Hash(a.Data) != a.Digest || (a.State != "available" && a.State != "revoked") || (a.Format != "png" && a.Format != "svg") {
			return fmt.Errorf("invalid artifact lineage")
		}
	}
	for id, p := range s.Proofs {
		_, ok := s.Artifacts[p.Artifact]
		if p.ID != id || !ok || (p.State != "open" && p.State != "accepted" && p.State != "rejected") {
			return fmt.Errorf("invalid proof lineage")
		}
	}
	for id, h := range s.Handovers {
		if err := checkHandover(s, id, h); err != nil {
			return err
		}
	}
	return nil
}

// checkHandover verifies that a finalization manifest is internally consistent
// and derivable from the plate, artifact and proof facts in the snapshot: the
// plate must be archived, the chosen revision must exist with matching scene
// digest, every present row must resolve to a same-revision, same-digest fact,
// and the declared completeness and self-check digest must be accurate.
//
// The manifest is an immutable record of the finalization decision, so a PNG/SVG
// row keeps the available state captured at finalization even if that artifact is
// revoked later. Snapshot Check therefore pins identity, lineage and digests but
// does not demand current-state equality; the runtime verify endpoint compares
// against live facts and reports any later revocation. Accepted-proof rows are
// stricter because an accepted proof already forbids revoking its artifact, so
// that reference cannot diverge.
func checkHandover(s State, id string, h Handover) error {
	p, ok := s.Plates[h.Plate]
	if !ok || id != h.Plate || p.State != "archived" {
		return fmt.Errorf("invalid handover plate")
	}
	if h.Revision < 1 || h.Revision > len(p.Revisions) {
		return fmt.Errorf("invalid handover revision")
	}
	rev := p.Revisions[h.Revision-1]
	if h.SceneDigest != rev.Digest {
		return fmt.Errorf("invalid handover scene digest")
	}
	if h.OnMissing != "reject" && h.OnMissing != "exclude" {
		return fmt.Errorf("invalid handover missing policy")
	}
	if err := checkRef(s, h.Plate, h.Revision, rev.Digest, h.PNG, "png"); err != nil {
		return err
	}
	if err := checkRef(s, h.Plate, h.Revision, rev.Digest, h.SVG, "svg"); err != nil {
		return err
	}
	seenProof := map[string]bool{}
	for _, pr := range h.Proofs {
		if seenProof[pr.ID] {
			return fmt.Errorf("duplicate handover proof")
		}
		seenProof[pr.ID] = true
		proof, ok := s.Proofs[pr.ID]
		if !ok || proof.ID != pr.ID || proof.State != "accepted" || proof.Artifact != pr.Artifact {
			return fmt.Errorf("invalid handover proof")
		}
		artifact, ok := s.Artifacts[proof.Artifact]
		if !ok || artifact.Plate != h.Plate || artifact.Revision != h.Revision || artifact.State != "available" ||
			pr.Format != artifact.Format || pr.Digest != artifact.Digest || pr.SceneDigest != rev.Digest ||
			pr.State != "accepted" || pr.Note != proof.Note {
			return fmt.Errorf("invalid handover proof lineage")
		}
	}
	complete := h.PNG != nil && h.SVG != nil && len(h.Proofs) > 0
	if h.Complete != complete {
		return fmt.Errorf("invalid handover completeness")
	}
	if h.OnMissing == "reject" && !complete {
		return fmt.Errorf("reject policy requires a complete handover")
	}
	if h.Digest != HandoverHash(h) {
		return fmt.Errorf("invalid handover digest")
	}
	return nil
}

func checkRef(s State, plate string, revision int, sceneDigest string, r *HandoverRef, format string) error {
	if r == nil {
		return nil
	}
	a, ok := s.Artifacts[r.ID]
	// Identity, plate/revision placement, format and both digests must always
	// reconcile. The recorded row state is the finalization-time fact and may
	// legitimately lag a later revoke; runtime verify surfaces that divergence.
	if !ok || a.Plate != plate || a.Revision != revision || a.Format != format ||
		a.Digest != r.Digest || a.SceneDigest != sceneDigest || r.Format != format ||
		r.SceneDigest != sceneDigest || (r.State != "available" && r.State != "revoked") {
		return fmt.Errorf("invalid handover artifact lineage")
	}
	return nil
}
func clone(s State) State   { b, _ := json.Marshal(s); var d State; _ = json.Unmarshal(b, &d); return d }
func (r *Repo) View() State { r.mu.RLock(); defer r.mu.RUnlock(); return clone(r.state) }

// Update keeps failed persistence invisible. Rename is the commit point; directory
// sync is best-effort because failure after rename cannot be represented as rollback.
func (r *Repo) Update(fn func(*State) error) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.lock == nil {
		return ErrState
	}
	next := clone(r.state)
	if err := fn(&next); err != nil {
		return err
	}
	if err := Check(next); err != nil {
		return err
	}
	if len(next.Plates) > 128 || len(next.Artifacts) > 256 || len(next.Proofs) > 1024 {
		return fmt.Errorf("snapshot object capacity reached")
	}
	b, err := json.Marshal(next)
	if err != nil {
		return err
	}
	if len(b) > 128<<20 {
		return fmt.Errorf("snapshot byte capacity reached")
	}
	f, err := os.CreateTemp(r.root, "state-*.tmp")
	if err != nil {
		return err
	}
	name := f.Name()
	defer os.Remove(name)
	if _, err = f.Write(b); err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	if err = os.Rename(name, filepath.Join(r.root, "state.json")); err != nil {
		return err
	}
	r.state = next
	if dir, e := os.Open(r.root); e == nil {
		_ = dir.Sync()
		_ = dir.Close()
	}
	return nil
}
func (r *Repo) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.lock == nil {
		return nil
	}
	err := r.lock.Close()
	r.lock = nil
	if e := os.Remove(filepath.Join(r.root, "writer.lock")); err == nil {
		err = e
	}
	return err
}
