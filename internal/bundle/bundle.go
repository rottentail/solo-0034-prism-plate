// Package bundle seals portable scene history without private runtime state.
package bundle

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"prismplate/internal/repository"
	"prismplate/internal/validate"
)

type Envelope struct {
	Schema string           `json:"schema"`
	Digest string           `json:"digest"`
	Plate  repository.Plate `json:"plate"`
}

// sealDigest preserves the historical envelope algorithm verbatim: hash the
// plate exactly as stored. Bundles written before the svg-path-1 migration
// therefore keep byte-identical envelope digests and still verify.
func sealDigest(p repository.Plate) string {
	b, err := json.Marshal(p)
	if err != nil {
		return ""
	}
	return repository.Hash(b)
}

// normalizedDigest hashes the plate after forcing every document to the legacy
// op-array spelling. It only backs verification of envelopes produced by a
// compliant vector-tool exchange that rewrote path_text while preserving the
// normalized geometry; this service never emits such envelopes itself.
func normalizedDigest(p repository.Plate) string {
	clone := p
	clone.Revisions = make([]repository.Revision, len(p.Revisions))
	for i, r := range p.Revisions {
		r.Digest = repository.SceneHash(r.Document)
		r.Document = repository.NormalizedDocument(r.Document)
		clone.Revisions[i] = r
	}
	b, err := json.Marshal(clone)
	if err != nil {
		return ""
	}
	return repository.Hash(b)
}

func Seal(p repository.Plate) ([]byte, error) {
	if _, err := json.Marshal(p); err != nil {
		return nil, err
	}
	return json.Marshal(Envelope{Schema: "prism-plate-bundle-v1", Digest: sealDigest(p), Plate: p})
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
	if e.Schema != "prism-plate-bundle-v1" ||
		(sealDigest(e.Plate) != e.Digest && normalizedDigest(e.Plate) != e.Digest) {
		return e.Plate, fmt.Errorf("bundle digest mismatch")
	}
	s := repository.Fresh()
	s.Plates[e.Plate.ID] = e.Plate
	if err := repository.Check(s); err != nil {
		return e.Plate, err
	}
	if !validate.ID(e.Plate.ID) || len(e.Plate.Revisions) > 64 || len(e.Plate.Title) > 160 {
		return e.Plate, fmt.Errorf("invalid plate metadata")
	}
	for _, r := range e.Plate.Revisions {
		if _, err := validate.Compile(r.Document); err != nil {
			return e.Plate, err
		}
	}
	return e.Plate, nil
}
