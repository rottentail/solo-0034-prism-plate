FROM golang:1.27 AS compile
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -o /prismplate ./cmd/prismplate
FROM scratch
COPY --from=compile /prismplate /prismplate
USER 65532:65532
EXPOSE 7846
ENTRYPOINT ["/prismplate", "-listen", "0.0.0.0:7846", "-data", "/data/prism-plate-v1"]
