// JSON shapes for Layer and Shape so the dual-representation PathRef fields
// keep their historical field names while clip/mask remain optional.
package scene

import (
	"encoding/json"
	"prismplate/internal/geom"
)

func (s Shape) MarshalJSON() ([]byte, error) {
	type alias struct {
		ID        string      `json:"id"`
		Path      PathRef     `json:"path"`
		FillRule  string      `json:"fill_rule"`
		Brush     Brush       `json:"brush"`
		Transform geom.Matrix `json:"transform"`
	}
	return json.Marshal(alias{ID: s.ID, Path: s.Path, FillRule: s.FillRule, Brush: s.Brush, Transform: s.Transform})
}

func (s *Shape) UnmarshalJSON(b []byte) error {
	type alias struct {
		ID        string      `json:"id"`
		Path      PathRef     `json:"path"`
		FillRule  string      `json:"fill_rule"`
		Brush     Brush       `json:"brush"`
		Transform geom.Matrix `json:"transform"`
	}
	var a alias
	if err := strictDecode(b, &a); err != nil {
		return err
	}
	s.ID, s.Path, s.FillRule, s.Brush, s.Transform = a.ID, a.Path, a.FillRule, a.Brush, a.Transform
	return nil
}

func (l Layer) MarshalJSON() ([]byte, error) {
	type out struct {
		ID      string   `json:"id"`
		Opacity float64  `json:"opacity"`
		Blend   string   `json:"blend"`
		Clip    *PathRef `json:"clip,omitempty"`
		Mask    *PathRef `json:"mask,omitempty"`
		Shapes  []Shape  `json:"shapes"`
	}
	return json.Marshal(out{ID: l.ID, Opacity: l.Opacity, Blend: l.Blend, Clip: l.Clip, Mask: l.Mask, Shapes: l.Shapes})
}

func (l *Layer) UnmarshalJSON(b []byte) error {
	type in struct {
		ID      string   `json:"id"`
		Opacity float64  `json:"opacity"`
		Blend   string   `json:"blend"`
		Clip    *PathRef `json:"clip"`
		Mask    *PathRef `json:"mask"`
		Shapes  []Shape  `json:"shapes"`
	}
	var v in
	if err := strictDecode(b, &v); err != nil {
		return err
	}
	l.ID, l.Opacity, l.Blend, l.Clip, l.Mask, l.Shapes = v.ID, v.Opacity, v.Blend, v.Clip, v.Mask, v.Shapes
	return nil
}
