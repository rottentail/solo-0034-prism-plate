// rendercases renders the coordinate-debugging mini plates without a running
// service: it exercises the same validate/raster/vector packages as the HTTP
// path, so the PNG/SVG bytes are identical to a revision render on the server.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"image/png"
	"os"
	"path/filepath"
	"sort"

	"prismplate/internal/raster"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
	"prismplate/internal/vector"
)

// Probe is one canvas-coordinate observation point; RGBA8 is filled after render.
type Probe struct {
	Point struct {
		X float64 `json:"x"`
		Y float64 `json:"y"`
	} `json:"point"`
	Expect string  `json:"expect"`
	RGBA   *[4]int `json:"rgba8,omitempty"`
}

type CaseFile struct {
	ID       string          `json:"id"`
	Title    string          `json:"title"`
	Probes   []Probe         `json:"probes"`
	Document json.RawMessage `json:"document"`
}

type ArtifactInfo struct {
	File   string `json:"file"`
	SHA256 string `json:"sha256"`
	Bytes  int    `json:"bytes"`
}

type RenderedCase struct {
	ID     string       `json:"id"`
	Title  string       `json:"title"`
	Width  int          `json:"width"`
	Height int          `json:"height"`
	Probes []Probe      `json:"probes"`
	PNG    ArtifactInfo `json:"png"`
	SVG    ArtifactInfo `json:"svg"`
}

func hashFile(path string, data []byte) ArtifactInfo {
	sum := sha256.Sum256(data)
	return ArtifactInfo{File: path, SHA256: hex.EncodeToString(sum[:]), Bytes: len(data)}
}

func main() {
	root := os.Args[1]
	srcDir := filepath.Join(root, "sources")
	pngDir := filepath.Join(root, "outputs", "png")
	svgDir := filepath.Join(root, "outputs", "svg")
	if err := os.MkdirAll(pngDir, 0o755); err != nil {
		panic(err)
	}
	if err := os.MkdirAll(svgDir, 0o755); err != nil {
		panic(err)
	}
	entries, err := os.ReadDir(srcDir)
	if err != nil {
		panic(err)
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		if filepath.Ext(e.Name()) == ".json" {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	manifest := struct {
		GeneratedBy string         `json:"generated_by"`
		Cases       []RenderedCase `json:"cases"`
	}{GeneratedBy: "manual/tools/rendercases"}

	for _, name := range names {
		raw, err := os.ReadFile(filepath.Join(srcDir, name))
		if err != nil {
			panic(err)
		}
		var cf CaseFile
		if err := json.Unmarshal(raw, &cf); err != nil {
			panic(fmt.Errorf("%s: %w", name, err))
		}
		var doc scene.Document
		if err := json.Unmarshal(cf.Document, &doc); err != nil {
			panic(fmt.Errorf("%s document: %w", name, err))
		}
		compiled, err := validate.Compile(doc)
		if err != nil {
			panic(fmt.Errorf("%s rejected by admission: %w", name, err))
		}
		pngBytes, err := raster.Render(context.Background(), compiled)
		if err != nil {
			panic(err)
		}
		svgBytes := vector.Export(compiled)

		img, err := png.Decode(bytes.NewReader(pngBytes))
		if err != nil {
			panic(err)
		}
		bounds := img.Bounds()
		for i := range cf.Probes {
			ix := int(cf.Probes[i].Point.X + 0.5)
			iy := int(cf.Probes[i].Point.Y + 0.5)
			if ix < bounds.Min.X || ix >= bounds.Max.X || iy < bounds.Min.Y || iy >= bounds.Max.Y {
				panic(fmt.Errorf("%s probe outside canvas: %v", name, cf.Probes[i].Point))
			}
			r, g, b, a := img.At(ix, iy).RGBA()
			v := [4]int{int(r >> 8), int(g >> 8), int(b >> 8), int(a >> 8)}
			cf.Probes[i].RGBA = &v
		}

		stem := name[:len(name)-len(".json")]
		pngName := stem + ".png"
		svgName := stem + ".svg"
		if err := os.WriteFile(filepath.Join(pngDir, pngName), pngBytes, 0o644); err != nil {
			panic(err)
		}
		if err := os.WriteFile(filepath.Join(svgDir, svgName), svgBytes, 0o644); err != nil {
			panic(err)
		}
		manifest.Cases = append(manifest.Cases, RenderedCase{
			ID: cf.ID, Title: cf.Title, Width: doc.Width, Height: doc.Height,
			Probes: cf.Probes,
			PNG:    hashFile(filepath.Join("outputs/png", pngName), pngBytes),
			SVG:    hashFile(filepath.Join("outputs/svg", svgName), svgBytes),
		})
	}
	out, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		panic(err)
	}
	if err := os.WriteFile(filepath.Join(root, "outputs", "manifest.json"), append(out, '\n'), 0o644); err != nil {
		panic(err)
	}
	fmt.Printf("rendered %d cases\n", len(manifest.Cases))
}
