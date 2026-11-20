// Package timeline validates the keyframe time contract and deterministically
// samples a document at one instant into an ordinary static scene document.
//
// Supported interpolation scope, by explicit choice:
//
//   - Every animated value is piecewise LINEAR in time between its keyframes;
//     no easing curves, no extrapolation beyond the authored timeline domain.
//     Outside the authored range the nearest endpoint value is held.
//   - position (translation x/y), rotation about the local origin in degrees,
//     non-uniform signed scale (sx, sy) and opacity are independently animated.
//   - Rotation is stored as an unbounded signed degree value and interpolated
//     exactly as authored: 350 -> 370 sweeps +20 degrees across the 360
//     boundary instead of snapping backwards, and 179 -> -179 means an
//     explicit -358 degree sweep. The engine never rewraps angles, so there
//     is no hidden jump at any intermediate instant.
//   - Scale components are signed and interpolated linearly, including through
//     zero. A negative component is a mirror, not a jump: the transform passes
//     continuously through a fully collapsed state. At a sampling instant whose
//     composed transform is (near) singular the shape provably has zero area;
//     the proven-invertible authored transform is kept with a zero-alpha fill,
//     which is visually identical and never an undefined inversion.
//   - Opacity is linear on its 0..1 scale (transparent -> fully present); the
//     first visible frame fades in continuously, there is no pop at alpha 0.
package timeline

import (
	"fmt"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

const (
	maxTracks         = 32
	maxKeyframesTotal = 128
	bounds            = 1e6
	// singularLimit mirrors geom.Matrix.Inverse; a determinant at or below this
	// magnitude has no meaningful inverse and the shape has zero visual area.
	singularLimit = 1e-10
)

func finite(x float64) bool { return !math.IsNaN(x) && !math.IsInf(x, 0) && math.Abs(x) <= bounds }
func unit(x float64) bool   { return finite(x) && x >= 0 && x <= 1 }

// Validate checks the time description itself. Rendering admission (invertible
// transforms at every keyframe instant, segment budgets) is owned by validate,
// which recompiles the sampled document for each authored keyframe.
func Validate(d scene.Document, t *scene.Timeline) error {
	if t == nil {
		return nil
	}
	if !finite(t.Duration) || t.Duration <= 0 {
		return fmt.Errorf("timeline duration must be a positive finite number")
	}
	if len(t.Tracks) == 0 {
		return fmt.Errorf("timeline requires at least one track")
	}
	if len(t.Tracks) > maxTracks {
		return fmt.Errorf("timeline track capacity reached")
	}
	ids := map[string]bool{}
	total := 0
	shapeKinds := map[string]bool{"position": true, "rotation": true, "scale": true, "opacity": true}
	for _, tr := range t.Tracks {
		if tr.ID == "" || ids[tr.ID] {
			return fmt.Errorf("invalid or duplicate track id %q", tr.ID)
		}
		ids[tr.ID] = true
		if !shapeKinds[tr.Kind] {
			return fmt.Errorf("unknown track kind %q", tr.Kind)
		}
		if tr.Target == "" {
			return fmt.Errorf("track requires a target")
		}
		if len(tr.Keyframes) < 1 {
			return fmt.Errorf("track %q requires at least one keyframe", tr.ID)
		}
		total += len(tr.Keyframes)
		if total > maxKeyframesTotal {
			return fmt.Errorf("timeline keyframe capacity reached")
		}
		prev := -1.0
		for _, k := range tr.Keyframes {
			if !finite(k.At) || k.At < 0 || k.At > t.Duration {
				return fmt.Errorf("keyframe time outside timeline domain")
			}
			if k.At <= prev {
				return fmt.Errorf("keyframe times must be strictly increasing")
			}
			prev = k.At
			// One track owns one channel family: stray fields from another
			// channel would be silently ignored dead data, so reject them.
			switch tr.Kind {
			case "position":
				if k.Position == nil || !finite(k.Position.X) || !finite(k.Position.Y) || k.Rotation != nil || k.ScaleX != nil || k.ScaleY != nil || k.Opacity != nil {
					return fmt.Errorf("position track %q keyframes must define only position", tr.ID)
				}
			case "rotation":
				if k.Rotation == nil || !finite(*k.Rotation) || k.Position != nil || k.ScaleX != nil || k.ScaleY != nil || k.Opacity != nil {
					return fmt.Errorf("rotation track %q keyframes must define only rotation", tr.ID)
				}
			case "scale":
				if k.ScaleX == nil || k.ScaleY == nil || !finite(*k.ScaleX) || !finite(*k.ScaleY) || k.Position != nil || k.Rotation != nil || k.Opacity != nil {
					return fmt.Errorf("scale track %q keyframes must define both scale components only", tr.ID)
				}
			case "opacity":
				if k.Opacity == nil || !unit(*k.Opacity) || k.Position != nil || k.Rotation != nil || k.ScaleX != nil || k.ScaleY != nil {
					return fmt.Errorf("opacity track %q keyframes must define only opacity", tr.ID)
				}
			}
		}
	}
	// Every target must resolve to a real node; an orphan track could never be
	// inspected but would still make the time description misleading.
	for _, tr := range t.Tracks {
		found := false
		for _, l := range d.Layers {
			for _, s := range l.Shapes {
				if s.ID == tr.Target {
					found = true
				}
			}
		}
		if !found {
			return fmt.Errorf("track %q targets unknown shape %q", tr.ID, tr.Target)
		}
	}
	return nil
}

// KeyframeTimes returns the union of every authored instant plus the domain
// endpoints, for admission recompilation. Deduplicated and sorted.
func KeyframeTimes(t *scene.Timeline) []float64 {
	seen := map[float64]bool{0: true, t.Duration: true}
	for _, tr := range t.Tracks {
		for _, k := range tr.Keyframes {
			seen[k.At] = true
		}
	}
	out := make([]float64, 0, len(seen))
	for v := range seen {
		out = append(out, v)
	}
	// Small bounded set; a simple insertion sort keeps the package stdlib-only.
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j-1] > out[j]; j-- {
			out[j-1], out[j] = out[j], out[j-1]
		}
	}
	return out
}

type scalarFrame struct {
	at    float64
	value float64
}

// channelFrames extracts the values one track defines for a scalar channel.
// Admission guarantees the matching channel is present on every keyframe.
func channelFrames(frames []scene.Keyframe, pick func(scene.Keyframe) *float64) []scalarFrame {
	out := make([]scalarFrame, 0, len(frames))
	for _, k := range frames {
		if v := pick(k); v != nil {
			out = append(out, scalarFrame{k.At, *v})
		}
	}
	return out
}

// valueAt performs piecewise-linear interpolation on the channel's own frames.
// Outside the authored range the nearest endpoint value is held; there is no
// extrapolation and no wrap-around, so rotation crossing 360 keeps its meaning.
func valueAt(frames []scalarFrame, at float64) float64 {
	if at <= frames[0].at {
		return frames[0].value
	}
	if at >= frames[len(frames)-1].at {
		return frames[len(frames)-1].value
	}
	for i := 1; i < len(frames); i++ {
		if at <= frames[i].at {
			u := (at - frames[i-1].at) / (frames[i].at - frames[i-1].at)
			return frames[i-1].value + (frames[i].value-frames[i-1].value)*u
		}
	}
	return frames[len(frames)-1].value
}

func pointAt(frames []scene.Keyframe, at float64) geom.Point {
	return geom.Point{
		X: valueAt(channelFrames(frames, func(k scene.Keyframe) *float64 {
			if k.Position == nil {
				return nil
			}
			return &k.Position.X
		}), at),
		Y: valueAt(channelFrames(frames, func(k scene.Keyframe) *float64 {
			if k.Position == nil {
				return nil
			}
			return &k.Position.Y
		}), at),
	}
}

// compose prepends the animated TRS to the authored static transform: the
// shape's local contour first passes through T*R*S, then through its existing
// transform, matching the local-coordinate brush/path contract.
func compose(base geom.Matrix, p geom.Point, rotDeg, sx, sy float64) geom.Matrix {
	rad := rotDeg * math.Pi / 180
	cos, sin := math.Cos(rad), math.Sin(rad)
	// T * R * S
	animated := geom.Matrix{cos * sx, sin * sx, -sin * sy, cos * sy, p.X, p.Y}
	var m geom.Matrix
	m[0] = base[0]*animated[0] + base[2]*animated[1]
	m[1] = base[1]*animated[0] + base[3]*animated[1]
	m[2] = base[0]*animated[2] + base[2]*animated[3]
	m[3] = base[1]*animated[2] + base[3]*animated[3]
	m[4] = base[0]*animated[4] + base[2]*animated[5] + base[4]
	m[5] = base[1]*animated[4] + base[3]*animated[5] + base[5]
	return m
}

func clamp01(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

// scaleBrush multiplies every alpha carried by a brush by the animated shape
// opacity. Solid colors and every gradient stop are handled.
func scaleBrush(b scene.Brush, a float64) scene.Brush {
	b.Color.A = clamp01(b.Color.A * a)
	for i := range b.Stops {
		b.Stops[i].Color.A = clamp01(b.Stops[i].Color.A * a)
	}
	return b
}

type anim struct {
	pos          geom.Point
	hasPos       bool
	rot, sx, sy  float64
	opacity      float64
	hasRot       bool
	hasSx, hasSy bool
	hasOpacity   bool
}

// Sample freezes the document at time at and returns an ordinary static
// document (no timeline) that goes through the exact same render admission and
// renderers as any hand-authored scene.
func Sample(d scene.Document, t *scene.Timeline, at float64) (scene.Document, error) {
	if t == nil {
		return d, nil
	}
	if !finite(at) || at < 0 || at > t.Duration {
		return scene.Document{}, fmt.Errorf("sample time outside timeline domain 0..%g", t.Duration)
	}
	out := d
	out.Timeline = nil
	// Defaults preserve the static identity: no animation means no motion.
	effects := map[string]*anim{}
	get := func(id string) *anim {
		a := effects[id]
		if a == nil {
			a = &anim{sx: 1, sy: 1, opacity: 1}
			effects[id] = a
		}
		return a
	}
	for _, tr := range t.Tracks {
		a := get(tr.Target)
		switch tr.Kind {
		case "position":
			a.pos, a.hasPos = pointAt(tr.Keyframes, at), true
		case "rotation":
			a.rot, a.hasRot = valueAt(channelFrames(tr.Keyframes, func(k scene.Keyframe) *float64 { return k.Rotation }), at), true
		case "scale":
			a.sx, a.hasSx = valueAt(channelFrames(tr.Keyframes, func(k scene.Keyframe) *float64 { return k.ScaleX }), at), true
			a.sy, a.hasSy = valueAt(channelFrames(tr.Keyframes, func(k scene.Keyframe) *float64 { return k.ScaleY }), at), true
		case "opacity":
			a.opacity, a.hasOpacity = valueAt(channelFrames(tr.Keyframes, func(k scene.Keyframe) *float64 { return k.Opacity }), at), true
		}
	}
	layers := make([]scene.Layer, len(d.Layers))
	for li, l := range d.Layers {
		shapes := make([]scene.Shape, len(l.Shapes))
		for si, s := range l.Shapes {
			base := s.Transform
			a := effects[s.ID]
			if a != nil {
				if a.hasPos || a.hasRot || a.hasSx || a.hasSy {
					s.Transform = compose(base, a.pos, a.rot, a.sx, a.sy)
					// A mirror sweep passes through zero area deterministically.
					// At a (near) singular instant the fill provably has no area;
					// keep the proven-invertible authored transform and zero the
					// alpha, which is visually identical and always admissible.
					// The inverse-boundedness check mirrors validate so an
					// intermediate instant can never fail admission at render.
					det := s.Transform[0]*s.Transform[3] - s.Transform[1]*s.Transform[2]
					inv, invertible := s.Transform.Inverse()
					bounded := true
					for _, v := range inv {
						if !finite(v) {
							bounded = false
						}
					}
					if math.Abs(det) <= singularLimit || !invertible || !bounded {
						s.Transform = base
						s.Brush = scaleBrush(s.Brush, 0)
					}
				}
				if a.hasOpacity {
					s.Brush = scaleBrush(s.Brush, a.opacity)
				}
			}
			shapes[si] = s
		}
		l.Shapes = shapes
		layers[li] = l
	}
	out.Layers = layers
	return out, nil
}
