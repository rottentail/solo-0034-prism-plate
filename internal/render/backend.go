// Package render turns an already compiled scene into output bytes.
//
// Backends are pure computation: they never open the repository, allocate
// artifact identity or commit publication state. The same compiled document
// can therefore be handed to PNG and SVG without a data root.
package render

import (
	"context"
	"errors"
	"prismplate/internal/raster"
	"prismplate/internal/scene"
	"prismplate/internal/vector"
	"sort"
)

// ErrUnknownFormat is returned when no backend exists for a format name.
var ErrUnknownFormat = errors.New("format must be png or svg")

// Backend produces the complete bytes for one output format.
type Backend interface {
	Format() string
	Produce(context.Context, scene.Compiled) ([]byte, error)
}

// PNG is the canonical supersampled raster backend.
type PNG struct{}

func (PNG) Format() string { return "png" }
func (PNG) Produce(ctx context.Context, c scene.Compiled) ([]byte, error) {
	return raster.Render(ctx, c)
}

// SVG is the vector backend. It shares the flattened geometry with PNG but does
// not promise pixel equality in a browser.
type SVG struct{}

func (SVG) Format() string { return "svg" }
func (SVG) Produce(_ context.Context, c scene.Compiled) ([]byte, error) {
	return vector.Export(c), nil
}

var registry = map[string]Backend{"png": PNG{}, "svg": SVG{}}

// For returns the backend registered for format.
func For(format string) (Backend, error) {
	b, ok := registry[format]
	if !ok {
		return nil, ErrUnknownFormat
	}
	return b, nil
}

// Formats lists the supported output formats in stable order.
func Formats() []string {
	out := make([]string, 0, len(registry))
	for name := range registry {
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}
