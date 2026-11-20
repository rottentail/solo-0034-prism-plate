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

// Projective maps a source quadrilateral onto Quad with a true perspective
// transform. Quad and Source corners are ordered top-left, top-right,
// bottom-right, bottom-left in their own coordinate spaces. Source is expressed
// after the shape's Transform and defaults to the transformed bounding quad
// when omitted. The mapping composes after Transform, so the full path is
// local -> affine Transform -> Source space -> plane of Quad.
type Projective struct {
	Quad [4]geom.Point `json:"quad"`
	// Source is the pre-image quadrilateral in post-transform coordinates;
	// empty means the transformed bounding quad.
	Source []geom.Point `json:"source,omitempty"`
	// Affine requests the best-fit affine approximation instead of the true
	// perspective mapping. It exists so a plate can display both treatments
	// side by side with an explicit, machine-readable distinction.
	Affine bool `json:"affine,omitempty"`
}
type Shape struct {
	ID         string      `json:"id"`
	Path       []geom.Op   `json:"path"`
	FillRule   string      `json:"fill_rule"`
	Brush      Brush       `json:"brush"`
	Transform  geom.Matrix `json:"transform"`
	Projective *Projective `json:"projective,omitempty"`
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
	Shape Shape
	Path  geom.Path
	// PlaneToLocal maps display-plane coordinates back to local shape
	// coordinates. It is always populated; affine shapes get an exact
	// homography built from the inverse of Transform.
	PlaneToLocal geom.Homography
	// ProjectedPath is the flattened outline mapped into plane coordinates.
	// It is only populated for projective shapes and is what SVG exports so
	// the receiver keeps an editable contour in the display plane.
	ProjectedPath geom.Path
	// AffineApprox is the best-fit affine map from local to plane coordinates,
	// used for the labelled affine comparison. Affine-comparison shapes render
	// through it; true perspective shapes render through PlaneToLocal.
	AffineApprox geom.Matrix
	Projective   bool
	// LocalToPlane is the composed local -> display homography. For an
	// affine-comparison shape it is the homography form of AffineApprox.
	LocalToPlane geom.Homography
	// Face is the display quad for true perspective shapes. Coverage is
	// clipped to it so artwork is pasted onto the face rather than painted
	// past its edge. FaceSet is false for the affine-comparison variant, which
	// must not be clipped or its divergence from the true face would vanish.
	Face    [4]geom.Point
	FaceSet bool
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
