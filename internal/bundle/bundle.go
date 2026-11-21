// Package bundle seals portable scene history without private runtime state.
//
// Legacy bundles carry "prism-plate-bundle-v1" and are readable by both legacy
// and versioned services. Bundles whose history uses the versioned color model
// carry "prism-plate-bundle-v2"; a legacy service rejects that schema outright
// rather than reading documents whose color components it would misinterpret.
package bundle

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"prismplate/internal/repository"
	"prismplate/internal/validate"
)

const (
	SchemaV1 = "prism-plate-bundle-v1"
	SchemaV2 = "prism-plate-bundle-v2"
)

type Envelope struct {
	Schema string           `json:"schema"`
	Digest string           `json:"digest"`
	Plate  repository.Plate `json:"plate"`
}

// schema selects the narrowest envelope schema that preserves the history: v2
// only when at least one revision uses the explicit color model.
func schema(p repository.Plate) string {
	for _, r := range p.Revisions {
		if r.Document.ColorModel != nil {
			return SchemaV2
		}
	}
	return SchemaV1
}

func Seal(p repository.Plate) ([]byte, error) {
	b, err := json.Marshal(p)
	if err != nil {
		return nil, err
	}
	return json.Marshal(Envelope{Schema: schema(p), Digest: repository.Hash(b), Plate: p})
}
func Open(raw []byte) (repository.Plate, error) {
	var e Envelope
	if len(raw) > 8<<20 {
		return e.Plate, fmt.Errorf("bundle too large")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&e); err != nil {
		return e.Plate, err
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return e.Plate, fmt.Errorf("trailing bundle content")
	}
	b, err := json.Marshal(e.Plate)
	if err != nil {
		return e.Plate, err
	}
	if (e.Schema != SchemaV1 && e.Schema != SchemaV2) || repository.Hash(b) != e.Digest {
		return e.Plate, fmt.Errorf("bundle digest mismatch")
	}
	// The declared schema must describe the actual history. A v1 envelope that
	// contains a versioned revision is a down-conversion attempt and is refused;
	// a v2 envelope with only legacy revisions is merely unreasonably wide and
	// is also refused so bundles stay canonical.
	if schema(e.Plate) != e.Schema {
		return e.Plate, fmt.Errorf("bundle schema does not match its revisions")
	}
	s := repository.Fresh()
	if e.Schema == SchemaV2 {
		s.Schema = "prism-plate-v2"
	}
	s.Plates[e.Plate.ID] = e.Plate
	if err = repository.Check(s); err != nil {
		return e.Plate, err
	}
	if !validate.ID(e.Plate.ID) || len(e.Plate.Revisions) > 64 || len(e.Plate.Title) > 160 {
		return e.Plate, fmt.Errorf("invalid plate metadata")
	}
	for _, r := range e.Plate.Revisions {
		if _, err = validate.Compile(r.Document); err != nil {
			return e.Plate, err
		}
		if r.Digest != repository.RevisionDigest(r.Document) {
			return e.Plate, fmt.Errorf("revision digest mismatch")
		}
	}
	return e.Plate, nil
}
