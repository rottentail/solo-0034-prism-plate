// Package corridor verifies protected negative-space channels and solves bounded
// curve-node moves that keep every channel open. All measurements use the same
// flattened geometry as raster and SVG export, in canvas coordinates.
package corridor

import (
	"fmt"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
)

// Tolerance covers the chord-vs-Bezier deviation of the fixed Flatten contract
// (Q=16, C=24 subdivisions) on the 512px canvas bound and the centreline sampling
// gap. A candidate only passes when clearance exceeds half the width by it.
const Tolerance = 0.05

const (
	maxChannels      = 4
	maxMoves         = 16
	maxCenterPoints  = 64
	solveGridSteps   = 32
	bisectSteps      = 12
	maxCandidates    = 5
	sampleGapDivisor = 4.0
	sampleGapFloor   = 2.0
)

// Channel is a designer-marked negative-space corridor in canvas coordinates.
// The polyline centreline may bend; MinWidth is the required minimum canvas width.
type Channel struct {
	ID         string       `json:"id"`
	Centerline []geom.Point `json:"centerline"`
	MinWidth   float64      `json:"min_width"`
}

// Move is one drag intent: a node of one shape path, identified in local
// coordinates, dragged toward To (also local coordinates).
type Move struct {
	ShapeID string     `json:"shape_id"`
	From    geom.Point `json:"from"`
	To      geom.Point `json:"to"`
}

// NarrowPoint records the tightest centreline sample of a channel check.
type NarrowPoint struct {
	At        geom.Point `json:"at"`
	Clearance float64    `json:"clearance"`
	Wall      string     `json:"wall"`
}

// ChannelReport is the verifiable result for one protected channel.
type ChannelReport struct {
	ID           string      `json:"id"`
	MinWidth     float64     `json:"min_width"`
	MinClearance float64     `json:"min_clearance"`
	Passes       bool        `json:"passes"`
	Samples      int         `json:"samples"`
	Narrowest    NarrowPoint `json:"narrowest"`
}

// Report covers every channel of one candidate document.
type Report struct {
	Passes   bool            `json:"passes"`
	Channels []ChannelReport `json:"channels"`
}

type wall struct {
	segments [][2]geom.Point
	label    string
}

type filledRegion struct {
	path    geom.Path
	rule    string
	inverse geom.Matrix
	hasInv  bool
	clip    geom.Path
	mask    geom.Path
}

type sceneGeometry struct {
	width, height float64
	walls         []wall
	filled        []filledRegion
}

func finitePoint(p geom.Point) bool {
	return !math.IsNaN(p.X) && !math.IsInf(p.X, 0) && !math.IsNaN(p.Y) && !math.IsInf(p.Y, 0)
}

// validateRequest checks the designer-supplied protection request independently
// of scene compilation, so malformed markers are rejected before any solving.
func validateRequest(moves []Move, channels []Channel) error {
	if len(moves) < 1 || len(moves) > maxMoves {
		return fmt.Errorf("moves must contain 1..%d entries", maxMoves)
	}
	seenMoves := map[string]bool{}
	for i, m := range moves {
		if !validate.ID(m.ShapeID) {
			return fmt.Errorf("move %d references invalid shape id", i)
		}
		if !finitePoint(m.From) || !finitePoint(m.To) {
			return fmt.Errorf("move %d contains non-finite coordinates", i)
		}
		key := m.ShapeID + ":" + coordKey(m.From)
		if seenMoves[key] {
			return fmt.Errorf("duplicate move on shape %q", m.ShapeID)
		}
		seenMoves[key] = true
	}
	if len(channels) < 1 || len(channels) > maxChannels {
		return fmt.Errorf("channels must contain 1..%d entries", maxChannels)
	}
	seenChannels := map[string]bool{}
	for i, ch := range channels {
		if !validate.ID(ch.ID) {
			return fmt.Errorf("channel %d has invalid id", i)
		}
		if seenChannels[ch.ID] {
			return fmt.Errorf("duplicate channel id %q", ch.ID)
		}
		seenChannels[ch.ID] = true
		if len(ch.Centerline) < 2 || len(ch.Centerline) > maxCenterPoints {
			return fmt.Errorf("channel %q centreline needs 2..%d points", ch.ID, maxCenterPoints)
		}
		if !(ch.MinWidth > 0 && ch.MinWidth <= 512 && !math.IsNaN(ch.MinWidth) && !math.IsInf(ch.MinWidth, 0)) {
			return fmt.Errorf("channel %q min_width must be within (0,512]", ch.ID)
		}
		for _, p := range ch.Centerline {
			if !finitePoint(p) {
				return fmt.Errorf("channel %q contains non-finite centreline point", ch.ID)
			}
		}
	}
	return nil
}

func coordKey(p geom.Point) string {
	return fmt.Sprintf("%.9g,%.9g", p.X, p.Y)
}

// buildGeometry extracts canvas-coordinate boundary walls and the visible fill
// membership test from a compiled scene. The same flattened rings feed raster,
// SVG and every channel assertion.
func buildGeometry(c scene.Compiled) sceneGeometry {
	g := sceneGeometry{width: float64(c.Document.Width), height: float64(c.Document.Height)}
	corners := [4]geom.Point{{}, {X: g.width}, {X: g.width, Y: g.height}, {Y: g.height}}
	var canvas [][2]geom.Point
	for i := 0; i < 4; i++ {
		canvas = append(canvas, [2]geom.Point{corners[i], corners[(i+1)%4]})
	}
	g.walls = append(g.walls, wall{segments: canvas, label: "canvas"})
	for li, cl := range c.Layers {
		if len(cl.Clip) > 0 {
			g.walls = append(g.walls, wall{segments: ringSegments(cl.Clip), label: fmt.Sprintf("layer[%s].clip", cl.Layer.ID)})
		}
		if len(cl.Mask) > 0 {
			g.walls = append(g.walls, wall{segments: ringSegments(cl.Mask), label: fmt.Sprintf("layer[%s].mask", cl.Layer.ID)})
		}
		for _, cs := range cl.Shapes {
			tr := cs.Shape.Transform
			ring := make(geom.Path, len(cs.Path))
			for ri, r := range cs.Path {
				conv := make(geom.Contour, len(r))
				for pi, p := range r {
					conv[pi] = tr.Apply(p)
				}
				ring[ri] = conv
			}
			g.walls = append(g.walls, wall{segments: ringSegments(ring), label: fmt.Sprintf("layer[%d].shape[%s]", li, cs.Shape.ID)})
			g.filled = append(g.filled, filledRegion{path: cs.Path, rule: cs.Shape.FillRule, inverse: cs.Inverse, hasInv: true, clip: cl.Clip, mask: cl.Mask})
		}
	}
	return g
}

func ringSegments(p geom.Path) [][2]geom.Point {
	var out [][2]geom.Point
	for _, ring := range p {
		for i := range ring {
			out = append(out, [2]geom.Point{ring[i], ring[(i+1)%len(ring)]})
		}
	}
	return out
}

// isFilled mirrors raster.Render's visibility test: layer clip/mask rejection
// followed by shape fill-rule containment, all expressed through the compiled
// inverse transform and flattened paths.
func (g sceneGeometry) isFilled(p geom.Point) bool {
	for _, f := range g.filled {
		if len(f.clip) > 0 && !f.clip.Contains(p, "nonzero") {
			continue
		}
		if len(f.mask) > 0 && !f.mask.Contains(p, "nonzero") {
			continue
		}
		q := p
		if f.hasInv {
			q = f.inverse.Apply(p)
		}
		if f.path.Contains(q, f.rule) {
			return true
		}
	}
	return false
}

func pointSegmentDistance(p, a, b geom.Point) (float64, geom.Point) {
	dx, dy := b.X-a.X, b.Y-a.Y
	len2 := dx*dx + dy*dy
	t := 0.0
	if len2 > 0 {
		t = ((p.X-a.X)*dx + (p.Y-a.Y)*dy) / len2
		if t < 0 {
			t = 0
		} else if t > 1 {
			t = 1
		}
	}
	closest := geom.Point{X: a.X + t*dx, Y: a.Y + t*dy}
	return math.Hypot(p.X-closest.X, p.Y-closest.Y), closest
}

func (g sceneGeometry) clearance(p geom.Point) (float64, NarrowPoint) {
	best := math.Inf(1)
	label := ""
	for _, w := range g.walls {
		for _, seg := range w.segments {
			d, _ := pointSegmentDistance(p, seg[0], seg[1])
			if d < best {
				best, label = d, w.label
			}
		}
	}
	return best, NarrowPoint{At: p, Clearance: best, Wall: label}
}

// centrelineSamples walks the polyline by arc length. The step is at most a
// quarter of the required width and never coarser than sampleGapFloor canvas
// units; every polyline vertex and both endpoints are included.
func centrelineSamples(line []geom.Point, minWidth float64) []geom.Point {
	step := minWidth / sampleGapDivisor
	if step > sampleGapFloor {
		step = sampleGapFloor
	}
	out := []geom.Point{line[0]}
	remain := step
	for i := 0; i+1 < len(line); i++ {
		a, b := line[i], line[i+1]
		segLen := math.Hypot(b.X-a.X, b.Y-a.Y)
		d := remain
		for d < segLen {
			t := d / segLen
			out = append(out, geom.Point{X: a.X + t*(b.X-a.X), Y: a.Y + t*(b.Y-a.Y)})
			d += step
		}
		remain = d - segLen
		out = append(out, b)
	}
	return out
}

// Check compiles a candidate document and evaluates every protected channel.
func Check(d scene.Document, channels []Channel) (Report, error) {
	c, err := validate.Compile(d)
	if err != nil {
		return Report{}, err
	}
	g := buildGeometry(c)
	rep := Report{Passes: true, Channels: make([]ChannelReport, 0, len(channels))}
	for _, ch := range channels {
		samples := centrelineSamples(ch.Centerline, ch.MinWidth)
		required := ch.MinWidth/2 + Tolerance
		cr := ChannelReport{ID: ch.ID, MinWidth: ch.MinWidth, MinClearance: math.Inf(1), Samples: len(samples)}
		for _, p := range samples {
			dist, np := g.clearance(p)
			if g.isFilled(p) {
				dist, np.Clearance, np.Wall = 0, 0, np.Wall+":filled"
			}
			if dist < cr.MinClearance {
				cr.MinClearance = dist
				cr.Narrowest = np
			}
		}
		cr.Passes = cr.MinClearance >= required
		if !cr.Passes {
			rep.Passes = false
		}
		rep.Channels = append(rep.Channels, cr)
	}
	return rep, nil
}
