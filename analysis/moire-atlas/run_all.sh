#!/usr/bin/env bash
# Reproduce the whole moire atlas end to end.
# Requires: Go 1.24+ (or set GO_BIN), Python 3 stdlib, ImageMagick (contact sheet only).
set -euo pipefail
cd "$(dirname "$0")"

GO_BIN="${GO_BIN:-go}"
if ! command -v "$GO_BIN" >/dev/null 2>&1; then
  echo "Go toolchain not found; set GO_BIN to a go 1.24+ binary" >&2
  exit 1
fi

echo "[1/5] build prismplate (existing renderer, no new render code)"
mkdir -p /tmp/moire-work/bin
"$GO_BIN" build -o /tmp/moire-work/bin/prismplate ../../cmd/prismplate

echo "[2/5] generate scene source JSON (closed M/L/Z polygons only)"
python3 make_scenes.py

echo "[3/5] render PNG+SVG through the real HTTP service and verify digests"
python3 render_atlas.py

echo "[4/5] independent references + spectral measurement"
python3 analyze_atlas.py

echo "[5/5] reconcile predictions vs measured PNG stripes"
python3 reconcile.py

if command -v montage >/dev/null 2>&1; then
  python3 - <<'PY'
import json, subprocess
cases=[c['id'] for c in json.load(open('cases.json'))['cases']]
rows=[]
for cid in cases:
    tiles=[]
    for j,src in enumerate([f'artifacts/{cid}.png',f'reference/{cid}.continuous8x.png',f'reference/{cid}.folded.png']):
        out=f'/tmp/tile_{cid}_{j}.png'
        subprocess.run(['convert',src,'-filter','point','-resize','200%','-bordercolor','#444','-border','2',out],check=True)
        tiles.append(out)
    row=f'/tmp/row_{cid}.png'
    subprocess.run(['montage',*tiles,'-tile','3x1','-geometry','+4+4','-background','black',row],check=True)
    rows.append(row)
subprocess.run(['montage',*rows,'-tile',f'1x{len(rows)}','-geometry','+6+6','-background','#111','results/contact-sheet.png'],check=True)
print('contact sheet regenerated')
PY
else
  echo "ImageMagick not found; skipping contact-sheet.png (data outputs unchanged)"
fi

python3 manifest_checksums.py
echo "DONE. See results/reconciliation.json (all_pass) and README.md"
