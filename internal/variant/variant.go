// Package variant resolves constrained badge parameter templates into frozen
// scene documents. A template fixes a recipe, canvas, palette, parameter
// domains and joint constraints as versioned data; Resolve maps one parameter
// set to exactly one document, so a resolved draft never changes when the
// template later evolves.
package variant

import (
	"fmt"
	"math"
	"prismplate/internal/geom"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"sort"
	"strings"
)

// Recipe is the only generator contract this build admits.
const Recipe = "badge-v1"

// badgeSegments counts flattened edges of the three circles the recipe emits.
const badgeSegments = 3 * 4 * 24

type ParamSpec struct {
	Name string  `json:"name"`
	Kind string  `json:"kind"` // number | integer
	Min  float64 `json:"min"`
	Max  float64 `json:"max"`
}

// Constraint is a joint inequality over parameters and derived quantities:
// sum(coef*value) op limit. It exists because individually legal values can
// still combine into canvas overflow or degenerate geometry.
type Constraint struct {
	ID    string             `json:"id"`
	Terms map[string]float64 `json:"terms"`
	Op    string             `json:"op"` // <= | >=
	Limit float64            `json:"limit"`
}
type Definition struct {
	Recipe       string       `json:"recipe"`
	Width        int          `json:"width"`
	Height       int          `json:"height"`
	Background   scene.Color  `json:"background"`
	Edge         scene.Color  `json:"edge"`
	FillFrom     scene.Color  `json:"fill_from"`
	FillTo       scene.Color  `json:"fill_to"`
	CoreDiameter float64      `json:"core_diameter"`
	Params       []ParamSpec  `json:"params"`
	Constraints  []Constraint `json:"constraints"`
}

// required pins the parameter names, kinds and recipe-level domains; a
// template narrows domains but cannot rename or retype the recipe contract.
var required = map[string]struct {
	kind       string
	floor, cap float64
}{
	"margin":  {"number", 0, 512},
	"levels":  {"integer", 2, 16},
	"outline": {"number", 0, 0.95},
}

// derived lists the quantities Resolve computes from raw parameters; joint
// constraints may reference them alongside parameter names.
var derived = map[string]bool{
	"core": true, "ring": true, "span": true,
	"canvas_min": true, "canvas_width": true, "canvas_height": true,
}

func finite(x float64) bool { return !math.IsNaN(x) && !math.IsInf(x, 0) && math.Abs(x) <= 1e6 }

// ValidateDefinition admits a template revision before it can be frozen.
func ValidateDefinition(d Definition) error {
	if d.Recipe != Recipe {
		return fmt.Errorf("unknown recipe %q", d.Recipe)
	}
	if d.Width < 1 || d.Width > 512 || d.Height < 1 || d.Height > 512 {
		return fmt.Errorf("invalid template canvas (maximum 512 square)")
	}
	if int64(badgeSegments)*int64(d.Width)*int64(d.Height)*4 > 160_000_000 {
		return fmt.Errorf("template canvas exceeds bounded rendering work")
	}
	for _, c := range []scene.Color{d.Background, d.Edge, d.FillFrom, d.FillTo} {
		if !validate.Color(c) {
			return fmt.Errorf("invalid template color")
		}
	}
	if !finite(d.CoreDiameter) || d.CoreDiameter < 8 || d.CoreDiameter > float64(min(d.Width, d.Height)) {
		return fmt.Errorf("core diameter must stay within 8 and the smaller canvas edge")
	}
	if len(d.Params) != len(required) {
		return fmt.Errorf("recipe %s declares exactly the parameters margin, levels, outline", Recipe)
	}
	seen := map[string]bool{}
	for _, p := range d.Params {
		req, ok := required[p.Name]
		if !ok || seen[p.Name] {
			return fmt.Errorf("unexpected parameter %q", p.Name)
		}
		seen[p.Name] = true
		if p.Kind != req.kind {
			return fmt.Errorf("parameter %s must be %s", p.Name, req.kind)
		}
		if !finite(p.Min) || !finite(p.Max) || p.Min > p.Max {
			return fmt.Errorf("invalid domain for parameter %s", p.Name)
		}
		if p.Min < req.floor || p.Max > req.cap {
			return fmt.Errorf("parameter %s domain must stay within %v..%v", p.Name, req.floor, req.cap)
		}
	}
	if len(d.Constraints) > 16 {
		return fmt.Errorf("at most 16 constraints per template")
	}
	for _, c := range d.Constraints {
		if !validate.ID(c.ID) {
			return fmt.Errorf("invalid constraint id %q", c.ID)
		}
		if c.Op != "<=" && c.Op != ">=" {
			return fmt.Errorf("constraint %s operator must be <= or >=", c.ID)
		}
		if !finite(c.Limit) {
			return fmt.Errorf("constraint %s limit is not finite", c.ID)
		}
		if len(c.Terms) < 1 || len(c.Terms) > 8 {
			return fmt.Errorf("constraint %s requires 1..8 terms", c.ID)
		}
		for name, coef := range c.Terms {
			if _, ok := required[name]; !ok && !derived[name] {
				return fmt.Errorf("constraint %s references unknown quantity %q", c.ID, name)
			}
			if !finite(coef) || coef == 0 {
				return fmt.Errorf("constraint %s has a non-finite or zero coefficient", c.ID)
			}
		}
	}
	return nil
}

// Resolve maps one parameter set to a frozen document. Every rejection names
// the parameter or the joint constraint responsible, with evaluated numbers.
func Resolve(d Definition, params map[string]float64) (scene.Document, error) {
	var doc scene.Document
	for name := range params {
		if _, ok := required[name]; !ok {
			return doc, fmt.Errorf("unknown parameter %q", name)
		}
	}
	values := map[string]float64{}
	for _, spec := range d.Params {
		v, ok := params[spec.Name]
		if !ok {
			return doc, fmt.Errorf("missing parameter %q", spec.Name)
		}
		if !finite(v) {
			return doc, fmt.Errorf("parameter %s is not finite", spec.Name)
		}
		if spec.Kind == "integer" && v != math.Trunc(v) {
			return doc, fmt.Errorf("parameter %s=%v must be an integer", spec.Name, v)
		}
		if v < spec.Min || v > spec.Max {
			return doc, fmt.Errorf("parameter %s=%v outside %.4g..%.4g", spec.Name, v, spec.Min, spec.Max)
		}
		values[spec.Name] = v
	}
	// Derived geometry: the outline ring grows outward from the fixed core, so
	// margin and outline jointly decide whether the ink still fits the canvas.
	core := d.CoreDiameter
	ring := core / 2 * values["outline"]
	values["core"] = core
	values["ring"] = ring
	values["span"] = core + 2*ring
	values["canvas_min"] = float64(min(d.Width, d.Height))
	values["canvas_width"] = float64(d.Width)
	values["canvas_height"] = float64(d.Height)
	for _, c := range d.Constraints {
		lhs := 0.0
		for name, coef := range c.Terms {
			lhs += coef * values[name]
		}
		if (c.Op == "<=" && lhs > c.Limit) || (c.Op == ">=" && lhs < c.Limit) {
			return doc, fmt.Errorf("constraint %q violated: %s = %.6g but must be %s %.6g", c.ID, describe(c.Terms), lhs, c.Op, c.Limit)
		}
	}
	doc = build(d, values)
	if _, err := validate.Compile(doc); err != nil {
		return scene.Document{}, fmt.Errorf("resolved scene rejected: %w", err)
	}
	return doc, nil
}

func describe(terms map[string]float64) string {
	names := make([]string, 0, len(terms))
	for n := range terms {
		names = append(names, n)
	}
	sort.Strings(names)
	var b strings.Builder
	for i, n := range names {
		if i > 0 {
			b.WriteByte('+')
		}
		fmt.Fprintf(&b, "%.4g*%s", terms[n], n)
	}
	return b.String()
}

const kappa = 0.5522847498307936

func circleOps(r, cx, cy float64) []geom.Op {
	k := kappa * r
	pt := func(x, y float64) geom.Point { return geom.Point{X: cx + x, Y: cy + y} }
	return []geom.Op{
		{Kind: "M", Points: []geom.Point{pt(r, 0)}},
		{Kind: "C", Points: []geom.Point{pt(r, k), pt(k, r), pt(0, r)}},
		{Kind: "C", Points: []geom.Point{pt(-k, r), pt(-r, k), pt(-r, 0)}},
		{Kind: "C", Points: []geom.Point{pt(-r, -k), pt(-k, -r), pt(0, -r)}},
		{Kind: "C", Points: []geom.Point{pt(k, -r), pt(r, -k), pt(r, 0)}},
		{Kind: "Z"},
	}
}
func mix(a, b scene.Color, t float64) scene.Color {
	return scene.Color{R: a.R + (b.R-a.R)*t, G: a.G + (b.G-a.G)*t, B: a.B + (b.B-a.B)*t, A: a.A + (b.A-a.A)*t}
}

// build is deterministic: identical definitions and parameters always produce
// byte-identical documents and therefore identical digests.
func build(d Definition, values map[string]float64) scene.Document {
	cx, cy := float64(d.Width)/2, float64(d.Height)/2
	rc := d.CoreDiameter / 2
	ro := rc * (1 + values["outline"])
	levels := int(values["levels"])
	stops := make([]scene.Stop, 0, levels)
	for i := 0; i < levels; i++ {
		t := float64(i) / float64(levels-1)
		stops = append(stops, scene.Stop{At: t, Color: mix(d.FillFrom, d.FillTo, t)})
	}
	ring := scene.Shape{
		ID:        "badge-outline-ring",
		FillRule:  "evenodd",
		Path:      append(circleOps(ro, cx, cy), circleOps(rc, cx, cy)...),
		Transform: geom.Identity,
		Brush:     scene.Brush{Kind: "solid", Color: d.Edge},
	}
	core := scene.Shape{
		ID:        "badge-core-disc",
		FillRule:  "nonzero",
		Path:      circleOps(1, 0, 0),
		Transform: geom.Matrix{rc, 0, 0, rc, cx, cy},
		Brush: scene.Brush{Kind: "linear",
			From:  geom.Point{X: -1, Y: -1},
			To:    geom.Point{X: 1, Y: 1},
			Stops: stops,
		},
	}
	return scene.Document{
		Width: d.Width, Height: d.Height, Background: d.Background,
		Layers: []scene.Layer{
			{ID: "badge-ring", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{ring}},
			{ID: "badge-core", Opacity: 1, Blend: "normal", Shapes: []scene.Shape{core}},
		},
	}
}
