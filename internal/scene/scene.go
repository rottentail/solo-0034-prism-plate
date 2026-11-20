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

// Cell is one vector unit inside a repeatable pattern tile. Path is expressed
// in tile coordinates and may bleed across tile edges by at most one period.
type Cell struct {
	Path     []geom.Op `json:"path"`
	FillRule string    `json:"fill_rule"`
	Color    Color     `json:"color"`
}

// Pattern tiles small vector units without external images. Space selects the
// coordinate system the tile grid is locked to: "object" follows the shape
// transform, "canvas" stays fixed in plate coordinates.
type Pattern struct {
	Space  string     `json:"space"`
	Period geom.Point `json:"period"`
	Phase  geom.Point `json:"phase"`
	Cells  []Cell     `json:"cells"`
}
type Brush struct {
	Kind    string     `json:"kind"`
	Color   Color      `json:"color"`
	From    geom.Point `json:"from"`
	To      geom.Point `json:"to"`
	Stops   []Stop     `json:"stops,omitempty"`
	Pattern *Pattern   `json:"pattern,omitempty"`
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

// CompiledCell is a flattened pattern unit plus the periodic images that can
// reach into the base tile. Shifts always contains the zero vector.
type CompiledCell struct {
	Cell   Cell
	Path   geom.Path
	Shifts []geom.Point
}
type CompiledShape struct {
	Shape   Shape
	Path    geom.Path
	Inverse geom.Matrix
	Cells   []CompiledCell
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
