// Package branch derives a new, independently editable document from a frozen
// source revision by copying only selected layers or shapes. The source plate
// and revision are never modified; every result carries provenance and human
// review notices describing how isolation changes the appearance.
package branch

import (
	"fmt"
	"prismplate/internal/geom"
	"prismplate/internal/repository"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"time"
)

// Canvas overrides the destination document canvas. Zero dimensions inherit
// the source canvas; a nil background inherits the source background.
type Canvas struct {
	Width      int          `json:"width,omitempty"`
	Height     int          `json:"height,omitempty"`
	Background *scene.Color `json:"background,omitempty"`
}

// Plan is the partitive branch request accepted over HTTP.
type Plan struct {
	// Placement is "original" (keep source coordinates) or "fit" (uniformly
	// scale and center the selection onto the new canvas). Empty means original.
	Placement string `json:"placement,omitempty"`
	// Selection names whole layers ({layer}) or individual shapes ({layer,shape}).
	// Empty means the whole source document, preserving the legacy branch behavior.
	Selection []repository.Ref `json:"selection,omitempty"`
	Canvas    Canvas           `json:"canvas,omitempty"`
}

// Notice explains an appearance or structure consequence of the extraction.
type Notice struct {
	Code    string          `json:"code"`
	Ref     *repository.Ref `json:"ref,omitempty"`
	Message string          `json:"message"`
}

// Preview is the verifiable, side-effect-free result of a planned extraction.
type Preview struct {
	Document   scene.Document        `json:"document"`
	Provenance repository.Provenance `json:"provenance"`
	Notices    []Notice              `json:"notices"`
	// Bounds is the plate-space bounds of the visible selected geometry.
	Bounds *geom.Bounds `json:"bounds,omitempty"`
	// Transform is the plate-space affine applied in fit placement; nil for original.
	Transform *geom.Matrix `json:"transform,omitempty"`
	Canvas    Canvas       `json:"canvas"`
}

type layerPick struct {
	index  int
	whole  bool
	shapes map[int]bool
}

// Build resolves the plan against the source revision without persisting anything.
// The returned document always passes validate.Compile; callers must run it
// through the same admission before committing a plate.
func Build(sourceID string, rev repository.Revision, plan Plan) (Preview, error) {
	placement := plan.Placement
	if placement == "" {
		placement = "original"
	}
	if placement != "original" && placement != "fit" {
		return Preview{}, fmt.Errorf("placement must be original or fit")
	}
	src := rev.Document
	width, height := plan.Canvas.Width, plan.Canvas.Height
	if width == 0 {
		width = src.Width
	}
	if height == 0 {
		height = src.Height
	}
	if width < 1 || height < 1 || width > 512 || height > 512 {
		return Preview{}, fmt.Errorf("destination canvas must be 1..512 square")
	}
	background := src.Background
	if plan.Canvas.Background != nil {
		background = *plan.Canvas.Background
		if !validate.Color(background) {
			return Preview{}, fmt.Errorf("invalid destination background")
		}
	}
	if len(plan.Selection) > 512 {
		return Preview{}, fmt.Errorf("selection exceeds 512 references")
	}

	picks, refs, err := resolve(src, plan.Selection)
	if err != nil {
		return Preview{}, err
	}

	doc := scene.Document{Width: width, Height: height, Background: background}
	var notices []Notice
	for _, pick := range picks {
		layer := src.Layers[pick.index]
		nl := scene.Layer{ID: layer.ID, Opacity: layer.Opacity, Blend: layer.Blend, Clip: cloneOps(layer.Clip), Mask: cloneOps(layer.Mask)}
		if pick.whole {
			for _, sh := range layer.Shapes {
				nl.Shapes = append(nl.Shapes, cloneShape(sh))
			}
		} else {
			for i, sh := range layer.Shapes {
				if pick.shapes[i] {
					nl.Shapes = append(nl.Shapes, cloneShape(sh))
				}
			}
			ref := repository.Ref{Layer: layer.ID}
			notices = append(notices, Notice{
				Code:    "partial-layer",
				Ref:     &ref,
				Message: fmt.Sprintf("图层 %q 仅保留 %d/%d 个形状；未选中形状不再出现，图层结构已不完整。", layer.ID, len(nl.Shapes), len(layer.Shapes)),
			})
			if len(layer.Clip) > 0 || len(layer.Mask) > 0 {
				notices = append(notices, Notice{
					Code:    "region-retained",
					Ref:     &ref,
					Message: fmt.Sprintf("图层 %q 的裁域/蒙版随抽出形状保留，并继续裁剪其可见区域。", layer.ID),
				})
			}
		}
		if layer.Blend != "normal" {
			ref := repository.Ref{Layer: layer.ID}
			notices = append(notices, Notice{
				Code:    "blend-context",
				Ref:     &ref,
				Message: fmt.Sprintf("图层 %q 原使用 %s 混合；抽离后原下方图层不再参与混合，叠色将直接作用于新画布背景，外观会改变。", layer.ID, layer.Blend),
			})
		}
		if layer.Opacity < 1 {
			ref := repository.Ref{Layer: layer.ID}
			notices = append(notices, Notice{
				Code:    "opacity-context",
				Ref:     &ref,
				Message: fmt.Sprintf("图层 %q 组透明度为 %g；脱离原混合背景后，半透明覆盖在新背景上的颜色观感会变化。", layer.ID, layer.Opacity),
			})
		}
		doc.Layers = append(doc.Layers, nl)
	}

	if plan.Canvas.Background != nil && *plan.Canvas.Background != src.Background {
		notices = append(notices, Notice{
			Code:    "background-changed",
			Message: "画布背景已更换；半透明与混合形状的合成底色随之改变。",
		})
	}

	bounds := visibleBounds(src, picks)
	if bounds.Empty {
		return Preview{}, fmt.Errorf("selection has no visible plate geometry after clips and masks")
	}

	var transform *geom.Matrix
	if placement == "fit" {
		m := fitMatrix(bounds, width, height)
		applyPlateTransform(&doc, m)
		transform = &m
		scale := m[0]
		notices = append(notices, Notice{
			Code:    "fit-scaled",
			Message: fmt.Sprintf("选中内容以 %.4g 倍等比缩放并居中贴合新画布；几何、裁域与蒙版同步变换，笔刷保持形状局部坐标。", scale),
		})
	} else if clippedByCanvas(bounds, width, height) {
		notices = append(notices, Notice{
			Code:    "canvas-crop",
			Message: "保持原位置放置时，部分选中几何超出新画布范围，将被画布边缘裁切。",
		})
	}

	if _, err := validate.Compile(doc); err != nil {
		return Preview{}, fmt.Errorf("extracted document failed admission: %w", err)
	}

	return Preview{
		Document: doc,
		Provenance: repository.Provenance{
			PlateID:     sourceID,
			Revision:    rev.Number,
			SceneDigest: rev.Digest,
			Placement:   placement,
			Selection:   refs,
			Created:     time.Now().UTC(),
		},
		Notices:   notices,
		Bounds:    &bounds,
		Transform: transform,
		Canvas:    Canvas{Width: width, Height: height, Background: plan.Canvas.Background},
	}, nil
}

func resolve(src scene.Document, selection []repository.Ref) ([]layerPick, []repository.Ref, error) {
	byID := map[string]int{}
	shapeIDs := make([]map[string]int, len(src.Layers))
	for i, l := range src.Layers {
		byID[l.ID] = i
		shapeIDs[i] = map[string]int{}
		for j, s := range l.Shapes {
			shapeIDs[i][s.ID] = j
		}
	}
	if len(selection) == 0 {
		picks := make([]layerPick, 0, len(src.Layers))
		refs := make([]repository.Ref, 0, len(src.Layers))
		for i := range src.Layers {
			picks = append(picks, layerPick{index: i, whole: true})
			refs = append(refs, repository.Ref{Layer: src.Layers[i].ID})
		}
		return picks, refs, nil
	}
	pickByLayer := map[int]*layerPick{}
	seen := map[string]bool{}
	for _, ref := range selection {
		key := ref.Layer + "\x00" + ref.Shape
		if seen[key] {
			if ref.Shape == "" {
				return nil, nil, fmt.Errorf("duplicate selection reference to layer %q", ref.Layer)
			}
			return nil, nil, fmt.Errorf("duplicate selection reference to shape %q in layer %q", ref.Shape, ref.Layer)
		}
		seen[key] = true
		li, ok := byID[ref.Layer]
		if !ok {
			return nil, nil, fmt.Errorf("selection references unknown layer %q", ref.Layer)
		}
		pick, ok := pickByLayer[li]
		if !ok {
			pick = &layerPick{index: li, shapes: map[int]bool{}}
			pickByLayer[li] = pick
		}
		if ref.Shape == "" {
			if !pick.whole && len(pick.shapes) > 0 {
				return nil, nil, fmt.Errorf("cannot select whole layer %q together with individual shapes from it", ref.Layer)
			}
			pick.whole = true
			continue
		}
		if pick.whole {
			return nil, nil, fmt.Errorf("cannot select whole layer %q together with individual shapes from it", ref.Layer)
		}
		si, ok := shapeIDs[li][ref.Shape]
		if !ok {
			return nil, nil, fmt.Errorf("selection references unknown shape %q in layer %q", ref.Shape, ref.Layer)
		}
		pick.shapes[si] = true
	}
	// Keep source layer order: layer order defines compositing, so request
	// ordering must never silently change which retained layer paints on top.
	picks := make([]layerPick, 0, len(pickByLayer))
	for li := range src.Layers {
		if pick, ok := pickByLayer[li]; ok {
			picks = append(picks, *pick)
		}
	}
	return picks, append([]repository.Ref(nil), selection...), nil
}

func cloneOps(ops []geom.Op) []geom.Op {
	if len(ops) == 0 {
		return nil
	}
	out := make([]geom.Op, len(ops))
	for i, o := range ops {
		out[i] = geom.Op{Kind: o.Kind}
		if len(o.Points) > 0 {
			out[i].Points = append([]geom.Point(nil), o.Points...)
		}
	}
	return out
}

func cloneShape(s scene.Shape) scene.Shape {
	return scene.Shape{
		ID:        s.ID,
		Path:      cloneOps(s.Path),
		FillRule:  s.FillRule,
		Brush:     cloneBrush(s.Brush),
		Transform: s.Transform,
	}
}

func cloneBrush(b scene.Brush) scene.Brush {
	out := scene.Brush{Kind: b.Kind, Color: b.Color, From: b.From, To: b.To}
	if len(b.Stops) > 0 {
		out.Stops = append([]scene.Stop(nil), b.Stops...)
	}
	return out
}

func shapePlateBounds(l scene.Layer, s scene.Shape) geom.Bounds {
	local := geom.PathBounds(geom.Flatten(s.Path))
	b := geom.TransformBounds(s.Transform, local)
	if len(l.Clip) > 0 {
		b = b.Intersect(geom.PathBounds(geom.Flatten(l.Clip)))
	}
	if len(l.Mask) > 0 {
		b = b.Intersect(geom.PathBounds(geom.Flatten(l.Mask)))
	}
	return b
}

func visibleBounds(src scene.Document, picks []layerPick) geom.Bounds {
	union := geom.Bounds{Empty: true}
	add := func(b geom.Bounds) {
		if b.Empty {
			return
		}
		if union.Empty {
			union = b
			return
		}
		if b.MinX < union.MinX {
			union.MinX = b.MinX
		}
		if b.MinY < union.MinY {
			union.MinY = b.MinY
		}
		if b.MaxX > union.MaxX {
			union.MaxX = b.MaxX
		}
		if b.MaxY > union.MaxY {
			union.MaxY = b.MaxY
		}
	}
	for _, pick := range picks {
		layer := src.Layers[pick.index]
		for i, s := range layer.Shapes {
			if pick.whole || pick.shapes[i] {
				add(shapePlateBounds(layer, s))
			}
		}
	}
	return union
}

func fitMatrix(b geom.Bounds, width, height int) geom.Matrix {
	scale := mathMin(float64(width)/b.Width(), float64(height)/b.Height())
	cx, cy := (b.MinX+b.MaxX)/2, (b.MinY+b.MaxY)/2
	return geom.Matrix{
		scale, 0, 0, scale,
		float64(width)/2 - scale*cx,
		float64(height)/2 - scale*cy,
	}
}

func mathMin(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

func clippedByCanvas(b geom.Bounds, width, height int) bool {
	return b.MinX < 0 || b.MinY < 0 || b.MaxX > float64(width) || b.MaxY > float64(height)
}

// applyPlateTransform rewrites the plate-space placement of every retained
// node. Shape-local paths and brush coordinates are untouched, so the visual
// unit scales together with its transform.
func applyPlateTransform(doc *scene.Document, m geom.Matrix) {
	mapOps := func(ops []geom.Op) {
		for i := range ops {
			for j := range ops[i].Points {
				ops[i].Points[j] = m.Apply(ops[i].Points[j])
			}
		}
	}
	for li := range doc.Layers {
		mapOps(doc.Layers[li].Clip)
		mapOps(doc.Layers[li].Mask)
		for si := range doc.Layers[li].Shapes {
			s := &doc.Layers[li].Shapes[si]
			s.Transform = geom.Compose(m, s.Transform)
		}
	}
}
