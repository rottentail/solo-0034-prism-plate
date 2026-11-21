#!/usr/bin/env bash
# Rebuild every piece of acceptance evidence from the committed scenes.
#
#   acceptance/harness/run.sh
#
# Requirements, each detected and reported rather than assumed:
#   GO            Go 1.24+ on PATH (or set GO=/path/to/go)
#   node + Playwright chromium/firefox + resvg-js + sharp (see package.json)
#   python3 + Pillow + cairosvg
#   ImageMagick `convert` (negative control)
#
# Anything missing is skipped with a reason; viewers.mjs records it in
# observations/render/viewers.json, environment.py records it again, and the
# guide lists it as untested.
set -u
cd "$(dirname "$0")/../.." || exit 1
GO="${GO:-go}"

# The viewer harness needs its node modules.  Install them on first run; the
# pin in package.json is what the recorded results were produced with.
if [ ! -d acceptance/harness/node_modules/playwright ] && command -v npm >/dev/null 2>&1; then
  ( cd acceptance/harness && npm install --no-audit --no-fund ) || \
    echo "npm install failed; browser and library viewers will be recorded as unavailable"
fi

step() { printf '\n== %s ==\n' "$1"; }

step "1/7 generate scenes"
python3 acceptance/harness/scenes.py || exit 1

step "2/7 capture real service output (PNG + SVG bytes, digests, ETags)"
GO="$GO" python3 acceptance/harness/capture.py || exit 1

step "3/7 verify Prism against two independent models"
python3 acceptance/harness/reference.py --space linear || echo "REFERENCE MISMATCH: do not trust the PNG as oracle"
python3 acceptance/harness/reference.py --space srgb || true

step "4/7 render every available viewer in every viewing mode"
node acceptance/harness/viewers.mjs || echo "viewer capture reported failures; they are recorded"

step "5/7 format capability experiment (color-interpolation) and blend gap sweep"
node acceptance/harness/format_experiment.mjs || echo "format experiment unavailable"
python3 acceptance/harness/blend_sweep.py || echo "blend sweep unavailable"

step "6/7 measure captures against Prism"
python3 acceptance/harness/measure.py || exit 1

step "7/7 derive per-mechanism verdicts, gates, tables and environment"
python3 acceptance/harness/evaluate.py || exit 1
python3 acceptance/harness/report.py > /dev/null || exit 1
GO="$GO" python3 acceptance/harness/environment.py || exit 1
