// Package scene defines the portable prism-plate-v1 document contract.
package scene

import (
	"math"
	"prismplate/internal/geom"
	"strconv"
)

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

// MaxOutputEdge bounds an explicit render target on each side.
const MaxOutputEdge = 1024

// Target requests an output size with proportional fit adaptation. A nil
// *Target keeps the document pixel size and the legacy render behaviour.
type Target struct {
	Width  int    `json:"width"`
	Height int    `json:"height"`
	Fit    string `json:"fit"`
}

// Valid reports the structural contract: contain scales the document to fit
// inside the frame with transparent margins, cover scales to fill the frame
// and crops the overflow.
func (t Target) Valid() bool {
	if t.Width < 1 || t.Height < 1 || t.Width > MaxOutputEdge || t.Height > MaxOutputEdge {
		return false
	}
	return t.Fit == "contain" || t.Fit == "cover"
}

// Key is the canonical identity component for artifact provenance.
func (t Target) Key() string {
	return strconv.Itoa(t.Width) + "x" + strconv.Itoa(t.Height) + ":" + t.Fit
}

// Matrix maps document coordinates to output pixels: one uniform scale plus a
// centering offset, so brushes, clips, masks and cutouts share the same layout.
func (t Target) Matrix(dw, dh int) geom.Matrix {
	sx := float64(t.Width) / float64(dw)
	sy := float64(t.Height) / float64(dh)
	scale := math.Min(sx, sy)
	if t.Fit == "cover" {
		scale = math.Max(sx, sy)
	}
	return geom.Matrix{scale, 0, 0, scale, (float64(t.Width) - float64(dw)*scale) / 2, (float64(t.Height) - float64(dh)*scale) / 2}
}
