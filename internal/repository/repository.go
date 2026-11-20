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

// ReviewSide is one deliverable's conclusion inside a dual-artifact review.
// Role is derived from the artifact format: pixel for png (authoritative
// pixels), geometry for svg (editable geometry).
type ReviewSide struct {
	Artifact string `json:"artifact"`
	Role     string `json:"role"`
	State    string `json:"state"`
	Note     string `json:"note"`
}

// Review joins the png and svg of one draft (same plate, same revision) into
// a single sign-off session. State is the derived verdict of its two sides.
type Review struct {
	ID       string       `json:"id"`
	Plate    string       `json:"plate"`
	Revision int          `json:"revision"`
	Sides    []ReviewSide `json:"sides"`
	State    string       `json:"state"`
}
type State struct {
	Schema    string              `json:"schema"`
	Plates    map[string]Plate    `json:"plates"`
	Artifacts map[string]Artifact `json:"artifacts"`
	Proofs    map[string]Proof    `json:"proofs"`
	Reviews   map[string]Review   `json:"reviews"`
}
type Repo struct {
	mu    sync.RWMutex
	root  string
	state State
	lock  *os.File
}

func Hash(b []byte) string              { v := sha256.Sum256(b); return hex.EncodeToString(v[:]) }
func SceneHash(d scene.Document) string { b, _ := json.Marshal(d); return Hash(b) }

// RoleFor names the review annotation of a deliverable format: png carries the
// authoritative pixel result, svg carries geometric editability.
func RoleFor(format string) string {
	if format == "svg" {
		return "geometry"
	}
	return "pixel"
}

// ReviewVerdict derives the session outcome from its side conclusions: any
// pending side keeps the session open, two accepts pass it, two returns send
// it back, and a split conclusion is partial.
func ReviewVerdict(sides []ReviewSide) string {
	accepted, returned := 0, 0
	for _, side := range sides {
		switch side.State {
		case "accepted":
			accepted++
		case "returned":
			returned++
		}
	}
	if accepted+returned < len(sides) {
		return "open"
	}
	if returned == 0 {
		return "accepted"
	}
	if accepted == 0 {
		return "returned"
	}
	return "partial"
}
func Fresh() State {
	return State{Schema: "prism-plate-v1", Plates: map[string]Plate{}, Artifacts: map[string]Artifact{}, Proofs: map[string]Proof{}, Reviews: map[string]Review{}}
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
		if err == nil && r.state.Reviews == nil {
			// Snapshots written before dual-artifact reviews carry no reviews key.
			r.state.Reviews = map[string]Review{}
		}
		if err == nil {
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
	for id, r := range s.Reviews {
		if r.ID != id || len(r.Sides) != 2 || r.Sides[0].Artifact == r.Sides[1].Artifact || r.State != ReviewVerdict(r.Sides) {
			return fmt.Errorf("invalid review lineage")
		}
		first, ok1 := s.Artifacts[r.Sides[0].Artifact]
		second, ok2 := s.Artifacts[r.Sides[1].Artifact]
		if !ok1 || !ok2 || first.Format == second.Format || first.Plate != r.Plate || second.Plate != r.Plate || first.Revision != r.Revision || second.Revision != r.Revision {
			return fmt.Errorf("invalid review lineage")
		}
		for _, side := range r.Sides {
			a := s.Artifacts[side.Artifact]
			if side.Role != RoleFor(a.Format) || (side.State != "pending" && side.State != "accepted" && side.State != "returned") || len(side.Note) > 1000 {
				return fmt.Errorf("invalid review lineage")
			}
		}
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
	if len(next.Plates) > 128 || len(next.Artifacts) > 256 || len(next.Proofs) > 1024 || len(next.Reviews) > 1024 {
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
