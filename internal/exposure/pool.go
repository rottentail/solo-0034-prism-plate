// Package exposure runs bounded, cancellable, process-scoped exposures.
package exposure

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"
)

var ErrBusy = errors.New("exposure capacity reached")
var ErrMissing = errors.New("exposure not found")

type Job struct {
	ID       string    `json:"id"`
	State    string    `json:"state"`
	Artifact string    `json:"artifact,omitempty"`
	Error    string    `json:"error,omitempty"`
	Created  time.Time `json:"created"`
}
type item struct {
	job    Job
	ctx    context.Context
	cancel context.CancelFunc
	work   func(context.Context) (string, error)
}
type Pool struct {
	mu    sync.Mutex
	jobs  map[string]*item
	queue chan *item
	wg    sync.WaitGroup
	state string
	seq   uint64
}

func New(workers, capacity int) *Pool {
	p := &Pool{jobs: map[string]*item{}, queue: make(chan *item, capacity), state: "open"}
	for i := 0; i < workers; i++ {
		p.wg.Add(1)
		go p.worker()
	}
	return p
}
func (p *Pool) Submit(work func(context.Context) (string, error)) (Job, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.state != "open" {
		return Job{}, ErrBusy
	}
	if len(p.jobs) >= 256 {
		return Job{}, ErrBusy
	}
	p.seq++
	id := fmt.Sprintf("exposure-%d", p.seq)
	ctx, cancel := context.WithCancel(context.Background())
	it := &item{job: Job{ID: id, State: "queued", Created: time.Now().UTC()}, ctx: ctx, cancel: cancel, work: work}
	select {
	case p.queue <- it:
		p.jobs[id] = it
		return it.job, nil
	default:
		cancel()
		return Job{}, ErrBusy
	}
}
func (p *Pool) worker() {
	defer p.wg.Done()
	for it := range p.queue {
		p.mu.Lock()
		if it.ctx.Err() != nil {
			it.job.State = "cancelled"
			p.mu.Unlock()
			continue
		}
		it.job.State = "running"
		p.mu.Unlock()
		id, err := it.work(it.ctx)
		p.mu.Lock()
		if err != nil {
			if it.ctx.Err() != nil {
				it.job.State = "cancelled"
			} else {
				it.job.State = "failed"
				it.job.Error = err.Error()
			}
		} else {
			it.job.State = "succeeded"
			it.job.Artifact = id
		}
		it.cancel()
		p.mu.Unlock()
	}
}
func (p *Pool) Get(id string) (Job, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	it, ok := p.jobs[id]
	if !ok {
		return Job{}, ErrMissing
	}
	return it.job, nil
}

// Cancellation is a request. A render committed concurrently may still succeed;
// succeeded is the authoritative publication fact and never becomes cancelled.
func (p *Pool) Cancel(id string) (Job, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	it, ok := p.jobs[id]
	if !ok {
		return Job{}, ErrMissing
	}
	if it.job.State == "queued" || it.job.State == "running" {
		it.cancel()
	}
	return it.job, nil
}
func (p *Pool) Close() {
	p.mu.Lock()
	if p.state != "open" {
		p.mu.Unlock()
		p.wg.Wait()
		return
	}
	p.state = "draining"
	for _, it := range p.jobs {
		it.cancel()
	}
	close(p.queue)
	p.mu.Unlock()
	p.wg.Wait()
	p.mu.Lock()
	p.state = "closed"
	p.mu.Unlock()
}
