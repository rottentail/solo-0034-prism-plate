package geom

import "math"

// Homography is a projective mapping stored row-major as
//
//	[a b c]
//	[d e f]
//	[g h i]
//
// A point is mapped to ((a*x+b*y+c)/w, (d*x+e*y+f)/w) with w = g*x+h*y+i.
// Values are normalized so that i == 1 whenever that is numerically safe.
type Homography [9]float64

const (
	homographyEpsilon = 1e-12
	coordinateLimit   = 1e6
)

func isFinite(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }

func normalize(h Homography) Homography {
	max := 0.0
	for _, v := range h {
		if !isFinite(v) {
			return Homography{}
		}
		if a := math.Abs(v); a > max {
			max = a
		}
	}
	if max == 0 {
		return Homography{}
	}
	if math.Abs(h[8]) > homographyEpsilon*max {
		for i := range h {
			h[i] /= h[8]
		}
		return h
	}
	for i := range h {
		h[i] /= max
	}
	return h
}

// Apply maps a point and reports whether the result is finite. Points on or
// numerically adjacent to the projective horizon are rejected instead of
// producing an unbounded coordinate.
func (h Homography) Apply(p Point) (Point, bool) {
	w := h[6]*p.X + h[7]*p.Y + h[8]
	if !isFinite(w) || math.Abs(w) <= homographyEpsilon {
		return Point{}, false
	}
	x := (h[0]*p.X + h[1]*p.Y + h[2]) / w
	y := (h[3]*p.X + h[4]*p.Y + h[5]) / w
	if !isFinite(x) || !isFinite(y) || math.Abs(x) > coordinateLimit || math.Abs(y) > coordinateLimit {
		return Point{}, false
	}
	return Point{X: x, Y: y}, true
}

// Inverse returns the inverse mapping with the same bounded behaviour.
func (h Homography) Inverse() (Homography, bool) {
	a, b, c := h[0], h[1], h[2]
	d, e, f := h[3], h[4], h[5]
	g, k, i := h[6], h[7], h[8]
	det := a*(e*i-f*k) - b*(d*i-f*g) + c*(d*k-e*g)
	max := 0.0
	for _, v := range h {
		if a := math.Abs(v); a > max {
			max = a
		}
	}
	if !isFinite(det) || max == 0 || math.Abs(det) <= homographyEpsilon*max*max*max {
		return Homography{}, false
	}
	inv := Homography{
		e*i - f*k, c*k - b*i, b*f - c*e,
		f*g - d*i, a*i - c*g, c*d - a*f,
		d*k - e*g, b*g - a*k, a*e - b*d,
	}
	for j := range inv {
		inv[j] /= det
	}
	inv = normalize(inv)
	for _, v := range inv {
		if !isFinite(v) {
			return Homography{}, false
		}
	}
	return inv, true
}

// SquareToQuad maps unit-square corners (0,0),(1,0),(1,1),(0,1) onto q in the
// same order. It returns false for degenerate configurations.
func SquareToQuad(q [4]Point) (Homography, bool) {
	x0, y0 := q[0].X, q[0].Y
	x1, y1 := q[1].X, q[1].Y
	x2, y2 := q[2].X, q[2].Y
	x3, y3 := q[3].X, q[3].Y
	dx1, dx2, dx3 := x1-x2, x3-x2, x0-x1+x2-x3
	dy1, dy2, dy3 := y1-y2, y3-y2, y0-y1+y2-y3
	var a, b, c, d, e, f, g, k float64
	span := 1 + math.Abs(x0) + math.Abs(x1) + math.Abs(x2) + math.Abs(x3) + math.Abs(y0) + math.Abs(y1) + math.Abs(y2) + math.Abs(y3)
	if math.Abs(dx3) <= homographyEpsilon*span && math.Abs(dy3) <= homographyEpsilon*span {
		// Parallelograms have no perspective term.
		a, b, c = x1-x0, x2-x1, x0
		d, e, f = y1-y0, y2-y1, y0
		g, k = 0, 0
	} else {
		den := dx1*dy2 - dy1*dx2
		if math.Abs(den) <= homographyEpsilon*(math.Abs(dx1*dy2)+math.Abs(dy1*dx2)+1) {
			return Homography{}, false
		}
		g = (dx3*dy2 - dy3*dx2) / den
		k = (dx1*dy3 - dy1*dx3) / den
		a, b, c = x1-x0+g*x1, x3-x0+k*x3, x0
		d, e, f = y1-y0+g*y1, y3-y0+k*y3, y0
	}
	h := normalize(Homography{a, b, c, d, e, f, g, k, 1})
	for _, v := range h {
		if !isFinite(v) {
			return Homography{}, false
		}
	}
	return h, true
}

// QuadHomography maps the source quadrilateral onto the destination
// quadrilateral. Corners are ordered top-left, top-right, bottom-right,
// bottom-left in each quad's own coordinate space.
func QuadHomography(src, dst [4]Point) (Homography, bool) {
	s, ok := SquareToQuad(src)
	if !ok {
		return Homography{}, false
	}
	d, ok := SquareToQuad(dst)
	if !ok {
		return Homography{}, false
	}
	si, ok := s.Inverse()
	if !ok {
		return Homography{}, false
	}
	h := Compose(d, si)
	for _, v := range h {
		if !isFinite(v) {
			return Homography{}, false
		}
	}
	return h, true
}

// Compose returns m∘n: Compose(m, n).Apply(p) == m.Apply(n.Apply(p)).
func Compose(m, n Homography) Homography {
	var out Homography
	for r := 0; r < 3; r++ {
		for c := 0; c < 3; c++ {
			for k := 0; k < 3; k++ {
				out[r*3+c] += m[r*3+k] * n[k*3+c]
			}
		}
	}
	return normalize(out)
}

// ProjectPath maps every flattened vertex through h. It fails if any vertex is
// on or beyond the numeric horizon, or if the outline straddles the horizon:
// such a contour has no bounded straight-line image and must be rejected rather
// than clipped into runaway coordinates.
func ProjectPath(p Path, h Homography) (Path, bool) {
	out := make(Path, 0, len(p))
	var sign float64
	first := true
	for _, ring := range p {
		mapped := make(Contour, 0, len(ring))
		for _, v := range ring {
			w := h[6]*v.X + h[7]*v.Y + h[8]
			if !isFinite(w) || math.Abs(w) <= homographyEpsilon {
				return nil, false
			}
			s := math.Copysign(1, w)
			if first {
				sign, first = s, false
			} else if s != sign {
				return nil, false
			}
			q, ok := h.Apply(v)
			if !ok || math.Abs(q.X) > coordinateLimit || math.Abs(q.Y) > coordinateLimit {
				return nil, false
			}
			mapped = append(mapped, q)
		}
		out = append(out, mapped)
	}
	return out, true
}

// QuadContains reports whether p lies inside a convex quad. It is used to
// paste artwork onto a display face: samples outside the face are not painted.
func QuadContains(q [4]Point, p Point) bool {
	var sign float64
	for i := 0; i < 4; i++ {
		a, b := q[i], q[(i+1)%4]
		cross := (b.X-a.X)*(p.Y-a.Y) - (b.Y-a.Y)*(p.X-a.X)
		if math.Abs(cross) <= 1e-12 {
			continue
		}
		s := math.Copysign(1, cross)
		if sign == 0 {
			sign = s
		} else if s != sign {
			return false
		}
	}
	return true
}

// QuadUsable reports whether a quadrilateral is finite, strictly convex and far
// enough from collinearity that a homography through it stays numerically
// meaningful. Self-intersecting ("bow-tie") quads have inconsistent turn signs
// and are rejected.
func QuadUsable(q [4]Point) bool {
	var sign float64
	area := 0.0
	for i := 0; i < 4; i++ {
		a, b, c := q[i], q[(i+1)%4], q[(i+2)%4]
		for _, p := range []Point{a, b, c} {
			if !isFinite(p.X) || !isFinite(p.Y) {
				return false
			}
		}
		ux, uy := b.X-a.X, b.Y-a.Y
		vx, vy := c.X-b.X, c.Y-b.Y
		lu, lv := math.Hypot(ux, uy), math.Hypot(vx, vy)
		if lu < 1e-9 || lv < 1e-9 {
			return false
		}
		cross := ux*vy - uy*vx
		if math.Abs(cross) <= 1e-9*lu*lv {
			return false
		}
		s := math.Copysign(1, cross)
		if sign == 0 {
			sign = s
		} else if s != sign {
			return false
		}
		area += a.X*b.Y - b.X*a.Y
	}
	if math.Abs(area)/2 < 1e-6 {
		return false
	}
	s, ok := SquareToQuad(q)
	if !ok {
		return false
	}
	_, ok = s.Inverse()
	return ok
}

// AffineApprox returns the best-fit affine map from source coordinates to the
// homography's image, sampled on a fixed grid over the source quad. It is used
// only to produce the explicitly labelled affine comparison output.
func AffineApprox(h Homography, src [4]Point) (Matrix, bool) {
	s, ok := SquareToQuad(src)
	if !ok {
		return Matrix{}, false
	}
	var n, sx, sy, sxx, sxy, syy float64
	var sqx, sqy, sxqx, syqx, sxqy, syqy float64
	const steps = 4
	for i := 0; i <= steps; i++ {
		for j := 0; j <= steps; j++ {
			u, v := float64(i)/steps, float64(j)/steps
			w := s[6]*u + s[7]*v + s[8]
			if math.Abs(w) <= homographyEpsilon {
				return Matrix{}, false
			}
			p := Point{X: (s[0]*u + s[1]*v + s[2]) / w, Y: (s[3]*u + s[4]*v + s[5]) / w}
			q, ok := h.Apply(p)
			if !ok {
				return Matrix{}, false
			}
			n++
			sx += p.X
			sy += p.Y
			sxx += p.X * p.X
			sxy += p.X * p.Y
			syy += p.Y * p.Y
			sqx += q.X
			sqy += q.Y
			sxqx += p.X * q.X
			syqx += p.Y * q.X
			sxqy += p.X * q.Y
			syqy += p.Y * q.Y
		}
	}
	a, b, c, ok := solve3([3][3]float64{{sxx, sxy, sx}, {sxy, syy, sy}, {sx, sy, n}}, [3]float64{sxqx, syqx, sqx})
	if !ok {
		return Matrix{}, false
	}
	d, e, f, ok := solve3([3][3]float64{{sxx, sxy, sx}, {sxy, syy, sy}, {sx, sy, n}}, [3]float64{sxqy, syqy, sqy})
	if !ok {
		return Matrix{}, false
	}
	m := Matrix{a, d, b, e, c, f}
	for _, v := range m {
		if !isFinite(v) {
			return Matrix{}, false
		}
	}
	if _, ok := m.Inverse(); !ok {
		return Matrix{}, false
	}
	return m, true
}

func solve3(a [3][3]float64, b [3]float64) (float64, float64, float64, bool) {
	for col := 0; col < 3; col++ {
		pivot := col
		for row := col + 1; row < 3; row++ {
			if math.Abs(a[row][col]) > math.Abs(a[pivot][col]) {
				pivot = row
			}
		}
		if math.Abs(a[pivot][col]) < 1e-12 {
			return 0, 0, 0, false
		}
		if pivot != col {
			a[col], a[pivot] = a[pivot], a[col]
			b[col], b[pivot] = b[pivot], b[col]
		}
		for row := col + 1; row < 3; row++ {
			f := a[row][col] / a[col][col]
			for k := col; k < 3; k++ {
				a[row][k] -= f * a[col][k]
			}
			b[row] -= f * b[col]
		}
	}
	var x [3]float64
	for row := 2; row >= 0; row-- {
		sum := b[row]
		for k := row + 1; k < 3; k++ {
			sum -= a[row][k] * x[k]
		}
		x[row] = sum / a[row][row]
		if !isFinite(x[row]) {
			return 0, 0, 0, false
		}
	}
	return x[0], x[1], x[2], true
}
