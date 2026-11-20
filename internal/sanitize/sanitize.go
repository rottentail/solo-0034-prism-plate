// Package sanitize derives a delivery-only source draft that contains no
// geometry outside a caller-supplied convex region.
//
// The conversion is geometric, not visual. Outlines are cut to explicit closed
// contours, clips and masks that merely cover the region are replaced, and every
// retained vertex lies inside the region. Removing the delivery region
// therefore cannot reveal anything: the removed arcs are absent from the
// document, not hidden behind a clip or a visibility flag.
package sanitize

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"sort"

	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

// ReportVersion identifies the sanitization contract that produced a delivery.
const ReportVersion = "prism-plate-sanitize-v1"

// Report explains what the delivery contains and what error the receiver should
// expect. It is returned with the delivery, not sealed into the source draft.
//
// The error statement is empirical, not aspirational: CompareBound renders the
// source and the delivery through the canonical PNG backend and reports the
// largest 8-bit channel difference over every interior pixel. A zero therefore
// means the delivery is pixel-identical to the source inside the region on the
// authoritative output. Pixels whose four subsamples straddle the boundary are
// counted separately (BoundarySkipped) because a hard polygon edge and a
// subpixel-sampled edge can resolve differently there by construction.
type Report struct {
	Schema          string       `json:"schema"`
	Region          []geom.Point `json:"region"`
	RegionArea      float64      `json:"region_area"`
	BoundaryEpsilon float64      `json:"boundary_epsilon"`
	KeptLayers      int          `json:"kept_layers"`
	KeptShapes      int          `json:"kept_shapes"`
	BakedShapes     int          `json:"baked_shapes"`
	ClippedShapes   int          `json:"clipped_shapes"`
	DroppedShapes   int          `json:"dropped_shapes"`
	ClippedClips    int          `json:"clipped_clips"`
	DroppedClips    int          `json:"dropped_clips"`
	ClippedMasks    int          `json:"clipped_masks"`
	DroppedMasks    int          `json:"dropped_masks"`
	SampledError    int          `json:"max_png_channel_error"`
	VerifiedPixels  int          `json:"compared_pixels"`
	BoundarySkipped int          `json:"boundary_skipped_pixels"`
	OutsidePixels   int          `json:"outside_pixels"`
	OutsideError    int          `json:"max_outside_channel_error"`
	CurveTolerance  float64      `json:"curve_tolerance"`
	Notes           []string     `json:"notes,omitempty"`
}

// Result is a sanitized scene plus the evidence that accompanies delivery.
type Result struct {
	Document scene.Document
	Report   Report
	Digest   string
}

// Eligibility is the preflight answer: whether every revision can become a
// self-contained delivery and, if not, exactly which shapes block it.
type Eligibility struct {
	Convertible bool      `json:"convertible"`
	Blockers    []Blocker `json:"blockers,omitempty"`
	Notes       []string  `json:"notes,omitempty"`
}

type Blocker struct {
	Revision int    `json:"revision"`
	Layer    string `json:"layer,omitempty"`
	Shape    string `json:"shape,omitempty"`
	Reason   string `json:"reason"`
}

// Error marks a conversion that was refused because it cannot be represented
// faithfully. It is a caller-visible, non-retryable answer, never a partial
// delivery.
type Error struct{ Reason string }

func (e *Error) Error() string { return "cannot sanitize safely: " + e.Reason }

func fail(format string, args ...any) error { return &Error{Reason: fmt.Sprintf(format, args...)} }

// Revision couples a revision number with the scene to convert.
type Revision struct {
	Number int
	Scene  scene.Document
}

// maxRingVertices bounds the quadratic simplicity check. Rings above it are
// refused rather than silently approximated.
const maxRingVertices = 8192

type ring struct {
	pts []geom.Point
}

type ringClass int

const (
	ringInside ringClass = iota
	ringOutside
	ringCrossing
	// ringEncloses means the region lies entirely inside the ring while the
	// ring's own vertices lie outside it. It still contributes winding (or
	// parity) to every point in the region, so it must become the region
	// polygon with the ring's own orientation rather than being dropped.
	ringEncloses
)

// pointsEnclose reports whether every clip vertex is enclosed by r and no
// edge of r properly crosses an edge of clip. A ring that merely touches the
// boundary (vertices on the clip line) still encloses the region; it is not a
// crossing ring, and treating it as one would cut geometry that needs no cut.
func pointsEnclose(r, clip []geom.Point, tol float64) bool {
	for _, p := range clip {
		if !ringContains(r, p) {
			return false
		}
	}
	return true
}

// crossesProperly reports an edge pair that intersects away from shared
// endpoints. Touches and collinear overlaps are not proper crossings.
func crossesProperly(r, clip []geom.Point, tol float64) bool {
	for i := range r {
		a, b := r[i], r[(i+1)%len(r)]
		for j := range clip {
			c, d := clip[j], clip[(j+1)%len(clip)]
			if properCross(a, b, c, d, tol) {
				return true
			}
		}
	}
	return false
}

func properCross(a, b, c, d geom.Point, tol float64) bool {
	d1, d2 := orient(c, d, a), orient(c, d, b)
	d3, d4 := orient(a, b, c), orient(a, b, d)
	return ((d1 > tol && d2 < -tol) || (d1 < -tol && d2 > tol)) &&
		((d3 > tol && d4 < -tol) || (d3 < -tol && d4 > tol))
}

// classify uses edge intersections as well as vertex containment, so a contour
// crossing the region with every vertex outside is still found.
func classify(r []geom.Point, clip []geom.Point, tol float64) ringClass {
	if len(r) < 3 {
		return ringOutside
	}
	insideAll, outsideAll := true, true
	for _, p := range r {
		if pointInConvex(p, clip, tol) {
			outsideAll = false
		} else {
			insideAll = false
		}
	}
	if insideAll {
		return ringInside
	}
	if !outsideAll {
		// Vertices on both sides of the bounding structure. Only a proper
		// edge crossing actually requires a boolean cut; a ring whose edges
		// follow the boundary needs no change.
		if crossesProperly(r, clip, tol) {
			return ringCrossing
		}
		if pointsEnclose(r, clip, tol) {
			return ringEncloses
		}
		return ringCrossing
	}
	if pointsEnclose(r, clip, tol) {
		return ringEncloses
	}
	for _, p := range clip {
		if ringContains(r, p) {
			return ringCrossing
		}
	}
	if crossesProperly(r, clip, tol) {
		return ringCrossing
	}
	return ringOutside
}

// enclosedRing is the part of the region covered by a ring that encloses the
// whole region: the region itself, oriented like the authored ring so winding
// and even-odd holes keep their meaning.
func enclosedRing(a Area, r []geom.Point) ring {
	pts := append([]geom.Point(nil), a.Pts...)
	if polygonArea(r) < 0 {
		for i, j := 0, len(pts)-1; i < j; i, j = i+1, j-1 {
			pts[i], pts[j] = pts[j], pts[i]
		}
	}
	return ring{pts: pts}
}

func ringContains(ring []geom.Point, p geom.Point) bool {
	in := false
	for i, a := range ring {
		b := ring[(i+1)%len(ring)]
		if (a.Y > p.Y) != (b.Y > p.Y) {
			x := a.X + (p.Y-a.Y)*(b.X-a.X)/(b.Y-a.Y)
			if x > p.X {
				in = !in
			}
		}
	}
	return in
}

func orient(a, b, c geom.Point) float64 {
	return (b.X-a.X)*(c.Y-a.Y) - (b.Y-a.Y)*(c.X-a.X)
}

func segmentsIntersect(a, b, c, d geom.Point, tol float64) bool {
	d1, d2 := orient(c, d, a), orient(c, d, b)
	d3, d4 := orient(a, b, c), orient(a, b, d)
	if ((d1 > tol && d2 < -tol) || (d1 < -tol && d2 > tol)) &&
		((d3 > tol && d4 < -tol) || (d3 < -tol && d4 > tol)) {
		return true
	}
	on := func(p, q, r geom.Point) bool {
		return math.Abs(orient(p, q, r)) <= tol &&
			math.Min(p.X, q.X)-tol <= r.X && r.X <= math.Max(p.X, q.X)+tol &&
			math.Min(p.Y, q.Y)-tol <= r.Y && r.Y <= math.Max(p.Y, q.Y)+tol
	}
	return on(c, d, a) || on(c, d, b) || on(a, b, c) || on(a, b, d)
}

// selfIntersecting screens the subject of a boolean intersection. The half-plane
// cutter is only valid for simple polygons, so a crossing contour that fails
// this screen is reported instead of being cut incorrectly.
func selfIntersecting(r []geom.Point, tol float64) (bool, bool) {
	n := len(r)
	if n > maxRingVertices {
		return false, false
	}
	for i := 0; i < n; i++ {
		a, b := r[i], r[(i+1)%n]
		for j := i + 2; j < n; j++ {
			if i == 0 && j == n-1 {
				continue
			}
			c, d := r[j], r[(j+1)%n]
			if math.Max(a.X, b.X) < math.Min(c.X, d.X)-tol || math.Max(c.X, d.X) < math.Min(a.X, b.X)-tol ||
				math.Max(a.Y, b.Y) < math.Min(c.Y, d.Y)-tol || math.Max(c.Y, d.Y) < math.Min(a.Y, b.Y)-tol {
				continue
			}
			if segmentsIntersect(a, b, c, d, tol) {
				return true, true
			}
		}
	}
	return false, true
}

// checkRevision mirrors Sanitize's classification exactly, but without
// building geometry or rendering. Blend mode, layer opacity, clipped fills and
// gradient axes are all reproducible inside the region because the delivery
// recomputes composition from the same background and keeps each shape's own
// frame, so they are not grounds for refusal.
//
// What cannot be represented is a crossing contour whose input is not a simple
// closed ring, or a transformed region that is no longer a convex polygon.
func checkRevision(d scene.Document, area Area) *Blocker {
	for _, l := range d.Layers {
		// Iterate a slice, not a map: the first blocker reported must not depend
		// on map iteration order.
		for _, p := range []struct {
			name string
			ops  []geom.Op
		}{{"clip", l.Clip}, {"mask", l.Mask}} {
			if len(p.ops) == 0 {
				continue
			}
			if reason := pathBlock(p.ops, area); reason != "" {
				return &Blocker{Layer: l.ID, Reason: p.name + " " + reason}
			}
		}
		for _, s := range l.Shapes {
			inv, ok := s.Transform.Inverse()
			if !ok {
				return &Blocker{Layer: l.ID, Shape: s.ID, Reason: "局部变换不可逆"}
			}
			local, err := NewPolygon(area.MapPoints(inv))
			if err != nil {
				return &Blocker{Layer: l.ID, Shape: s.ID, Reason: "变换后的交付区域不是凸多边形，无法在局部坐标内裁切"}
			}
			if reason := pathBlock(s.Path, local); reason != "" {
				return &Blocker{Layer: l.ID, Shape: s.ID, Reason: reason}
			}
		}
	}
	return nil
}

// pathBlock reports the first contour of path that cannot be cut safely.
func pathBlock(path []geom.Op, area Area) string {
	for _, r := range geom.Flatten(path) {
		pts := dedupe(r, area.scale)
		if len(pts) < 3 {
			continue
		}
		c := classify(pts, area.Pts, area.Epsilon())
		if c != ringCrossing && c != ringEncloses {
			continue
		}
		if bad, bounded := selfIntersecting(pts, area.Epsilon()); !bounded {
			return "交叉边界的轮廓顶点超过简化验证上限，无法在预算内保证裁切正确"
		} else if bad {
			return "交叉边界的轮廓自相交，无法在不改变可见填充的前提下安全裁切"
		}
	}
	return ""
}

// Preflight classifies revisions without producing geometry or committing
// anything. Callers use it to tell the sender what cannot be handed over before
// an export is attempted.
func Preflight(revisions []Revision, points []geom.Point) Eligibility {
	e := Eligibility{Convertible: true}
	for _, rv := range revisions {
		area, err := AreaFor(points, rv.Scene)
		if err != nil {
			e.Convertible = false
			e.Blockers = append(e.Blockers, Blocker{Revision: rv.Number, Reason: err.Error()})
			continue
		}
		if b := checkRevision(rv.Scene, area); b != nil {
			b.Revision = rv.Number
			e.Convertible = false
			e.Blockers = append(e.Blockers, *b)
		}
	}
	sort.SliceStable(e.Blockers, func(i, j int) bool {
		a, b := e.Blockers[i], e.Blockers[j]
		if a.Revision != b.Revision {
			return a.Revision < b.Revision
		}
		if a.Layer != b.Layer {
			return a.Layer < b.Layer
		}
		return a.Shape < b.Shape
	})
	if !e.Convertible {
		e.Notes = append(e.Notes, "按当前交付区域存在无法安全转换的稿本；请扩大区域、拆分形状或改为简单闭合轮廓后重试")
	}
	return e
}

// AreaFor rebuilds the validated region for one document's canvas.
func AreaFor(points []geom.Point, d scene.Document) (Area, error) {
	return NewArea(points, d.Width, d.Height)
}

// restrictPath intersects a layer clip or mask with the region. It reports the
// number of contours that were actually cut or dropped, and whether the path
// retains any area at all.
func restrictPath(ops []geom.Op, area Area) (kept []geom.Op, cut, dropped int, empty bool, err error) {
	allInside := true
	var rings []ring
	for _, raw := range geom.Flatten(ops) {
		pts := dedupe(raw, area.scale)
		if len(pts) < 3 {
			dropped++
			allInside = false
			continue
		}
		switch classify(pts, area.Pts, area.Epsilon()) {
		case ringInside:
			rings = append(rings, ring{pts: pts})
		case ringOutside:
			dropped++
			allInside = false
		case ringEncloses:
			// The ring covers the whole region. Keeping it as authored would
			// leave a mask or clip larger than the delivery area, which is the
			// hiding mechanism this export exists to remove. Replace it with
			// the region polygon, oriented like the original ring.
			rings = append(rings, enclosedRing(area, pts))
			allInside = false
		case ringCrossing:
			cut++
			allInside = false
			parts, e := clipToConvex([][]geom.Point{pts}, area.Pts, area.scale)
			if e != nil {
				return nil, cut, dropped, false, e
			}
			for _, r := range parts {
				rings = append(rings, ring{pts: r})
			}
		}
	}
	if len(rings) == 0 {
		return nil, cut, dropped, true, nil
	}
	if allInside {
		return ops, 0, 0, false, nil
	}
	return ringsToOps(rings), cut, dropped, false, nil
}

func ringsToOps(rings []ring) []geom.Op {
	ops := make([]geom.Op, 0, len(rings)*4)
	for _, r := range rings {
		r.pts = dedupe(r.pts, 1)
		if len(r.pts) < 3 {
			continue
		}
		ops = append(ops, geom.Op{Kind: "M", Points: []geom.Point{r.pts[0]}})
		for _, p := range r.pts[1:] {
			ops = append(ops, geom.Op{Kind: "L", Points: []geom.Point{p}})
		}
		ops = append(ops, geom.Op{Kind: "Z"})
	}
	return ops
}

// Sanitize converts one revision into a standalone delivery document. It is
// deterministic for a fixed document and region.
//
// Per authored contour:
//   - wholly inside: kept byte-for-byte, including curves and its local
//     transform, so the receiver can keep editing the original shape;
//   - wholly outside: removed, not hidden; nothing can be recovered later;
//   - crossing: flattened with the shared 16/24 contract, cut against the
//     region with an exact convex boolean, and emitted as an explicit closed
//     polygon. The shape keeps its authored local transform and brush, so a
//     gradient axis and the rest of the shape stay editable.
//   - surrounding: the region lies wholly inside the contour; the contour is
//     replaced by the region polygon, or it would paint outside the delivery.
//
// Layer clips and masks are treated the same way: a clip that merely covers the
// region is not enough, the exported path itself is restricted.
func Sanitize(d scene.Document, area Area) (Result, error) {
	res := Result{}
	if b := checkRevision(d, area); b != nil {
		if b.Shape == "" {
			return res, fail("layer %q: %s", b.Layer, b.Reason)
		}
		return res, fail("layer %q shape %q: %s", b.Layer, b.Shape, b.Reason)
	}
	out := d
	out.Layers = nil
	rep := Report{
		Schema:          ReportVersion,
		Region:          append([]geom.Point(nil), area.Pts...),
		RegionArea:      area.AreaValue(),
		BoundaryEpsilon: area.Epsilon(),
		CurveTolerance:  curveTolerance(d),
	}
	for _, l := range d.Layers {
		nl := l
		if len(l.Clip) > 0 {
			ops, cut, dropped, empty, rerr := restrictPath(l.Clip, area)
			if rerr != nil {
				return res, fail("layer %q clip: %v", l.ID, rerr)
			}
			rep.ClippedClips += cut
			rep.DroppedClips += dropped
			if empty {
				continue
			}
			nl.Clip = ops
		}
		if len(l.Mask) > 0 {
			ops, cut, dropped, empty, rerr := restrictPath(l.Mask, area)
			if rerr != nil {
				return res, fail("layer %q mask: %v", l.ID, rerr)
			}
			rep.ClippedMasks += cut
			rep.DroppedMasks += dropped
			if empty {
				continue
			}
			nl.Mask = ops
		}
		nl.Shapes = nil
		for _, s := range l.Shapes {
			// Cut in the shape's own local space. The inverse image of the
			// convex region under an invertible affine map is convex, so the
			// same exact boolean applies; keeping the authored transform and
			// brush untouched also means gradients and local editing survive
			// unchanged, which baking into layout space would not guarantee
			// for non-similarity matrices.
			inv, ok := s.Transform.Inverse()
			if !ok {
				return res, fail("shape %q: 局部变换不可逆", s.ID)
			}
			localPts := area.MapPoints(inv)
			local, e := NewPolygon(localPts)
			if e != nil {
				// A non-convex inverse image is only possible from a singular
				// or wildly conditioned transform; report rather than guess.
				return res, fail("shape %q: 变换后的交付区域不是凸多边形: %v", s.ID, e)
			}
			contours := splitContours(s.Path)
			rings := geom.Flatten(s.Path)
			// Each contour is either kept exactly as authored or replaced by
			// its cut polygon. The two can be mixed in one shape, so an
			// untouched inner hole keeps its authored winding and curves.
			parts := make([][]geom.Op, 0, len(contours))
			anyKept, converted := false, false
			for i, ops := range contours {
				var flat []geom.Point
				if i < len(rings) {
					flat = rings[i]
				}
				pts := dedupe(flat, local.scale)
				switch classify(pts, local.Pts, local.Epsilon()) {
				case ringInside:
					parts = append(parts, ops)
					anyKept = true
				case ringOutside:
					rep.DroppedShapes++
				case ringEncloses:
					// The contour surrounds the whole region. Keeping it whole
					// would paint outside the delivery area, so it is replaced
					// by the region polygon, oriented like the original.
					parts = append(parts, ringsToOps([]ring{enclosedRing(local, pts)}))
					anyKept = true
					converted = true
					rep.ClippedShapes++
				case ringCrossing:
					cut, e := clipToConvex([][]geom.Point{pts}, local.Pts, local.scale)
					if e != nil {
						return res, fail("shape %q: %v", s.ID, e)
					}
					rep.ClippedShapes++
					if len(cut) == 0 {
						rep.DroppedShapes++
						continue
					}
					rs := make([]ring, 0, len(cut))
					for _, c := range cut {
						rs = append(rs, ring{pts: c})
					}
					parts = append(parts, ringsToOps(rs))
					anyKept = true
					converted = true
				}
			}
			if !anyKept {
				continue
			}
			ns := s
			path := make([]geom.Op, 0, len(s.Path))
			for _, p := range parts {
				path = append(path, p...)
			}
			ns.Path = path
			nl.Shapes = append(nl.Shapes, ns)
			rep.KeptShapes++
			if converted {
				rep.BakedShapes++
			}
		}
		if len(nl.Shapes) == 0 {
			continue
		}
		rep.KeptLayers++
		out.Layers = append(out.Layers, nl)
	}
	if len(out.Layers) == 0 {
		return res, fail("交付区域内没有保留下来的图层")
	}
	rep.Notes = append(rep.Notes,
		"轮廓按交付区域几何裁切并写成显式闭合路径；移走交付区域不会恢复区域外几何，因为它们在源稿中已不存在",
		"与边界相交的轮廓以多边形取代，完全在区域内的轮廓保留原始曲线；所有形状保留各自局部变换与渐变轴，可继续编辑",
		"曲线沿用既有 16/24 段展开约定，最大折线偏差见 curve_tolerance；边界交点容差见 boundary_epsilon",
		"max_png_channel_error 为原稿与交付稿在区域内部按 PNG 像素逐点比较的最大通道差；边界像素单列，不并入容差",
		"交付稿为单稿本，不含原历史、成品、校样或任务；导入空数据根即可打开",
	)
	res.Document = out
	res.Report = rep
	b, err := json.Marshal(out)
	if err != nil {
		return res, err
	}
	sum := sha256.Sum256(b)
	res.Digest = hex.EncodeToString(sum[:])
	return res, nil
}
