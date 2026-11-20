// Package compile owns the boundary between an editable source document and the
// fixed draft that proof output reads.
//
// A draft is compiled once per (document digest, algorithm) pair and is an
// immutable value: expansion copies every contour, so neither later edits to the
// source document nor a reader's own mutations can reach another reader. PNG and
// SVG consume the same draft, each keeping its own coordinate semantics.
//
// Ownership rules:
//   - Only a captured revision may become a draft; its audit digest must match
//     the document value it carries, otherwise ErrUnadmitted is returned.
//   - The cache key is derived from the document bytes, never from a caller
//     label, so an edited path, gradient stop or canvas clip cannot hit an older
//     draft.
//   - Drafts handed out by the cache are shared read-only values. Raster and
//     vector must not write to them; a caller that stores or exports one itself
//     must use scene.Compiled.Clone.
package compile

import (
	"errors"

	"prismplate/internal/scene"
	"prismplate/internal/validate"
)

// Algorithm names the expansion contract. It is part of every draft identity, so
// changing a subdivision rule cannot silently reuse an older expansion.
const Algorithm = "flatten-q16-c24-v1"

// ErrUnadmitted reports a captured revision whose audit digest does not match
// the document it carries. Such a source never reaches expansion.
var ErrUnadmitted = errors.New("captured revision digest mismatch")

// Key is the identity of a compiled draft, derived only from immutable facts.
type Key struct {
	SceneDigest string
	Algorithm   string
}

// KeyFor derives a draft identity from an audited scene digest.
func KeyFor(sceneDigest string) Key {
	return Key{SceneDigest: sceneDigest, Algorithm: Algorithm}
}

// Source is a captured revision: the only input from which a draft may be built.
// It carries the audit facts alongside the immutable document value.
type Source struct {
	Number   int
	Digest   string
	Document scene.Document
}

// Draft is the frozen expansion both output backends read.
type Draft = scene.Compiled

// Compile admits the document and returns an independent draft. The document is
// deep-copied before expansion, so later edits to the caller's value cannot
// reach the compiled result.
func Compile(d scene.Document) (Draft, error) {
	c, err := validate.Compile(d.Clone())
	if err != nil {
		return Draft{}, err
	}
	return c, nil
}

// CompileSource compiles a captured revision after checking that its audit
// digest matches the document value. A mismatch is a lineage fault, not a
// render error, and never reaches expansion.
func CompileSource(s Source) (Draft, error) {
	if s.Digest != "" && s.Digest != s.Document.Digest() {
		return Draft{}, ErrUnadmitted
	}
	return Compile(s.Document)
}

// Resolve returns the draft for a captured revision, reusing the bounded cache
// when the same document has been rendered before.
//
// The key is always the digest of the document value itself, so a stale or
// forged Digest label cannot select another document's draft. When the label is
// present it must agree, otherwise ErrUnadmitted is returned before any lookup.
func Resolve(c *Cache, s Source) (Draft, error) {
	digest := s.Document.Digest()
	if s.Digest != "" && s.Digest != digest {
		return Draft{}, ErrUnadmitted
	}
	build := func() (Draft, error) { return Compile(s.Document) }
	if c == nil {
		return build()
	}
	return c.Get(KeyFor(digest), build)
}
