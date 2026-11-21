#!/usr/bin/env sh
# Reproduce the whole audit pack from scratch (except the Go toolchain).
# Usage: GO_BIN=/path/to/go sh analysis/run_all.sh
set -eu
cd "$(dirname "$0")/.."
python3 analysis/generate_sources.py
python3 analysis/fetch_production_pngs.py
python3 analysis/tie_analysis.py
python3 analysis/run_study.py
python3 analysis/build_controls.py
python3 analysis/alpha_channel_study.py
python3 analysis/counterexamples.py
python3 analysis/export_pixel_reference.py
python3 analysis/verify_pack.py
