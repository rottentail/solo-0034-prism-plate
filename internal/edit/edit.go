// Package edit turns a batch of node-targeted operations into one candidate
// document. Operations never mutate a stored revision: they are applied in
// request order to a private clone and the result must pass the same compile
// admission as every other scene source.
package edit

import (
	"encoding/json"
	"fmt"
	"math"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
)

// MaxOperations bounds one revision edit batch alongside the 8MiB HTTP limit.
const MaxOperations = 64

type Move struct {
	Shape  string  `json:"shape"`
	DeltaX float64 `json:"delta_x"`
	DeltaY float64 `json:"delta_y"`
}
type BrushReplace struct {
	Shape string      `json:"shape"`
	Brush scene.Brush `json:"brush"`
}
type Relocate struct {
	Shape string `json:"shape"`
	Layer string `json:"layer"`
}

// Op is one discriminated edit. Exactly one payload matching Type must be set;
// the transport decoder rejects unknown JSON fields for every nested value.
type Op struct {
	Type     string        `json:"type"`
	Move     *Move         `json:"move,omitempty"`
	Brush    *BrushReplace `json:"brush,omitempty"`
	Relocate *Relocate     `json:"relocate,omitempty"`
}

func finite(x float64) bool {
	return !math.IsNaN(x) && !math.IsInf(x, 0) && math.Abs(x) <= 1e6
}

// clone keeps candidate construction isolated from any caller-owned memory;
// JSON round tripping matches the repository snapshot boundary.
func clone(d scene.Document) scene.Document {
	var out scene.Document
	b, _ := json.Marshal(d)
	_ = json.Unmarshal(b, &out)
	return out
}

func findShape(d *scene.Document, shapeID string) (int, int, error) {
	for li := range d.Layers {
		for si := range d.Layers[li].Shapes {
			if d.Layers[li].Shapes[si].ID == shapeID {
				return li, si, nil
			}
		}
	}
	return 0, 0, fmt.Errorf("edit target shape %q not found", shapeID)
}

func layerIndex(d *scene.Document, layerID string) (int, error) {
	for li := range d.Layers {
		if d.Layers[li].ID == layerID {
			return li, nil
		}
	}
	return 0, fmt.Errorf("edit target layer %q not found", layerID)
}

// Check validates operation envelopes before any document is touched so a bad
// batch fails without leaving a partially edited candidate behind.
func Check(ops []Op) error {
	if len(ops) < 1 || len(ops) > MaxOperations {
		return fmt.Errorf("one..%d edit operations required", MaxOperations)
	}
	for i, op := range ops {
		switch op.Type {
		case "move":
			if op.Move == nil {
				return fmt.Errorf("operation %d: move payload missing", i)
			}
			if op.Move.Shape == "" || !finite(op.Move.DeltaX) || !finite(op.Move.DeltaY) {
				return fmt.Errorf("operation %d: invalid move", i)
			}
		case "replace_brush":
			if op.Brush == nil {
				return fmt.Errorf("operation %d: brush payload missing", i)
			}
			if op.Brush.Shape == "" {
				return fmt.Errorf("operation %d: invalid brush target", i)
			}
		case "relocate":
			if op.Relocate == nil {
				return fmt.Errorf("operation %d: relocate payload missing", i)
			}
			if op.Relocate.Shape == "" || op.Relocate.Layer == "" {
				return fmt.Errorf("operation %d: invalid relocate target", i)
			}
		default:
			return fmt.Errorf("operation %d: unknown type %q", i, op.Type)
		}
	}
	return nil
}

// Apply returns a new document produced by applying the operations in order.
// Node lookup uses stable shape and layer identifiers; later operations see
// the effects of earlier ones. The compiled candidate must be admissible,
// otherwise an error is returned and no caller-visible state has changed.
func Apply(base scene.Document, ops []Op) (scene.Document, error) {
	if err := Check(ops); err != nil {
		return scene.Document{}, err
	}
	d := clone(base)
	for i, op := range ops {
		switch op.Type {
		case "move":
			li, si, err := findShape(&d, op.Move.Shape)
			if err != nil {
				return scene.Document{}, err
			}
			m := d.Layers[li].Shapes[si].Transform
			// Translation is the document-space position term; the local
			// contour, brush and linear part of the transform stay untouched.
			m[4] += op.Move.DeltaX
			m[5] += op.Move.DeltaY
			d.Layers[li].Shapes[si].Transform = m
		case "replace_brush":
			li, si, err := findShape(&d, op.Brush.Shape)
			if err != nil {
				return scene.Document{}, err
			}
			d.Layers[li].Shapes[si].Brush = op.Brush.Brush
		case "relocate":
			li, si, err := findShape(&d, op.Relocate.Shape)
			if err != nil {
				return scene.Document{}, err
			}
			dest, err := layerIndex(&d, op.Relocate.Layer)
			if err != nil {
				return scene.Document{}, err
			}
			if dest == li {
				return scene.Document{}, fmt.Errorf("operation %d: shape %q already belongs to layer %q", i, op.Relocate.Shape, op.Relocate.Layer)
			}
			shape := d.Layers[li].Shapes[si]
			d.Layers[li].Shapes = append(d.Layers[li].Shapes[:si], d.Layers[li].Shapes[si+1:]...)
			d.Layers[dest].Shapes = append(d.Layers[dest].Shapes, shape)
		}
	}
	// Same admission gate as create, revise, bundle import and recovery.
	if _, err := validate.Compile(d); err != nil {
		return scene.Document{}, fmt.Errorf("edit batch rejected: %w", err)
	}
	return d, nil
}
