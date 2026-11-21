// Package scene defines the portable prism-plate-v1 document contract.
package scene

import "prismplate/internal/geom"

// Color is a four-component color whose meaning depends on Document.ColorModel.
//
// Legacy documents (ColorModel omitted) store sRGB-encoded, straight
// (unpremultiplied) R,G,B with a separate alpha, the prism-plate-v1 contract.
// Versioned documents (ColorModel version 2) store linear-light, premultiplied
// R,G,B with a separate alpha.
type Color struct {
	R float64 `json:"r"`
	G float64 `json:"g"`
	B float64 `json:"b"`
	A float64 `json:"a"`
}

// ColorModel is the explicit marker for versioned color semantics. It is
// omitted on legacy revisions, which therefore keep their original JSON bytes
// and scene digests.
type ColorModel struct {
	Version int    `json:"version"`
	Space   string `json:"space"`
	Alpha   string `json:"alpha"`
}

type Model int

const (
	// StraightSRGB is the implicit prism-plate-v1 model: sRGB-encoded,
	// straight color interpolated by the renderer after linearization and
	// premultiplication.
	StraightSRGB Model = iota
	// LinearPremul is prism-plate-v2: linear-light, premultiplied color.
	LinearPremul
)

func (d Document) ModelKind() Model {
	if d.ColorModel == nil {
		return StraightSRGB
	}
	return LinearPremul
}

type Stop struct {
	At    float64 `json:"at"`
	Color Color   `json:"color"`
}
type Brush struct {
	Kind  string     `json:"kind"`
	Color Color      `json:"color"`
	From  geom.Point `json:"from"`
	To    geom.Point `json:"to"`
	Stops []Stop     `json:"stops,omitempty"`
}
type Shape struct {
	ID        string      `json:"id"`
	Path      []geom.Op   `json:"path"`
	FillRule  string      `json:"fill_rule"`
	Brush     Brush       `json:"brush"`
	Transform geom.Matrix `json:"transform"`
}
type Layer struct {
	ID      string    `json:"id"`
	Opacity float64   `json:"opacity"`
	Blend   string    `json:"blend"`
	Clip    []geom.Op `json:"clip,omitempty"`
	Mask    []geom.Op `json:"mask,omitempty"`
	Shapes  []Shape   `json:"shapes"`
}
type Document struct {
	Width      int         `json:"width"`
	Height     int         `json:"height"`
	Background Color       `json:"background"`
	ColorModel *ColorModel `json:"color_model,omitempty"`
	Layers     []Layer     `json:"layers"`
}
type CompiledShape struct {
	Shape   Shape
	Path    geom.Path
	Inverse geom.Matrix
}
type CompiledLayer struct {
	Layer  Layer
	Clip   geom.Path
	Mask   geom.Path
	Shapes []CompiledShape
}
type Compiled struct {
	Document Document
	Layers   []CompiledLayer
	Segments int
}
