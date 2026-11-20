// Package render_test exercises the output backends without a data root.
package render_test

import (
	"bytes"
	"context"
	"go/parser"
	"go/token"
	"image/png"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"prismplate/internal/geom"
	"prismplate/internal/raster"
	"prismplate/internal/render"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"prismplate/internal/vector"
)

func document() scene.Document {
	return scene.Document{
		Width: 24, Height: 16,
		Background: scene.Color{R: 0.05, G: 0.1, B: 0.2, A: 1},
		Layers: []scene.Layer{{
			ID: "sheet", Opacity: 0.9, Blend: "normal",
			Shapes: []scene.Shape{{
				ID: "contour", FillRule: "nonzero", Transform: geom.Identity,
				Brush: scene.Brush{Kind: "linear",
					From: geom.Point{X: 2, Y: 2}, To: geom.Point{X: 20, Y: 12},
					Stops: []scene.Stop{
						{At: 0, Color: scene.Color{R: 1, G: 0.4, B: 0.1, A: 1}},
						{At: 1, Color: scene.Color{R: 0.1, G: 0.6, B: 1, A: 0.5}},
					}},
				Path: []geom.Op{
					{Kind: "M", Points: []geom.Point{{X: 2, Y: 2}}},
					{Kind: "L", Points: []geom.Point{{X: 22, Y: 3}}},
					{Kind: "Q", Points: []geom.Point{{X: 20, Y: 10}, {X: 12, Y: 14}}},
					{Kind: "Z"},
				},
			}},
		}},
	}
}

// Both backends render one already admitted compiled value while no repository
// exists: opening a data root is not part of producing output bytes.
func TestBackendsProduceFromCompiledSceneWithoutDataRoot(t *testing.T) {
	c, err := validate.Compile(document())
	if err != nil {
		t.Fatalf("compile: %v", err)
	}
	ctx := context.Background()

	pngBackend, err := render.For("png")
	if err != nil {
		t.Fatalf("png backend: %v", err)
	}
	svgBackend, err := render.For("svg")
	if err != nil {
		t.Fatalf("svg backend: %v", err)
	}

	pngOnce, err := pngBackend.Produce(ctx, c)
	if err != nil {
		t.Fatalf("png produce: %v", err)
	}
	pngAgain, err := pngBackend.Produce(ctx, c)
	if err != nil {
		t.Fatalf("png produce again: %v", err)
	}
	if !bytes.Equal(pngOnce, pngAgain) {
		t.Fatal("png output is not deterministic for one compiled scene")
	}
	if len(pngOnce) < 8 || !bytes.Equal(pngOnce[:8], []byte("\x89PNG\r\n\x1a\n")) {
		t.Fatalf("png signature missing: % x", pngOnce[:min(8, len(pngOnce))])
	}
	img, err := png.Decode(bytes.NewReader(pngOnce))
	if err != nil {
		t.Fatalf("decode png: %v", err)
	}
	if b := img.Bounds(); b.Dx() != 24 || b.Dy() != 16 {
		t.Fatalf("png dimensions = %dx%d, want 24x16", b.Dx(), b.Dy())
	}

	svg, err := svgBackend.Produce(ctx, c)
	if err != nil {
		t.Fatalf("svg produce: %v", err)
	}
	for _, want := range [][]byte{[]byte("<svg"), []byte("<linearGradient"), []byte("matrix(")} {
		if !bytes.Contains(svg, want) {
			t.Fatalf("svg missing %q", want)
		}
	}

	// The backend boundary is a pure wrapper; existing raster/vector behaviour
	// and therefore old artifact bytes are unchanged.
	if legacy, err := raster.Render(ctx, c); err != nil || !bytes.Equal(legacy, pngOnce) {
		t.Fatalf("png backend diverged from raster.Render (err=%v)", err)
	}
	if legacy := vector.Export(c); !bytes.Equal(legacy, svg) {
		t.Fatal("svg backend diverged from vector.Export")
	}
}

func TestUnknownFormatIsRejected(t *testing.T) {
	if _, err := render.For("pdf"); err == nil {
		t.Fatal("unknown format accepted")
	}
	if got := render.Formats(); len(got) != 2 || got[0] != "png" || got[1] != "svg" {
		t.Fatalf("formats = %v", got)
	}
}

// A producer can run with no data root at all. The test isolates the working
// directory to an empty temporary directory, so any accidental attempt to
// create or read a snapshot root would be visible as a new filesystem entry.
func TestProducerNeedsNoDataRoot(t *testing.T) {
	original, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	empty := t.TempDir()
	if err := os.Chdir(empty); err != nil {
		t.Fatalf("chdir: %v", err)
	}
	t.Cleanup(func() { _ = os.Chdir(original) })

	p := render.NewProducer(1)
	for _, format := range render.Formats() {
		out, err := p.Produce(context.Background(), document(), format)
		if err != nil {
			t.Fatalf("%s produce: %v", format, err)
		}
		if len(out) == 0 {
			t.Fatalf("%s produced no bytes", format)
		}
	}
	entries, err := os.ReadDir(empty)
	if err != nil {
		t.Fatalf("read temp dir: %v", err)
	}
	if len(entries) != 0 {
		t.Fatalf("producing bytes touched the filesystem: %v", entries)
	}
}

// The render package must not depend on the repository or any persistence
// package; without that separation "render without a data root" would be a
// convention rather than an enforced boundary.
func TestRenderPackageHasNoPersistenceImports(t *testing.T) {
	files, err := filepath.Glob("*.go")
	if err != nil {
		t.Fatalf("glob: %v", err)
	}
	forbidden := []string{"repository", "service", "transport", "bundle", "exposure"}
	for _, file := range files {
		if strings.HasSuffix(file, "_test.go") {
			continue
		}
		f, err := parser.ParseFile(token.NewFileSet(), file, nil, parser.ImportsOnly)
		if err != nil {
			t.Fatalf("parse %s: %v", file, err)
		}
		for _, imp := range f.Imports {
			path := strings.Trim(imp.Path.Value, `"`)
			for _, bad := range forbidden {
				if strings.HasSuffix(path, "/"+bad) {
					t.Fatalf("%s imports %s; render must stay independent of persistence and orchestration", file, path)
				}
			}
		}
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
