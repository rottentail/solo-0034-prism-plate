// Versioned SVG path text exchange (svg-path-1).
//
// This file only parses the grammar of an SVG path data string (the content of
// a "d" attribute): the verbs M/L/Q/C/Z, their relative counterparts and the
// numeric spellings allowed by the SVG grammar. It never sees markup, styles,
// scripts or external references, so no arbitrary SVG can enter through it.
// The accepted geometry stays inside the historical closed-contour contract:
// every subpath starts with M, closes explicitly with Z, and only closed
// contours with at least three vertices are admitted. Relative coordinates are
// resolved against the running current point, so the produced Op sequence is
// absolute and renders identically to a legacy op array.
package geom

import (
	"fmt"
	"math"
	"strconv"
	"strings"
)

// MaxPathText bounds a single svg-path-1 string before any token is parsed.
// The 8MiB HTTP envelope and the 256-op contour budget apply on top of this.
const MaxPathText = 16 * 1024

// ParseError locates a rejected svg-path-1 string by byte offset and
// 1-based column (the accepted grammar is a single line).
type ParseError struct {
	Offset int
	Column int
	Reason string
}

func (e *ParseError) Error() string {
	return fmt.Sprintf("svg-path-1 syntax error at byte %d (column %d): %s", e.Offset, e.Column, e.Reason)
}

type pathLexer struct {
	src string
	pos int
}

func (l *pathLexer) fail(at int, reason string) error {
	return &ParseError{Offset: at, Column: at + 1, Reason: reason}
}

// skipSpace consumes SVG path data separators (whitespace and commas).
func (l *pathLexer) skipSpace() {
	for l.pos < len(l.src) {
		switch l.src[l.pos] {
		case ' ', '\t', '\n', '\r', ',':
			l.pos++
		default:
			return
		}
	}
}

// readNumber parses one SVG grammar number:
//
//	sign? (digit* "." digits | digits "."? ) exponent?
//
// Forms such as ".5", "1.", "1e3", "-2.5E-2" and "+.25" are accepted;
// "NaN" and "Infinity" are not part of the grammar and are rejected.
func (l *pathLexer) readNumber() (float64, error) {
	start := l.pos
	if l.pos >= len(l.src) {
		return 0, l.fail(start, "expected number, found end of path text")
	}
	if l.src[l.pos] == '+' || l.src[l.pos] == '-' {
		l.pos++
	}
	digits := 0
	for l.pos < len(l.src) && l.src[l.pos] >= '0' && l.src[l.pos] <= '9' {
		l.pos++
		digits++
	}
	if l.pos < len(l.src) && l.src[l.pos] == '.' {
		l.pos++
		frac := 0
		for l.pos < len(l.src) && l.src[l.pos] >= '0' && l.src[l.pos] <= '9' {
			l.pos++
			frac++
		}
		if digits == 0 && frac == 0 {
			return 0, l.fail(start, "number requires digits around the decimal point")
		}
	} else if digits == 0 {
		return 0, l.fail(start, "expected number")
	}
	if l.pos < len(l.src) && (l.src[l.pos] == 'e' || l.src[l.pos] == 'E') {
		exp := l.pos
		l.pos++
		if l.pos < len(l.src) && (l.src[l.pos] == '+' || l.src[l.pos] == '-') {
			l.pos++
		}
		expDigits := 0
		for l.pos < len(l.src) && l.src[l.pos] >= '0' && l.src[l.pos] <= '9' {
			l.pos++
			expDigits++
		}
		if expDigits == 0 {
			return 0, l.fail(exp, "exponent requires digits")
		}
	}
	value, err := strconv.ParseFloat(l.src[start:l.pos], 64)
	if err != nil {
		return 0, l.fail(start, "invalid number "+strconv.Quote(l.src[start:l.pos]))
	}
	if math.IsNaN(value) || math.IsInf(value, 0) {
		return 0, l.fail(start, "number must be finite")
	}
	return value, nil
}

// readPoint parses one x/y coordinate pair. When relative is true it is
// resolved against cur; the returned point is always absolute.
func (l *pathLexer) readPoint(relative bool, cur Point) (Point, error) {
	l.skipSpace()
	x, err := l.readNumber()
	if err != nil {
		return Point{}, err
	}
	l.skipSpace()
	y, err := l.readNumber()
	if err != nil {
		return Point{}, err
	}
	if relative {
		x += cur.X
		y += cur.Y
	}
	return Point{X: x, Y: y}, nil
}

// ParseSVGPath converts versioned svg-path-1 text into the absolute Op
// representation shared by the raster and vector pipelines. The structural
// contour rules mirror validate.path so both representations carry exactly the
// same fill and hole semantics.
func ParseSVGPath(text string) ([]Op, error) {
	if len(text) == 0 {
		return nil, &ParseError{Offset: 0, Column: 1, Reason: "empty path text"}
	}
	if len(text) > MaxPathText {
		return nil, &ParseError{Offset: len(text), Column: len(text) + 1, Reason: "path text exceeds 16KiB"}
	}
	l := &pathLexer{src: text}
	l.skipSpace()
	if l.pos == len(l.src) {
		return nil, l.fail(0, "path text contains only separators")
	}
	var ops []Op
	var cur, subStart Point
	open := false
	vertices := 0
	for l.pos < len(l.src) {
		cmdAt := l.pos
		verb := l.src[l.pos]
		upper := verb
		if upper >= 'a' && upper <= 'z' {
			upper -= 'a' - 'A'
		}
		relative := verb >= 'a' && verb <= 'z'
		switch upper {
		case 'M', 'L', 'Q', 'C', 'Z':
		default:
			return nil, l.fail(cmdAt, fmt.Sprintf("unsupported verb %q (only M/L/Q/C/Z are accepted)", string(verb)))
		}
		l.pos++
		if upper == 'Z' {
			if !open {
				return nil, l.fail(cmdAt, "Z without an open contour")
			}
			if vertices < 3 {
				return nil, l.fail(cmdAt, "contour requires three vertices before Z")
			}
			ops = append(ops, Op{Kind: "Z"})
			open = false
			cur = subStart
			l.skipSpace()
			// A coordinate directly after Z can only belong to a new M verb.
			if l.pos < len(l.src) && !isLetter(l.src[l.pos]) {
				return nil, l.fail(l.pos, "coordinate after Z must begin with M")
			}
			continue
		}
		arity := map[byte]int{'M': 1, 'L': 1, 'Q': 2, 'C': 3}[upper]
		group := 0
		for {
			// Every coordinate pair of one verb group is relative to the
			// current point at the start of that group; intermediate control
			// points do not move the current point (SVG path grammar).
			start := cur
			pts := make([]Point, arity)
			for i := range pts {
				p, err := l.readPoint(relative, start)
				if err != nil {
					return nil, err
				}
				pts[i] = p
			}
			cur = pts[arity-1]
			if upper == 'M' {
				if group > 0 {
					// SVG would treat further M pairs as implicit L, but the
					// historical contract requires Z before another M.
					return nil, l.fail(cmdAt, "close each contour before another M")
				}
				if open {
					return nil, l.fail(cmdAt, "close each contour before another M")
				}
				open = true
				vertices = 1
				subStart = pts[0]
				ops = append(ops, Op{Kind: "M", Points: pts})
			} else {
				if !open {
					return nil, l.fail(cmdAt, "contour must begin with M")
				}
				vertices++
				ops = append(ops, Op{Kind: string(upper), Points: pts})
			}
			group++
			l.skipSpace()
			if l.pos >= len(l.src) || isLetter(l.src[l.pos]) {
				break
			}
			// Otherwise another coordinate group repeats the current verb
			// (implicit repeat rule of the SVG path grammar).
		}
	}
	if open {
		return nil, l.fail(len(text), "contour must terminate with Z")
	}
	if len(ops) == 0 {
		return nil, l.fail(0, "path text declares no contour")
	}
	if len(ops) > 256 {
		return nil, l.fail(len(text), "contour operation count must be 1..256")
	}
	return ops, nil
}

func isLetter(b byte) bool {
	return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z')
}

// SVGPathText renders Ops as canonical svg-path-1 text: absolute verbs, one
// space between every token, %.9g numbers and an explicit Z per contour. It is
// the stable spelling used for round-trip evidence and documentation; input
// accepts the full numeric grammar, output always uses this one form.
func SVGPathText(ops []Op) string {
	var b strings.Builder
	for i, o := range ops {
		if i > 0 {
			b.WriteByte(' ')
		}
		b.WriteString(o.Kind)
		for _, p := range o.Points {
			b.WriteByte(' ')
			b.WriteString(formatNumber(p.X))
			b.WriteByte(' ')
			b.WriteString(formatNumber(p.Y))
		}
	}
	return b.String()
}

func formatNumber(x float64) string {
	return fmt.Sprintf("%.9g", x)
}
