// Command boundary locates the exact rounding-collision boundaries of the
// "%.9g" SVG number format that make the viewer-side matrix singular, using
// the same fmt contract as internal/vector.
package main

import (
	"fmt"
	"math"
)

func num(x float64) string { return fmt.Sprintf("%.9g", x) }

func main() {
	fmt.Println("# 1. grid spacing of the 9-significant-digit format across magnitudes (adjacent distinct tokens)")
	for _, base := range []float64{1e2, 1e3, 1e4, 1e5, 316227.766, 1e6 - 1, 1e6, 1e6 + 1} {
		// smallest positive delta that changes the token
		lo, hi := 0.0, 1.0
		for num(base+hi) == num(base) {
			hi *= 2
		}
		for hi-lo > 1e-12 {
			mid := (lo + hi) / 2
			if num(base+mid) == num(base) {
				lo = mid
			} else {
				hi = mid
			}
		}
		fmt.Printf("near %.10g: token=%s smallest_token_changing_delta=%.10g next_token=%s\n",
			base, num(base), hi, num(base+hi))
	}

	fmt.Println()
	fmt.Println("# 2. exact collision boundary around the reported entries")
	m0 := 999999.9996
	for _, m2 := range []float64{999999.99945, 999999.99949, 999999.9995, 999999.99955,
		999999.9996, 999999.9997, 999999.9999, 1000000.0} {
		t0, t2 := num(m0), num(m2)
		singular := t0 == t2
		det := m0*999999.9996 - 999999.9996*m2 // m1=m3=m0
		fmt.Printf("m2=%.12g token(m0)=%s token(m2)=%s viewer_rounded_singular=%v exact_det=%.6g\n",
			m2, t0, t2, singular, det)
	}

	fmt.Println()
	fmt.Println("# 3. full binary search: largest |m2-m0| that still collides (m0=999999.9996)")
	// on the low side
	lo, hi := 0.0, 1.0
	for num(m0-hi) == num(m0) {
		hi *= 2
	}
	for hi-lo > 1e-12 {
		mid := (lo + hi) / 2
		if num(m0-mid) == num(m0) {
			lo = mid
		} else {
			hi = mid
		}
	}
	fmt.Printf("low-side collision radius ~ %.12g (m2 down to %.12g)\n", lo, m0-lo)
	lo, hi = 0.0, 1.0
	for num(m0+hi) == num(m0) {
		hi *= 2
	}
	for hi-lo > 1e-12 {
		mid := (lo + hi) / 2
		if num(m0+mid) == num(m0) {
			lo = mid
		} else {
			hi = mid
		}
	}
	fmt.Printf("high-side collision radius ~ %.12g (m2 up to %.12g)\n", lo, m0+lo)

	fmt.Println()
	fmt.Println("# 4. path-coordinate rounding: which local vertices collapse")
	coords := []float64{319999.99988, -319999.999872, -319999.999832, 319999.999872}
	for _, c := range coords {
		tok := num(c)
		var back float64
		fmt.Sscanf(tok, "%g", &back)
		fmt.Printf("input=%.12g token=%s parsed_back=%.10g abs_round_error=%.10g\n",
			c, tok, back, math.Abs(back-c))
	}
}
