// Dual path representations for the svg-path-1 migration.
//
// A PathRef is one contour source in exactly one of two wire spellings:
//
//   - legacy: the historical []geom.Op object array, JSON field "path"
//     (snapshots and bundles written before the migration keep this shape);
//   - versioned: an SVG path data string tagged "svg-path-1", JSON fields
//     "path_format" plus "path_text".
//
// Decoding either spelling normalizes to the same absolute geom.Op slice, so
// the raster, vector and validation pipelines have no second geometry path to
// trust. The originating spelling is retained for provenance summaries; it is
// never part of the content digest, which is computed from the normalized
// document exactly as before.
package scene

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"prismplate/internal/geom"
)

// strictDecode mirrors the transport contract inside nested UnmarshalJSON
// methods: unknown fields and trailing tokens are rejected. json.Unmarshal
// itself cannot carry DisallowUnknownFields, so every nested decoder uses it.
func strictDecode(b []byte, v any) error {
	d := json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	if err := d.Decode(v); err != nil {
		return err
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return fmt.Errorf("trailing JSON value")
	}
	return nil
}

// PathFormatSVG identifies the versioned SVG path text contract.
const PathFormatSVG = "svg-path-1"

// PathSource states which wire representation a PathRef was decoded from.
const (
	PathSourceLegacy = "legacy-op-array" // historical []geom.Op object array
	PathSourceSVG    = PathFormatSVG     // versioned SVG path text
)

// PathRef carries one contour source. Exactly one representation is populated
// on the wire; Ops always holds the normalized absolute contour after decode.
type PathRef struct {
	// Source records the decoded representation for provenance and error
	// attribution. It is derived, never accepted from JSON.
	Source string `json:"-"`
	// Ops is the normalized absolute M/L/Q/C/Z operation sequence used by
	// every downstream pipeline.
	Ops []geom.Op `json:"-"`
	// Text is the original svg-path-1 spelling, retained verbatim for
	// provenance; empty for legacy op-array sources.
	Text string `json:"-"`
}

// MarshalJSON emits the representation the instance was decoded from. Legacy
// data therefore round-trips byte-for-byte through snapshots and bundles; new
// svg-path-1 requests stay versioned text and never silently become op arrays.
func (p PathRef) MarshalJSON() ([]byte, error) {
	if p.Source == PathFormatSVG {
		return json.Marshal(struct {
			Format string `json:"path_format"`
			Text   string `json:"path_text"`
		}{PathFormatSVG, p.Text})
	}
	return json.Marshal(p.Ops)
}

// UnmarshalJSON accepts exactly one of the two contracted spellings. When
// PathRef is embedded as a field named "path" it receives the value directly
// (an op array or a versioned object); when used inside layer clip/mask it
// receives the whole object. Both positions accept the same grammar. Both
// representations supplied, or neither, is a hard contract error.
func (p *PathRef) UnmarshalJSON(b []byte) error {
	trimmed := bytes.TrimSpace(b)
	if len(trimmed) == 0 || string(trimmed) == "null" {
		return fmt.Errorf("contour requires path (legacy op array) or path_format/path_text (svg-path-1)")
	}
	if trimmed[0] == '[' {
		// Historical spelling: the M/L/Q/C/Z object array itself.
		var ops []geom.Op
		if err := strictDecode(b, &ops); err != nil {
			return err
		}
		if len(ops) == 0 {
			return fmt.Errorf("legacy op array must not be empty")
		}
		p.Source = PathSourceLegacy
		p.Ops = ops
		p.Text = ""
		return nil
	}
	var probe map[string]json.RawMessage
	if err := strictDecode(b, &probe); err != nil {
		return err
	}
	_, hasLegacy := probe["path"]
	_, hasFormat := probe["path_format"]
	_, hasText := probe["path_text"]
	hasVersioned := hasFormat || hasText
	if hasLegacy && hasVersioned {
		return fmt.Errorf("path representations are mutually exclusive: use either path or path_format/path_text")
	}
	if !hasLegacy && !hasVersioned {
		return fmt.Errorf("contour requires path (legacy op array) or path_format/path_text (svg-path-1)")
	}
	if hasLegacy {
		var wrapper struct {
			Path []geom.Op `json:"path"`
		}
		if err := strictDecode(b, &wrapper); err != nil {
			return err
		}
		if len(wrapper.Path) == 0 {
			return fmt.Errorf("legacy op array must not be empty")
		}
		p.Source = PathSourceLegacy
		p.Ops = wrapper.Path
		p.Text = ""
		return nil
	}
	var versioned struct {
		Format string `json:"path_format"`
		Text   string `json:"path_text"`
	}
	if err := strictDecode(b, &versioned); err != nil {
		return err
	}
	if versioned.Format != PathFormatSVG || versioned.Text == "" {
		return fmt.Errorf("versioned contour requires path_format=%q and non-empty path_text", PathFormatSVG)
	}
	ops, err := geom.ParseSVGPath(versioned.Text)
	if err != nil {
		// Keep the structured ParseError (with byte/column location) intact.
		return err
	}
	p.Source = PathFormatSVG
	p.Ops = ops
	p.Text = versioned.Text
	return nil
}

// PathSummary is the provenance description of one decoded contour source.
type PathSummary struct {
	Role      string `json:"role"`
	Node      string `json:"node"`
	Source    string `json:"source"`
	Ops       int    `json:"ops"`
	Subpaths  int    `json:"subpaths"`
	TextBytes int    `json:"text_bytes,omitempty"`
}

// Summarize reports which representation fed the normalized contour. TextBytes
// is zero for legacy op arrays, which carry no path text.
func (p PathRef) Summarize(role, node string) PathSummary {
	s := PathSummary{Role: role, Node: node, Source: p.Source, Ops: len(p.Ops)}
	for _, o := range p.Ops {
		if o.Kind == "M" {
			s.Subpaths++
		}
	}
	if p.Source == PathFormatSVG {
		s.TextBytes = len(p.Text)
	}
	return s
}

// ContourSummary lists the provenance of every contour in a document, in
// document order. It answers "which representation fed this scene" without
// exposing internal compilation state; the content digest still covers the
// normalized geometry only.
func ContourSummary(d Document) []PathSummary {
	var out []PathSummary
	for _, l := range d.Layers {
		if l.Clip != nil {
			out = append(out, l.Clip.Summarize("clip", l.ID))
		}
		if l.Mask != nil {
			out = append(out, l.Mask.Summarize("mask", l.ID))
		}
		for _, s := range l.Shapes {
			out = append(out, s.Path.Summarize("path", l.ID+"/"+s.ID))
		}
	}
	return out
}
