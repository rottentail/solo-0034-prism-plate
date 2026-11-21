// Command gotieprobe documents how the Go math library resolves sRGB
// half-code tie points.  It deliberately imports no prismplate package:
// the point is to show that a one-code disagreement at an exact 64.5-style
// boundary originates in the libm pow implementation, independently of any
// production code.  Run with: go run ./analysis/gotieprobe
package main

import (
	"fmt"
	"math"
	"os"
)

func linearGo(v float64) float64 {
	if v <= 0.04045 {
		return v / 12.92
	}
	return math.Pow((v+0.055)/1.055, 2.4)
}

func srgbGo(v float64) float64 {
	v = math.Max(0, math.Min(1, v))
	if v <= 0.0031308 {
		return v * 12.92
	}
	return 1.055*math.Pow(v, 1.0/2.4) - 0.055
}

func main() {
	// Every half-code boundary k+0.5 over the transfer round-trip.
	fmt.Println("half_code,roundtrip_x255,rounded_code")
	for k := 0; k < 255; k++ {
		v := (float64(k) + 0.5) / 255.0
		rt := srgbGo(linearGo(v)) * 255.0
		fmt.Printf("%d,%.17g,%d\n", k, rt, int(math.Round(rt)))
	}
	// Headline case observed in code-lattice-probe.
	v := 64.5 / 255.0
	rt := srgbGo(linearGo(v)) * 255.0
	fmt.Fprintf(os.Stderr, "k=64.5 tie -> %.17g -> Round=%d (exact decimal "+
		"value is 64.499999999999991..., i.e. one ULP below the tie)\n",
		rt, int(math.Round(rt)))
}
