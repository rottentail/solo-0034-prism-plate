#!/usr/bin/env bash
# One-command recomputation of the whole forensics kit.
# Uses only the existing prismplate HTTP contract; no new production endpoint.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
OUT="$(cd "$HERE/.." && pwd)"

# The project ships no committed binary; build it with the locally installed
# Go toolchain (Go >= 1.24). Override with PRISMPLATE_BIN / PRISMPLATE_GO.
GO_BIN="${PRISMPLATE_GO:-go}"
if ! command -v "$GO_BIN" >/dev/null 2>&1 && [ -x /home/node/tooling/go/bin/go ]; then
  export PATH="/home/node/tooling/go/bin:$PATH"
fi
BIN="$OUT/.prismplate-bin"
(cd "$ROOT" && go build -o "$BIN" ./cmd/prismplate)
export PRISMPLATE_BIN="$BIN"

echo "[1/5] verify candidate equifinality from the transcribed paint model"
python3 -I "$HERE/_probe_check.py" > "$OUT/probe-check.txt"
tail -n 1 "$OUT/probe-check.txt" >/dev/null || true

echo "[2/5] render 128 PNGs through the real HTTP service (16 plates x 8 backgrounds)"
rm -rf "$OUT/.service-state"
python3 -I "$HERE/render_service.py"

echo "[3/5] measure PNGs, verify against model, invert under quantization"
python3 -I "$HERE/analyze.py"

echo "[4/5] build and verify montage grids"
python3 -I "$HERE/montage.py"
python3 -I "$HERE/_verify_montage.py"

echo "[5/5] regenerate the human-readable derivation appendix"
python3 -I "$HERE/build_report.py"

echo "OK - outputs under $OUT"
