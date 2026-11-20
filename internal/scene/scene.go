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

// Keyframe carries the value of its track's channel at one instant. Admission
// requires every keyframe on a track to define exactly that track's channel;
// values before the first and after the last keyframe are held, not extrapolated.
type Keyframe struct {
	At       float64     `json:"at"`
	Position *geom.Point `json:"position,omitempty"`
	Rotation *float64    `json:"rotation,omitempty"`
	ScaleX   *float64    `json:"scale_x,omitempty"`
	ScaleY   *float64    `json:"scale_y,omitempty"`
	Opacity  *float64    `json:"opacity,omitempty"`
}

// Track binds keyframes of one channel kind to one shape. Kind is one of
// position, rotation, scale or opacity.
type Track struct {
	ID        string     `json:"id"`
	Target    string     `json:"target"`
	Kind      string     `json:"kind"`
	Keyframes []Keyframe `json:"keyframes"`
}

// Timeline is the portable time description stored inside a document, so it is
// saved with revisions, sealed into bundles and recompiled on recovery exactly
// like every other scene fact.
type Timeline struct {
	Duration float64 `json:"duration"`
	Tracks   []Track `json:"tracks"`
}

type Document struct {
	Width      int       `json:"width"`
	Height     int       `json:"height"`
	Background Color     `json:"background"`
	Layers     []Layer   `json:"layers"`
	Timeline   *Timeline `json:"timeline,omitempty"`
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
