// Package scene defines the portable prism-plate-v1 document contract.
package scene

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"

	"prismplate/internal/geom"
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

// Clone deep-copies a compiled draft, including every expanded contour and the
// embedded source document, so readers never share mutable slices with a caller.
func (c Compiled) Clone() Compiled {
	out := Compiled{Segments: c.Segments}
	out.Document = c.Document.Clone()
	if c.Layers != nil {
		out.Layers = make([]CompiledLayer, len(c.Layers))
		for i, l := range c.Layers {
			cl := CompiledLayer{Layer: l.Layer.Clone(), Clip: l.Clip.Clone(), Mask: l.Mask.Clone()}
			if l.Shapes != nil {
				cl.Shapes = make([]CompiledShape, len(l.Shapes))
				for j, s := range l.Shapes {
					cl.Shapes[j] = CompiledShape{Shape: s.Shape.Clone(), Path: s.Path.Clone(), Inverse: s.Inverse}
				}
			}
			out.Layers[i] = cl
		}
	}
	return out
}

// Clone deep-copies the document so revisions and captures are value facts.
func (d Document) Clone() Document {
	out := Document{Width: d.Width, Height: d.Height, Background: d.Background}
	if d.Layers != nil {
		out.Layers = make([]Layer, len(d.Layers))
		for i, l := range d.Layers {
			out.Layers[i] = l.Clone()
		}
	}
	return out
}
func (l Layer) Clone() Layer {
	out := Layer{ID: l.ID, Opacity: l.Opacity, Blend: l.Blend, Clip: geom.CloneOps(l.Clip), Mask: geom.CloneOps(l.Mask)}
	if l.Shapes != nil {
		out.Shapes = make([]Shape, len(l.Shapes))
		for i, s := range l.Shapes {
			out.Shapes[i] = s.Clone()
		}
	}
	return out
}
func (s Shape) Clone() Shape {
	out := Shape{ID: s.ID, FillRule: s.FillRule, Brush: s.Brush.Clone(), Transform: s.Transform, Path: geom.CloneOps(s.Path)}
	return out
}
func (b Brush) Clone() Brush {
	out := Brush{Kind: b.Kind, Color: b.Color, From: b.From, To: b.To}
	if b.Stops != nil {
		out.Stops = append([]Stop(nil), b.Stops...)
	}
	return out
}

// Digest is the canonical identity of a source document. Compilation and
// persistence both key off these bytes, so a revision's audit digest and its
// compiled draft always describe the same value.
func (d Document) Digest() string {
	b, err := json.Marshal(d)
	if err != nil {
		return ""
	}
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}
