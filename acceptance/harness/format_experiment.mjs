#!/usr/bin/env node
/**
 * Causal experiment: which parts of the SVG format can express linear light?
 *
 * Prism composites in linear light.  The SVG string carries
 * color-interpolation="linearRGB" on the root.  Two different mechanisms could
 * respond to it, and the recommendation depends on telling them apart:
 *
 *   gradient stop interpolation   (does the red->green midpoint move?)
 *   layer compositing / blending  (does multiply/screen move?)
 *
 * We render the shipped SVG and two controlled variants, then measure.  The
 * result is committed to acceptance/observations/format_experiment.json.
 *
 *   node acceptance/harness/format_experiment.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "../..");
const ART = path.join(ROOT, "acceptance/artifacts");
const OUT = path.join(ROOT, "acceptance/observations/format_experiment");
fs.mkdirSync(OUT, { recursive: true });

const cases = {
  // scene, probe, page
  gradient: { file: "d-gradient-opaque.svg", probe: [18, 32], w: 64, h: 64 },
  multiply: { file: "b-backdrop-blend.svg", probe: [32, 22], w: 64, h: 64 },
  screen: { file: "b-backdrop-blend.svg", probe: [32, 55], w: 64, h: 64 },
};

function variants(svg) {
  return {
    shipped: svg,
    root_sRGB: svg.replace('color-interpolation="linearRGB"', 'color-interpolation="sRGB"'),
    root_removed: svg.replace(' color-interpolation="linearRGB"', ''),
    // A gradient-local override is the specified way to control stop
    // interpolation; compositing has no equivalent property.
    gradient_linear: svg.replace("<linearGradient ", '<linearGradient color-interpolation="linearRGB" ')
                          .replace('color-interpolation="linearRGB"', ""),
    gradient_sRGB: svg.replace("<linearGradient ", '<linearGradient color-interpolation="sRGB" ')
                        .replace('color-interpolation="linearRGB"', ""),
  };
}

async function run(browserName) {
  let playwright;
  try { playwright = require("playwright"); } catch { return null; }
  let browser;
  try {
    browser = await playwright[browserName].launch(
      browserName === "chromium"
        ? { args: ["--force-color-profile=srgb", "--disable-gpu", "--disable-lcd-text"] }
        : {});
  } catch (e) {
    return { error: String(e?.message || e).slice(0, 500) };
  }
  const result = {};
  try {
    for (const [name, c] of Object.entries(cases)) {
      const svg = fs.readFileSync(path.join(ART, c.file), "utf8");
      result[name] = {};
      for (const [vlabel, v] of Object.entries(variants(svg))) {
        const page = await browser.newPage({ viewport: { width: c.w, height: c.h }, deviceScaleFactor: 1 });
        await page.setContent(`<style>html,body{margin:0;padding:0;background:#ffffff}svg{display:block}</style>${v}`);
        const file = path.join(OUT, `${browserName}.${name}.${vlabel}.png`);
        await page.screenshot({ path: file });
        await page.close();
        result[name][vlabel] = file.replace(ROOT + "/", "");
      }
    }
  } finally {
    try { await browser.close(); } catch { /* gone */ }
  }
  return result;
}

const out = { generated_at: new Date().toISOString(), browsers: {}, files: {} };
for (const b of ["chromium", "firefox"]) {
  const r = await run(b);
  if (r) out.browsers[b] = r.error ? { available: false, error: r.error } : { available: true };
  if (r && !r.error) out.files[b] = r;
}
fs.writeFileSync(path.join(ROOT, "acceptance/observations/format_experiment.json"),
  JSON.stringify(out, null, 2) + "\n");
console.log(JSON.stringify({ out: "acceptance/observations/format_experiment.json",
  browsers: Object.fromEntries(Object.entries(out.browsers).map(([k, v]) => [k, v.available])) }));
