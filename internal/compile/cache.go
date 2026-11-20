package compile

import (
	"sync"

	"prismplate/internal/geom"
	"prismplate/internal/scene"
)

// Cache bounds compiled drafts by both count and expanded size. Drafts are
// immutable values keyed by document digest, so concurrent PNG and SVG proofs of
// the same revision share one expansion without sharing mutable state.
//
// Eviction is least-recently-used until both the draft count and the expanded
// point budget hold. A draft larger than the whole budget is still compiled for
// its caller but never retained, so one adversarial scene cannot pin the cache.
type Cache struct {
	mu        sync.Mutex
	capacity  int
	byteLimit int64
	bytes     int64
	order     []Key
	entries   map[Key]*entry
	stats     Stats
}

type entry struct {
	draft scene.Compiled
	size  int64
	ready chan struct{}
	err   error
}

// Stats is a cumulative view of cache use. Hits count reads that reused a
// resident expansion; Compiles counts actual expansions. These are process
// diagnostics and never become persisted or exported facts.
type Stats struct {
	Capacity  int    `json:"capacity"`
	ByteLimit int64  `json:"byte_limit"`
	Bytes     int64  `json:"bytes"`
	Resident  int    `json:"resident"`
	Hits      uint64 `json:"hits"`
	Misses    uint64 `json:"misses"`
	Compiles  uint64 `json:"compiles"`
	Evictions uint64 `json:"evictions"`
	Algorithm string `json:"algorithm"`
}

// NewCache returns a cache holding at most capacity drafts and byteLimit expanded
// bytes. A non-positive capacity or budget yields a cache that never stores
// anything; compiles still run.
func NewCache(capacity int, byteLimit int64) *Cache {
	return &Cache{capacity: capacity, byteLimit: byteLimit, entries: map[Key]*entry{}}
}

// Get returns the resident draft for key. On a miss the draft is compiled once;
// concurrent misses for the same key wait for that compile instead of expanding
// the same geometry twice. A failed compile is never cached, so a later retry
// runs admission and expansion again.
//
// The returned draft is shared, not copied: it is an immutable value, and every
// backend reads it without writing. Callers that need to retain or hand out the
// value themselves must use scene.Compiled.Clone.
func (c *Cache) Get(k Key, build func() (scene.Compiled, error)) (scene.Compiled, error) {
	if c.capacity <= 0 || c.byteLimit <= 0 {
		return build()
	}
	c.mu.Lock()
	e, resident := c.entries[k]
	if resident {
		c.stats.Hits++
		c.touch(k)
		c.mu.Unlock()
		<-e.ready
		if e.err != nil {
			return scene.Compiled{}, e.err
		}
		return e.draft, nil
	}
	c.stats.Misses++
	e = &entry{ready: make(chan struct{})}
	c.entries[k] = e
	c.order = append(c.order, k)
	c.evict()
	c.mu.Unlock()

	draft, err := build()
	size := DraftSize(draft)
	c.mu.Lock()
	c.stats.Compiles++
	e.draft, e.err = draft, err
	if err != nil {
		// A failed expansion is not worth reusing; drop the placeholder and do
		// not count toward the byte budget.
		if c.entries[k] == e {
			delete(c.entries, k)
			c.dropOrder(k)
		}
		close(e.ready)
		c.mu.Unlock()
		return scene.Compiled{}, err
	}
	// An entry evicted while compiling must not reappear; this build still
	// serves its own caller. A draft larger than the budget is likewise dropped.
	if c.entries[k] == e {
		e.size = size
		c.bytes += size
		c.touch(k)
	}
	close(e.ready)
	c.mu.Unlock()
	return draft, nil
}

// touch refreshes recency and enforces both bounds. Called with mu held.
func (c *Cache) touch(k Key) {
	c.dropOrder(k)
	c.order = append(c.order, k)
	c.evict()
}

// dropOrder removes a key from the recency list. Called with mu held.
func (c *Cache) dropOrder(k Key) {
	for i, v := range c.order {
		if v == k {
			c.order = append(c.order[:i], c.order[i+1:]...)
			return
		}
	}
}

// evict removes least-recently-used drafts until both bounds hold. Called with mu
// held. Entries still compiling can be evicted; their builder detects this and
// does not publish into the map again.
func (c *Cache) evict() {
	for len(c.order) > 0 && (len(c.entries) > c.capacity || c.bytes > c.byteLimit) {
		oldest := c.order[0]
		c.order = c.order[1:]
		if e, ok := c.entries[oldest]; ok {
			delete(c.entries, oldest)
			c.bytes -= e.size
			c.stats.Evictions++
		}
	}
}

// DraftSize estimates the retained bytes of an expanded draft. Points dominate
// the representation; the fixed per-ring overhead is ignored, which can only
// understate the bound slightly and never overstates reuse.
func DraftSize(d scene.Compiled) int64 {
	var points int64
	count := func(p []geom.Contour) {
		for _, ring := range p {
			points += int64(len(ring))
		}
	}
	for _, l := range d.Layers {
		count(l.Clip)
		count(l.Mask)
		for _, s := range l.Shapes {
			count(s.Path)
		}
	}
	return points * 16
}

// Len reports resident drafts, including any still compiling.
func (c *Cache) Len() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.entries)
}

// Stats snapshots counters for diagnostics.
func (c *Cache) Stats() Stats {
	c.mu.Lock()
	defer c.mu.Unlock()
	s := c.stats
	s.Capacity = c.capacity
	s.ByteLimit = c.byteLimit
	s.Bytes = c.bytes
	s.Resident = len(c.entries)
	s.Algorithm = Algorithm
	return s
}
