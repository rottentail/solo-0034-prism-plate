package service

import (
	"context"
	"errors"
	"fmt"
	"prismplate/internal/exposure"
	"prismplate/internal/render"
	"prismplate/internal/repository"
	"strconv"
	"time"
)

// candidate builds the artifact identity for one complete output byte slice.
// Identity binds plate, revision, scene digest, format and the bytes that were
// actually produced, so different output bytes for the same revision are a
// different artifact rather than a replacement for the earlier one.
func (s *Service) candidate(id string, r repository.Revision, format string, data []byte) repository.Artifact {
	digest := repository.Hash(data)
	key := repository.Hash([]byte(id + ":" + strconv.Itoa(r.Number) + ":" + r.Digest + ":" + format + ":" + digest))
	return repository.Artifact{ID: key, Plate: id, Revision: r.Number, SceneDigest: r.Digest, Format: format, Digest: digest, State: "available", Data: data}
}

// publish is the single publication path for synchronous renders and
// background exposures. The candidate has already been computed completely;
// this step alone decides whether it becomes durable. Cancellation observed
// before the commit seam prevents publication; publication that has already
// happened is authoritative and is reported as success even if the caller
// cancels concurrently.
func (s *Service) publish(ctx context.Context, a repository.Artifact) (repository.Artifact, error) {
	err := s.commit(ctx, func(st *repository.State) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if old, ok := st.Artifacts[a.ID]; ok {
			if old.State != "available" {
				return repository.ErrState
			}
			a = old
			return nil
		}
		st.Artifacts[a.ID] = a
		return nil
	})
	if err != nil {
		return a, err
	}
	return a, nil
}

// render is the shared synchronous and asynchronous pipeline: compute complete
// bytes from the captured revision with the format backend, then publish
// through the one commit path.
func (s *Service) render(ctx context.Context, id string, r repository.Revision, format string) (repository.Artifact, error) {
	data, err := s.Producer.Produce(ctx, r.Document, format)
	if errors.Is(err, render.ErrBusy) {
		// Keep the historical admission sentinel so HTTP mapping stays 429.
		err = exposure.ErrBusy
	}
	if err != nil {
		return repository.Artifact{}, err
	}
	return s.publish(ctx, s.candidate(id, r, format, data))
}

func (s *Service) Render(ctx context.Context, id string, revision int, format string) (repository.Artifact, error) {
	r, err := s.capture(id, revision)
	if err != nil {
		return repository.Artifact{}, err
	}
	return s.render(ctx, id, r, format)
}

func (s *Service) Submit(id string, revision int, format string) (exposure.Job, error) {
	r, err := s.capture(id, revision)
	if err != nil {
		return exposure.Job{}, err
	}
	if !s.Producer.Supports(format) {
		return exposure.Job{}, fmt.Errorf("unknown format")
	}
	return s.Pool.Submit(func(ctx context.Context) (string, error) {
		ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		a, e := s.render(ctx, id, r, format)
		return a.ID, e
	})
}
