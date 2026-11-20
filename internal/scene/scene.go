// Package scene defines the portable prism-plate-v1 document contract.
package scene

import "prismplate/internal/geom"

type Color struct {
	R float64 `json:"r"`
	G float64 `json:"g"`
	B float64 `json:"b"`
	A float64 `json:"a"`
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
	Width      int     `json:"width"`
	Height     int     `json:"height"`
	Background Color   `json:"background"`
	Layers     []Layer `json:"layers"`
}
type CompiledShape struct {
	Shape   Shape
	Path    geom.Path
	Inverse geom.Matrix
	// Visible means the transformed fill bbox meets the sampled canvas; the
	// rasterizer never evaluates an invisible shape against any pixel sample.
	Visible bool
}
type CompiledLayer struct {
	Layer  Layer
	Clip   geom.Path
	Mask   geom.Path
	Shapes []CompiledShape
	// Visible means every applicable clip/mask bbox meets the canvas, so at
	// least one of the layer's shapes can be evaluated at a sample. An
	// invisible layer is skipped wholesale by the rasterizer.
	Visible bool
}
type Compiled struct {
	Document Document
	Layers   []CompiledLayer
	// Segments counts every flattened edge, including provably invisible
	// geometry, and bounds the one-time flatten/export work.
	Segments int
	// VisibleSegments counts edges the rasterizer can actually traverse at a
	// sample; it is the basis of the bounded rendering budget.
	VisibleSegments int
}
