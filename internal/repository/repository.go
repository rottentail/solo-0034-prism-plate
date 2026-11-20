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
	"prismplate/internal/variant"
	"reflect"
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

// Origin pins the template provenance of a batch-resolved draft plate. The
// frozen document lives in revision 1; Origin explains which template
// revision and parameter set produced it.
type Origin struct {
	Batch    string             `json:"batch"`
	Template string             `json:"template"`
	Revision int                `json:"revision"`
	Digest   string             `json:"digest"`
	Label    string             `json:"label"`
	Params   map[string]float64 `json:"params"`
}
type Plate struct {
	ID        string     `json:"id"`
	Title     string     `json:"title"`
	State     string     `json:"state"`
	Revisions []Revision `json:"revisions"`
	Origin    *Origin    `json:"origin,omitempty"`
}
type TemplateRevision struct {
	Number     int                `json:"number"`
	Digest     string             `json:"digest"`
	Definition variant.Definition `json:"definition"`
	Created    time.Time          `json:"created"`
}
type Template struct {
	ID        string             `json:"id"`
	Title     string             `json:"title"`
	State     string             `json:"state"`
	Revisions []TemplateRevision `json:"revisions"`
}

// BatchItem is one submitted parameter set. Resolved items carry the frozen
// document and its draft plate; rejected items carry only the reason.
type BatchItem struct {
	Label    string             `json:"label"`
	Params   map[string]float64 `json:"params"`
	State    string             `json:"state"`
	Reason   string             `json:"reason,omitempty"`
	Digest   string             `json:"digest,omitempty"`
	Plate    string             `json:"plate,omitempty"`
	Document *scene.Document    `json:"document,omitempty"`
}
type Batch struct {
	ID             string      `json:"id"`
	Template       string      `json:"template"`
	Revision       int         `json:"revision"`
	TemplateDigest string      `json:"template_digest"`
	Items          []BatchItem `json:"items"`
	Created        time.Time   `json:"created"`
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
type State struct {
	Schema    string              `json:"schema"`
	Plates    map[string]Plate    `json:"plates"`
	Artifacts map[string]Artifact `json:"artifacts"`
	Proofs    map[string]Proof    `json:"proofs"`
	Templates map[string]Template `json:"templates"`
	Batches   map[string]Batch    `json:"batches"`
}
type Repo struct {
	mu    sync.RWMutex
	root  string
	state State
	lock  *os.File
}

func Hash(b []byte) string              { v := sha256.Sum256(b); return hex.EncodeToString(v[:]) }
func SceneHash(d scene.Document) string { b, _ := json.Marshal(d); return Hash(b) }
func DefinitionHash(d variant.Definition) string {
	b, _ := json.Marshal(d)
	return Hash(b)
}
func Fresh() State {
	return State{Schema: "prism-plate-v1", Plates: map[string]Plate{}, Artifacts: map[string]Artifact{}, Proofs: map[string]Proof{}, Templates: map[string]Template{}, Batches: map[string]Batch{}}
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
			// Snapshots written before templates and batches existed restore as empty sets.
			if r.state.Templates == nil {
				r.state.Templates = map[string]Template{}
			}
			if r.state.Batches == nil {
				r.state.Batches = map[string]Batch{}
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
	if s.Schema != "prism-plate-v1" || s.Plates == nil || s.Artifacts == nil || s.Proofs == nil || s.Templates == nil || s.Batches == nil {
		return fmt.Errorf("unsupported snapshot schema")
	}
	for id, t := range s.Templates {
		if t.ID != id || len(t.Revisions) == 0 || len(t.Revisions) > 32 || (t.State != "active" && t.State != "archived") {
			return fmt.Errorf("invalid template lineage")
		}
		for i, r := range t.Revisions {
			if r.Number != i+1 || r.Digest != DefinitionHash(r.Definition) {
				return fmt.Errorf("invalid template revision digest")
			}
		}
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
		// Draft provenance is enforced against the batch record when the batch
		// lives in this instance; an imported bundle carries it as declared
		// provenance because the template may not exist here.
		if o := p.Origin; o != nil {
			b, ok := s.Batches[o.Batch]
			if !ok {
				continue
			}
			if b.Template != o.Template || b.Revision != o.Revision || b.TemplateDigest != o.Digest {
				return fmt.Errorf("invalid draft origin")
			}
			found := false
			for _, it := range b.Items {
				if it.Label == o.Label && it.State == "resolved" && it.Plate == p.ID && it.Digest == p.Revisions[0].Digest && reflect.DeepEqual(it.Params, o.Params) {
					found = true
				}
			}
			if !found {
				return fmt.Errorf("invalid draft origin")
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
	for id, b := range s.Batches {
		t, ok := s.Templates[b.Template]
		if !ok || b.ID != id || b.Revision < 1 || b.Revision > len(t.Revisions) || b.TemplateDigest != t.Revisions[b.Revision-1].Digest || len(b.Items) == 0 || len(b.Items) > 24 {
			return fmt.Errorf("invalid batch lineage")
		}
		labels := map[string]bool{}
		for _, it := range b.Items {
			if labels[it.Label] {
				return fmt.Errorf("invalid batch item")
			}
			labels[it.Label] = true
			switch it.State {
			case "resolved":
				if it.Document == nil || it.Reason != "" || it.Digest == "" || SceneHash(*it.Document) != it.Digest {
					return fmt.Errorf("invalid batch item lineage")
				}
				p, ok := s.Plates[it.Plate]
				if !ok || p.Revisions[0].Digest != it.Digest || p.Origin == nil || p.Origin.Batch != id || p.Origin.Label != it.Label {
					return fmt.Errorf("invalid batch item lineage")
				}
			case "rejected":
				if it.Reason == "" || it.Digest != "" || it.Plate != "" || it.Document != nil {
					return fmt.Errorf("invalid batch item lineage")
				}
			default:
				return fmt.Errorf("invalid batch item state")
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
	if len(next.Plates) > 128 || len(next.Artifacts) > 256 || len(next.Proofs) > 1024 || len(next.Templates) > 64 || len(next.Batches) > 64 {
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
