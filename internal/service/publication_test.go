package service

import (
	"bytes"
	"context"
	"errors"
	"image/png"
	"sync"
	"testing"
	"time"

	"prismplate/internal/geom"
	"prismplate/internal/render"
	"prismplate/internal/repository"
	"prismplate/internal/scene"
	"prismplate/internal/validate"
)

// scripted backend lets a test decide exactly when, and with which bytes, an
// output backend finishes. It is still invoked through the real producer,
// slots and publication path; only the timing of a real backend's bytes is
// replayed.
type scripted struct {
	format  string
	produce func(context.Context) ([]byte, error)
}

func (s scripted) Format() string { return s.format }
func (s scripted) Produce(ctx context.Context, _ scene.Compiled) ([]byte, error) {
	return s.produce(ctx)
}

func testDocument() scene.Document {
	return scene.Document{
		Width: 16, Height: 12,
		Background: scene.Color{A: 1},
		Layers: []scene.Layer{{
			ID: "layer", Opacity: 1, Blend: "normal",
			Shapes: []scene.Shape{{
				ID: "shape", FillRule: "nonzero", Transform: geom.Identity,
				Brush: scene.Brush{Kind: "solid", Color: scene.Color{R: 0.5, G: 0.2, B: 0.1, A: 1}},
				Path: []geom.Op{
					{Kind: "M", Points: []geom.Point{{X: 1, Y: 1}}},
					{Kind: "L", Points: []geom.Point{{X: 14, Y: 2}}},
					{Kind: "L", Points: []geom.Point{{X: 8, Y: 10}}},
					{Kind: "Z"},
				},
			}},
		}},
	}
}

func newTestService(t *testing.T) (*Service, *repository.Repo) {
	t.Helper()
	repo, err := repository.Open(t.TempDir())
	if err != nil {
		t.Fatalf("open repo: %v", err)
	}
	svc := New(repo)
	t.Cleanup(func() { svc.Close(); _ = repo.Close() })
	if _, err := svc.Create("plate", "测试版面", testDocument()); err != nil {
		t.Fatalf("create plate: %v", err)
	}
	return svc, repo
}

// realBytes produces the genuine bytes for a format with the production
// backend. Boundary tests replay these real bytes instead of synthetic strings.
func realBytes(t *testing.T, format string) []byte {
	t.Helper()
	backend, err := render.For(format)
	if err != nil {
		t.Fatalf("backend %s: %v", format, err)
	}
	c, err := validate.Compile(testDocument())
	if err != nil {
		t.Fatalf("compile: %v", err)
	}
	data, err := backend.Produce(context.Background(), c)
	if err != nil {
		t.Fatalf("produce %s: %v", format, err)
	}
	if len(data) == 0 {
		t.Fatalf("%s backend produced no bytes", format)
	}
	return data
}

func outputFormats() []string { return render.Formats() }

// useBackend replaces backend selection for one test while leaving the real
// producer, slots and publication pipeline intact.
func useBackend(svc *Service, format string, produce func(context.Context) ([]byte, error)) {
	svc.Producer = render.NewProducerWith(2, func(name string) (render.Backend, error) {
		if name == format {
			return scripted{format: format, produce: produce}, nil
		}
		return render.For(name)
	})
}

// JobView is the terminal fact observed by the caller.
type JobView struct {
	State    string
	Artifact string
	Error    string
}

func waitJob(t *testing.T, svc *Service, id string) JobView {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for {
		job, err := svc.Pool.Get(id)
		if err != nil {
			t.Fatalf("get job: %v", err)
		}
		switch job.State {
		case "succeeded", "failed", "cancelled":
			return JobView{State: job.State, Artifact: job.Artifact, Error: job.Error}
		}
		if time.Now().After(deadline) {
			t.Fatalf("job %s stuck in %s", id, job.State)
		}
		time.Sleep(2 * time.Millisecond)
	}
}

func artifacts(repo *repository.Repo) map[string]repository.Artifact {
	return repo.View().Artifacts
}

// Boundary 1: the backend has not produced anything. A synchronous failure
// publishes nothing; a background job cancelled while its backend is still
// running ends cancelled with no artifact. Both real output formats are
// exercised.
func TestFailureBeforeProductionPublishesNothing(t *testing.T) {
	for _, format := range outputFormats() {
		t.Run(format+"/sync backend error", func(t *testing.T) {
			svc, repo := newTestService(t)
			wantErr := errors.New("backend unavailable")
			useBackend(svc, format, func(context.Context) ([]byte, error) { return nil, wantErr })

			if _, err := svc.Render(context.Background(), "plate", 1, format); !errors.Is(err, wantErr) {
				t.Fatalf("Render error = %v, want %v", err, wantErr)
			}
			if got := len(artifacts(repo)); got != 0 {
				t.Fatalf("artifacts after failed compute = %d, want 0", got)
			}
		})

		t.Run(format+"/async cancelled before production", func(t *testing.T) {
			svc, repo := newTestService(t)
			started := make(chan struct{})
			useBackend(svc, format, func(ctx context.Context) ([]byte, error) {
				close(started)
				<-ctx.Done()
				return nil, ctx.Err()
			})
			job, err := svc.Submit("plate", 1, format)
			if err != nil {
				t.Fatalf("submit: %v", err)
			}
			<-started
			if _, err := svc.Pool.Cancel(job.ID); err != nil {
				t.Fatalf("cancel: %v", err)
			}
			terminal := waitJob(t, svc, job.ID)
			if terminal.State != "cancelled" {
				t.Fatalf("state = %s, want cancelled", terminal.State)
			}
			if got := len(artifacts(repo)); got != 0 {
				t.Fatalf("artifacts after cancelled compute = %d, want 0", got)
			}
		})

		t.Run(format+"/async backend failure", func(t *testing.T) {
			svc, repo := newTestService(t)
			useBackend(svc, format, func(context.Context) ([]byte, error) {
				return nil, errors.New("backend unavailable")
			})
			job, err := svc.Submit("plate", 1, format)
			if err != nil {
				t.Fatalf("submit: %v", err)
			}
			terminal := waitJob(t, svc, job.ID)
			if terminal.State != "failed" || terminal.Artifact != "" {
				t.Fatalf("terminal = %+v, want failed without artifact", terminal)
			}
			if got := len(artifacts(repo)); got != 0 {
				t.Fatalf("artifacts after failed job = %d, want 0", got)
			}
		})
	}
}

// A job cancelled while still queued never runs its backend and publishes
// nothing. Two blocking jobs occupy the workers so the third is deterministically
// still queued when the cancel request arrives; it is only marked cancelled
// when a worker dequeues it, which stays the process-scoped fact.
func TestQueuedCancelPublishesNothing(t *testing.T) {
	svc, repo := newTestService(t)
	want := realBytes(t, "png")
	started := make(chan struct{}, 2)
	block := make(chan struct{})
	var mu sync.Mutex
	runs := 0
	useBackend(svc, "png", func(ctx context.Context) ([]byte, error) {
		mu.Lock()
		runs++
		mu.Unlock()
		started <- struct{}{}
		select {
		case <-block:
			return want, nil
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	})
	var running []string
	for i := 0; i < 2; i++ {
		job, err := svc.Submit("plate", 1, "png")
		if err != nil {
			t.Fatalf("submit running %d: %v", i, err)
		}
		running = append(running, job.ID)
	}
	<-started
	<-started
	queued, err := svc.Submit("plate", 1, "png")
	if err != nil {
		t.Fatalf("submit queued: %v", err)
	}
	if view, _ := svc.Pool.Get(queued.ID); view.State != "queued" {
		t.Fatalf("third job state = %s, want queued", view.State)
	}
	if _, err := svc.Pool.Cancel(queued.ID); err != nil {
		t.Fatalf("cancel queued: %v", err)
	}
	close(block)
	if got := waitJob(t, svc, queued.ID); got.State != "cancelled" {
		t.Fatalf("queued job state = %s, want cancelled", got.State)
	}
	for _, id := range running {
		if got := waitJob(t, svc, id); got.State != "succeeded" {
			t.Fatalf("running job = %s", got.State)
		}
	}
	mu.Lock()
	defer mu.Unlock()
	if runs != 2 {
		t.Fatalf("backend ran %d times, want 2; the cancelled queued job must not produce", runs)
	}
	if n := len(artifacts(repo)); n != 1 {
		t.Fatalf("artifacts = %d, want the one shared identity of the two successful jobs", n)
	}
}

// Boundary 2: real complete bytes exist but nothing has been committed. The
// caller must not observe an artifact yet; releasing the boundary commits
// exactly those bytes. Cancelling in this window aborts publication.
func TestProducedButUncommittedIsInvisible(t *testing.T) {
	for _, format := range outputFormats() {
		want := realBytes(t, format)

		// holdBeforeCommit pauses the real publication seam before the durable
		// update runs. Output bytes are already complete at this point.
		holdBeforeCommit := func(svc *Service) (entered chan struct{}, release chan struct{}) {
			entered = make(chan struct{})
			release = make(chan struct{})
			inner := svc.commit
			var once sync.Once
			svc.commit = func(ctx context.Context, fn func(*repository.State) error) error {
				once.Do(func() { close(entered) })
				<-release
				return inner(ctx, fn)
			}
			return entered, release
		}

		t.Run(format+"/release then commit", func(t *testing.T) {
			svc, repo := newTestService(t)
			entered, release := holdBeforeCommit(svc)
			job, err := svc.Submit("plate", 1, format)
			if err != nil {
				t.Fatalf("submit: %v", err)
			}
			<-entered
			if n := len(artifacts(repo)); n != 0 {
				t.Fatalf("artifact visible before commit: %d", n)
			}
			if view, _ := svc.Pool.Get(job.ID); view.State != "running" {
				t.Fatalf("state before commit = %s, want running", view.State)
			}
			close(release)
			terminal := waitJob(t, svc, job.ID)
			if terminal.State != "succeeded" {
				t.Fatalf("state = %s, want succeeded (%s)", terminal.State, terminal.Error)
			}
			a, ok := artifacts(repo)[terminal.Artifact]
			if !ok {
				t.Fatalf("artifact %s missing after commit", terminal.Artifact)
			}
			// The real backend ran; the committed bytes are its real output.
			if !bytes.Equal(a.Data, want) || a.Digest != repository.Hash(want) {
				t.Fatal("committed artifact does not match produced bytes")
			}
			assertRealOutput(t, format, a.Data)
		})

		t.Run(format+"/cancel before commit", func(t *testing.T) {
			svc, repo := newTestService(t)
			entered, release := holdBeforeCommit(svc)
			job, err := svc.Submit("plate", 1, format)
			if err != nil {
				t.Fatalf("submit: %v", err)
			}
			<-entered
			if _, err := svc.Pool.Cancel(job.ID); err != nil {
				t.Fatalf("cancel: %v", err)
			}
			close(release)
			terminal := waitJob(t, svc, job.ID)
			if terminal.State != "cancelled" {
				t.Fatalf("state = %s, want cancelled", terminal.State)
			}
			if n := len(artifacts(repo)); n != 0 {
				t.Fatalf("artifacts after cancel before commit = %d, want 0", n)
			}
		})

		t.Run(format+"/sync cancellation before commit", func(t *testing.T) {
			svc, repo := newTestService(t)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			entered := make(chan struct{})
			inner := svc.commit
			var once sync.Once
			svc.commit = func(c context.Context, fn func(*repository.State) error) error {
				once.Do(func() { close(entered); cancel() })
				return inner(c, fn)
			}
			_, err := svc.Render(ctx, "plate", 1, format)
			<-entered
			if !errors.Is(err, context.Canceled) {
				t.Fatalf("Render error = %v, want context.Canceled", err)
			}
			if n := len(artifacts(repo)); n != 0 {
				t.Fatalf("artifacts after sync cancel before commit = %d, want 0", n)
			}
		})
	}
}

// Boundary 3: the artifact has crossed the commit point and the caller cancels
// immediately afterwards. The durable success is authoritative: the job
// reports succeeded and the real bytes remain readable; cancellation does not
// rewrite a committed fact.
func TestCommittedThenCancelledStaysSucceeded(t *testing.T) {
	for _, format := range outputFormats() {
		t.Run(format, func(t *testing.T) {
			svc, repo := newTestService(t)
			want := realBytes(t, format)
			useBackend(svc, format, func(context.Context) ([]byte, error) { return want, nil })

			committed := make(chan struct{})
			release := make(chan struct{})
			inner := svc.commit
			svc.commit = func(ctx context.Context, fn func(*repository.State) error) error {
				err := inner(ctx, fn)
				if err == nil {
					close(committed)
					<-release
				}
				return err
			}
			job, err := svc.Submit("plate", 1, format)
			if err != nil {
				t.Fatalf("submit: %v", err)
			}
			<-committed
			if _, err := svc.Pool.Cancel(job.ID); err != nil {
				t.Fatalf("cancel: %v", err)
			}
			close(release)
			terminal := waitJob(t, svc, job.ID)
			if terminal.State != "succeeded" {
				t.Fatalf("state = %s, want succeeded", terminal.State)
			}
			a, ok := artifacts(repo)[terminal.Artifact]
			if !ok || !bytes.Equal(a.Data, want) {
				t.Fatalf("committed artifact lost after late cancel: %+v", a)
			}
			assertRealOutput(t, format, a.Data)
			if state, _ := svc.Pool.Get(job.ID); state.State != "succeeded" {
				t.Fatalf("cancellation rewrote committed success to %s", state.State)
			}
		})
	}
}

func assertRealOutput(t *testing.T, format string, data []byte) {
	t.Helper()
	switch format {
	case "png":
		if len(data) < 8 || !bytes.Equal(data[:8], []byte("\x89PNG\r\n\x1a\n")) {
			t.Fatal("committed bytes are not a PNG")
		}
		img, err := png.Decode(bytes.NewReader(data))
		if err != nil {
			t.Fatalf("decode committed png: %v", err)
		}
		if b := img.Bounds(); b.Dx() != 16 || b.Dy() != 12 {
			t.Fatalf("committed png = %dx%d, want 16x12", b.Dx(), b.Dy())
		}
	case "svg":
		if !bytes.Contains(data, []byte("<svg")) || !bytes.HasSuffix(bytes.TrimSpace(data), []byte("</svg>")) {
			t.Fatal("committed bytes are not an SVG document")
		}
	default:
		t.Fatalf("unexpected format %q", format)
	}
}
