// Package repository commits complete immutable snapshots under a single writer.
package repository

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"prismplate/internal/geom"
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
	Plate    string `json:"plate"`
	Artifact string `json:"artifact"`
	State    string `json:"state"`
	Note     string `json:"note"`
}

// Region is a rectangle in the canvas coordinate space of the proofed revision.
type Region struct {
	X float64 `json:"x"`
	Y float64 `json:"y"`
	W float64 `json:"w"`
	H float64 `json:"h"`
}

// Anchor pins an annotation to the shape under its region center when one exists.
// LocalRegion stores the rectangle in that shape's pre-transform coordinates so a
// later affine change can be replayed instead of silently pasted by shared id.
type Anchor struct {
	Layer        string      `json:"layer"`
	Shape        string      `json:"shape"`
	Transform    geom.Matrix `json:"transform"`
	CanvasWidth  int         `json:"canvas_width"`
	CanvasHeight int         `json:"canvas_height"`
	LocalRegion  Region      `json:"local_region"`
}
type Annotation struct {
	ID           string    `json:"id"`
	Proof        string    `json:"proof"`
	Plate        string    `json:"plate"`
	Revision     int       `json:"revision"`
	CanvasWidth  int       `json:"canvas_width"`
	CanvasHeight int       `json:"canvas_height"`
	Region       Region    `json:"region"`
	Anchor       *Anchor   `json:"anchor,omitempty"`
	Comment      string    `json:"comment"`
	State        string    `json:"state"`
	HandledBy    int       `json:"handled_by,omitempty"`
	Created      time.Time `json:"created"`
}
type State struct {
	Schema      string                `json:"schema"`
	Plates      map[string]Plate      `json:"plates"`
	Artifacts   map[string]Artifact   `json:"artifacts"`
	Proofs      map[string]Proof      `json:"proofs"`
	Annotations map[string]Annotation `json:"annotations"`
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
	return State{Schema: "prism-plate-v1", Plates: map[string]Plate{}, Artifacts: map[string]Artifact{}, Proofs: map[string]Proof{}, Annotations: map[string]Annotation{}}
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
			// Snapshots written before annotations existed lack the map; keep them loadable.
			if r.state.Annotations == nil {
				r.state.Annotations = map[string]Annotation{}
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

func SameMatrix(a, b geom.Matrix) bool {
	for i := range a {
		if math.Abs(a[i]-b[i]) > 1e-9 {
			return false
		}
	}
	return true
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
	for id, n := range s.Annotations {
		if n.ID != id {
			return fmt.Errorf("invalid annotation identity")
		}
		p, ok := s.Plates[n.Plate]
		if !ok || n.Revision < 1 || n.Revision > len(p.Revisions) {
			return fmt.Errorf("invalid annotation plate lineage")
		}
		pr, ok := s.Proofs[n.Proof]
		if !ok || pr.Plate != n.Plate {
			return fmt.Errorf("invalid annotation proof lineage")
		}
		if a, ok := s.Artifacts[pr.Artifact]; !ok || a.Revision != n.Revision {
			return fmt.Errorf("invalid annotation artifact lineage")
		}
		if n.State != "open" && n.State != "resolved" && n.State != "dismissed" {
			return fmt.Errorf("invalid annotation state")
		}
		if n.HandledBy != 0 && (n.HandledBy < 1 || n.HandledBy > len(p.Revisions) || n.HandledBy <= n.Revision || n.State == "open") {
			return fmt.Errorf("invalid annotation handling")
		}
		if n.Region.W <= 0 || n.Region.H <= 0 || !finiteRegion(n.Region) {
			return fmt.Errorf("invalid annotation region")
		}
		if n.Anchor != nil {
			if n.Anchor.LocalRegion.W <= 0 || n.Anchor.LocalRegion.H <= 0 || !finiteRegion(n.Anchor.LocalRegion) || n.Anchor.CanvasWidth < 1 || n.Anchor.CanvasHeight < 1 {
				return fmt.Errorf("invalid annotation anchor")
			}
		}
	}
	return nil
}
func finiteRegion(r Region) bool {
	for _, v := range []float64{r.X, r.Y, r.W, r.H} {
		if math.IsNaN(v) || math.IsInf(v, 0) || math.Abs(v) > 1e6 {
			return false
		}
	}
	return true
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
	if len(next.Plates) > 128 || len(next.Artifacts) > 256 || len(next.Proofs) > 1024 || len(next.Annotations) > 4096 {
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
