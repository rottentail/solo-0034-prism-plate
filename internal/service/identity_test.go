package service

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"image/png"
	"sync"
	"testing"

	"prismplate/internal/repository"
)

// Equivalent output bytes for the same revision do not replace the artifact a
// proof already references. Each distinct byte delivery is its own artifact;
// the accepted proof keeps pointing at the bytes that were actually submitted,
// and re-rendering an equivalent picture does not silently reactivate old
// revoked output.
func TestProofPinsSubmittedBytesNotEquivalentReencoding(t *testing.T) {
	for _, tc := range []struct {
		format  string
		variant func([]byte) []byte
	}{
		{format: "png", variant: reencodePNG},
		{format: "svg", variant: commentSVG},
	} {
		t.Run(tc.format, func(t *testing.T) {
			svc, repo := newTestService(t)
			original := realBytes(t, tc.format)
			alternative := tc.variant(original)
			if len(alternative) == 0 || bytes.Equal(alternative, original) {
				t.Fatal("variant did not change the output bytes")
			}
			// Both deliveries are valid, equivalent pictures.
			assertRealOutput(t, tc.format, original)
			assertRealOutput(t, tc.format, alternative)

			var mu sync.Mutex
			delivery := 0
			useBackend(svc, tc.format, func(context.Context) ([]byte, error) {
				mu.Lock()
				defer mu.Unlock()
				delivery++
				if delivery == 1 {
					return original, nil
				}
				return alternative, nil
			})

			first, err := svc.Render(context.Background(), "plate", 1, tc.format)
			if err != nil {
				t.Fatalf("first render: %v", err)
			}
			if _, err := svc.OpenProof("signed", first.ID); err != nil {
				t.Fatalf("open proof: %v", err)
			}
			if _, err := svc.Decide("signed", "accepted", "本轮实际提交"); err != nil {
				t.Fatalf("accept proof: %v", err)
			}

			second, err := svc.Render(context.Background(), "plate", 1, tc.format)
			if err != nil {
				t.Fatalf("second render: %v", err)
			}
			if second.ID == first.ID {
				t.Fatal("different output bytes reused the old artifact identity")
			}
			if !bytes.Equal(second.Data, alternative) || second.Digest != repository.Hash(alternative) {
				t.Fatal("second artifact is not the bytes actually delivered")
			}
			if !bytes.Equal(first.Data, original) || first.Digest != repository.Hash(original) {
				t.Fatal("first artifact bytes changed to the equivalent re-encoding")
			}
			got, ok := artifacts(repo)[first.ID]
			if !ok || !bytes.Equal(got.Data, original) {
				t.Fatal("persisted old artifact was replaced by equivalent output")
			}
			proof := repo.View().Proofs["signed"]
			if proof.Artifact != first.ID || proof.State != "accepted" {
				t.Fatalf("proof now references %s (%s), want original %s", proof.Artifact, proof.State, first.ID)
			}
			// The accepted proof protects the bytes it signed, and only those.
			if err := svc.Revoke(first.ID); !errors.Is(err, repository.ErrState) {
				t.Fatalf("revoke signed artifact = %v, want ErrState", err)
			}
			if err := svc.Revoke(second.ID); err != nil {
				t.Fatalf("revoke unsigned artifact: %v", err)
			}
		})
	}
}

// Synchronous and background paths are one publication pipeline: identical
// complete bytes for one captured revision yield one artifact identity, and the
// job terminal fact points at that artifact.
func TestSyncAndAsyncSharePublicationIdentity(t *testing.T) {
	for _, format := range outputFormats() {
		t.Run(format, func(t *testing.T) {
			svc, repo := newTestService(t)
			bytesOut := realBytes(t, format)
			useBackend(svc, format, func(context.Context) ([]byte, error) { return bytesOut, nil })

			syncArtifact, err := svc.Render(context.Background(), "plate", 1, format)
			if err != nil {
				t.Fatalf("render: %v", err)
			}
			job, err := svc.Submit("plate", 1, format)
			if err != nil {
				t.Fatalf("submit: %v", err)
			}
			terminal := waitJob(t, svc, job.ID)
			if terminal.State != "succeeded" {
				t.Fatalf("job = %s (%s)", terminal.State, terminal.Error)
			}
			if terminal.Artifact != syncArtifact.ID {
				t.Fatalf("async artifact %s != sync artifact %s", terminal.Artifact, syncArtifact.ID)
			}
			if n := len(artifacts(repo)); n != 1 {
				t.Fatalf("shared publication produced %d artifacts, want 1", n)
			}
		})
	}
}

// The shared publication path still refuses to reactivate revoked bytes; a
// fresh, different delivery of the same revision is a new artifact instead.
func TestPublicationNeverReactivatesRevokedBytes(t *testing.T) {
	for _, format := range outputFormats() {
		t.Run(format, func(t *testing.T) {
			svc, repo := newTestService(t)
			original := realBytes(t, format)
			useBackend(svc, format, func(context.Context) ([]byte, error) { return original, nil })
			first, err := svc.Render(context.Background(), "plate", 1, format)
			if err != nil {
				t.Fatalf("render: %v", err)
			}
			if err := svc.Revoke(first.ID); err != nil {
				t.Fatalf("revoke: %v", err)
			}
			if _, err := svc.Render(context.Background(), "plate", 1, format); !errors.Is(err, repository.ErrState) {
				t.Fatalf("re-publishing revoked bytes = %v, want ErrState", err)
			}
			if got := repo.View().Artifacts[first.ID]; got.State != "revoked" {
				t.Fatalf("revoked artifact state = %s", got.State)
			}
			if n := len(repo.View().Artifacts); n != 1 {
				t.Fatalf("artifacts = %d, want 1", n)
			}
		})
	}
}

// A background job cancelled after its bytes were committed reports success;
// the published artifact remains available under the identity the job returns.
func TestLateCancelLeavesReadableArtifact(t *testing.T) {
	for _, format := range outputFormats() {
		t.Run(format, func(t *testing.T) {
			svc, _ := newTestService(t)
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
			_, _ = svc.Pool.Cancel(job.ID)
			close(release)
			terminal := waitJob(t, svc, job.ID)
			if terminal.State != "succeeded" {
				t.Fatalf("state = %s", terminal.State)
			}
			a, err := svc.Artifact(terminal.Artifact)
			if err != nil {
				t.Fatalf("read published artifact: %v", err)
			}
			if !bytes.Equal(a.Data, want) {
				t.Fatal("readable artifact is not the committed bytes")
			}
		})
	}
}

func reencodePNG(original []byte) []byte {
	img, err := png.Decode(bytes.NewReader(original))
	if err != nil {
		panic(fmt.Sprintf("decode png: %v", err))
	}
	var out bytes.Buffer
	enc := png.Encoder{CompressionLevel: png.BestSpeed}
	if err := enc.Encode(&out, img); err != nil {
		panic(err)
	}
	return out.Bytes()
}

func commentSVG(original []byte) []byte {
	const prefix = "<svg"
	i := bytes.Index(original, []byte(prefix))
	if i < 0 {
		panic("svg prefix missing")
	}
	out := append([]byte(nil), original[:i+len(prefix)]...)
	out = append(out, []byte(`><!--equivalent-->`)...)
	return append(out, original[i+len(prefix):]...)
}
