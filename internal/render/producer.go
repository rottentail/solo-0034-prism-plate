package render

import (
	"context"
	"errors"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
)

// ErrBusy means no compute slot was free without blocking. It mirrors the
// admission failure the service has always surfaced as HTTP 429.
var ErrBusy = errors.New("exposure capacity reached")

// Producer admits scenes and drives output backends under a fixed concurrency
// bound. It deliberately has no repository dependency: producing bytes never
// opens, locks or reads a data root, so the same compiled scene can be imaged
// before any publication state exists.
type Producer struct {
	slots    chan struct{}
	backends func(string) (Backend, error)
}

// NewProducer builds a producer with the given number of compute slots. Slots
// are shared by every caller using this producer, synchronous or background.
func NewProducer(concurrency int) *Producer {
	return NewProducerWith(concurrency, For)
}

// NewProducerWith allows replacing backend selection, primarily so callers can
// inject controlled timing or alternative encoders without changing admission
// or concurrency rules.
func NewProducerWith(concurrency int, backends func(string) (Backend, error)) *Producer {
	if concurrency < 1 {
		concurrency = 1
	}
	return &Producer{slots: make(chan struct{}, concurrency), backends: backends}
}

// Supports reports whether a backend exists for format. It allocates nothing.
func (p *Producer) Supports(format string) bool {
	_, err := p.backends(format)
	return err == nil
}

// Produce admits the document, then produces complete bytes with the selected
// backend while holding one compute slot.
func (p *Producer) Produce(ctx context.Context, d scene.Document, format string) ([]byte, error) {
	select {
	case p.slots <- struct{}{}:
		defer func() { <-p.slots }()
	case <-ctx.Done():
		return nil, ctx.Err()
	default:
		return nil, ErrBusy
	}
	c, err := validate.Compile(d)
	if err != nil {
		return nil, err
	}
	return p.ProduceCompiled(ctx, c, format)
}

// ProduceCompiled runs one backend over an already admitted document. Both PNG
// and SVG consume the identical compiled value.
func (p *Producer) ProduceCompiled(ctx context.Context, c scene.Compiled, format string) ([]byte, error) {
	backend, err := p.backends(format)
	if err != nil {
		return nil, err
	}
	return backend.Produce(ctx, c)
}
