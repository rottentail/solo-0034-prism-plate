// Package scene defines the portable prism-plate-v1 document contract.
package scene

import "prismplate/internal/geom"

// Region is an integer pixel rectangle in document canvas coordinates. It is a
// render request scope, not scene state: x/y name the canvas pixel that becomes
// the first output pixel, and width/height are output pixel counts.
type Region struct {
	X      int `json:"x"`
	Y      int `json:"y"`
	Width  int `json:"width"`
	Height int `json:"height"`
}

// Within reports whether the region is non-empty and lies fully inside a
// width by height canvas. Empty or partially outside regions are not cropped.
func (r Region) Within(width, height int) bool {
	if r.X < 0 || r.Y < 0 || r.Width < 1 || r.Height < 1 || r.X > width || r.Y > height {
		return false
	}
	return r.Width <= width-r.X && r.Height <= height-r.Y
}

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
