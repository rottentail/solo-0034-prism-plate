// Package scene defines the portable prism-plate-v1 document contract.
package scene

import "prismplate/internal/geom"

type Color struct {
	R float64 `json:"r"`
	G float64 `json:"g"`
	B float64 `json:"b"`
	A float64 `json:"a"`
}

// Spot is a named spot-color ink shared by every brush that references it.
// ProofColor is the sRGB approximation used only for on-screen recompositing;
// the coverage plates themselves carry geometry-derived alpha, never RGB channels.
type Spot struct {
	ID         string `json:"id"`
	Name       string `json:"name"`
	ProofColor Color  `json:"proof_color"`
}

type Stop struct {
	At    float64 `json:"at"`
	Color Color   `json:"color"`
}
type Brush struct {
	Kind  string     `json:"kind"`
	Color Color      `json:"color"`
	Spot  string     `json:"spot,omitempty"`
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
	// Overprint keeps underlying spot-ink coverage where this shape prints,
	// instead of knocking it out. Absent/false means knockout.
	Overprint bool `json:"overprint,omitempty"`
}
type Layer struct {
	ID      string    `json:"id"`
	Opacity float64   `json:"opacity"`
	Blend   string    `json:"blend"`
	Clip    []geom.Op `json:"clip,omitempty"`
	Mask    []geom.Op `json:"mask,omitempty"`
	// Overprint is the layer-level default for shapes that do not set their own flag.
	Overprint bool    `json:"overprint,omitempty"`
	Shapes    []Shape `json:"shapes"`
}
type Document struct {
	Width      int     `json:"width"`
	Height     int     `json:"height"`
	Background Color   `json:"background"`
	Spots      []Spot  `json:"spots,omitempty"`
	Layers     []Layer `json:"layers"`
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
	// SpotColors maps a spot ink id to its shared sRGB proof color.
	SpotColors map[string]Color
}
