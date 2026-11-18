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

func Seal(p repository.Plate) ([]byte, error) {
	b, err := json.Marshal(p)
	if err != nil {
		return nil, err
	}
	return json.Marshal(Envelope{Schema: "prism-plate-bundle-v1", Digest: repository.Hash(b), Plate: p})
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
	if e.Schema != "prism-plate-bundle-v1" || repository.Hash(b) != e.Digest {
		return e.Plate, fmt.Errorf("bundle digest mismatch")
	}
	s := repository.Fresh()
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
	}
	return e.Plate, nil
}
