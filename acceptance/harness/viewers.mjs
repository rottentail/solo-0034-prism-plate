#!/usr/bin/env node
/**
 * Render every captured SVG through each available viewer twice:
 *   once over the page's default white surface, and once over a solid
 *   black/green background, plus one capture with the page background removed
 *   (transparent) so that the renderer's own alpha is observable.
 *
 * The matrix exists because the hardest acceptance question is "did the
 * renderer respect the document's own transparent backdrop, or did it let the
 * page backdrop into the blend?"  Rendering on two very different page colours
 * answers that without trusting any single screenshot.
 *
 * Usage:
 *   node acceptance/harness/viewers.mjs --out acceptance/observations/render
 *
 * Viewers that cannot run in this environment are recorded as unavailable with
 * the exact missing library or launch error; they are never silently skipped.
 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "../..");
const SVGS = path.join(ROOT, "acceptance/artifacts");

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i === -1 ? fallback : process.argv[i + 1];
}
const OUT = path.resolve(ROOT, arg("--out", "acceptance/observations/render"));
fs.mkdirSync(OUT, { recursive: true });

const scenes = fs.readdirSync(SVGS).filter((f) => f.endsWith(".svg")).sort();
const BACKGROUNDS = ["white", "black", "green"];
const PAGE_BG = { white: "#ffffff", black: "#000000", green: "#00ff00" };

function write(sub, name, data) {
  const dir = path.join(OUT, sub);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, name), data);
}

async function tryViewer(name, version, fn) {
  try {
    return { name, version: version(), dir: name, ok: true, ...(await fn()) };
  } catch (e) {
    return { name, version: version(), dir: name, ok: false, error: String(e?.stack || e).slice(0, 4000) };
  }
}

/* ---------------------------------------------------------------- librsvg */
const librsvg = () => tryViewer("librsvg", () => {
  const s = require("sharp");
  return s.versions.rsvg + " (sharp " + s.versions.vips + " / " + s.versions.cairo + ")";
}, async () => {
  const sharp = require("sharp");
  for (const f of scenes) {
    const id = f.replace(/\.svg$/, "");
    for (const bg of BACKGROUNDS) {
      const png = await sharp(path.join(SVGS, f), { density: 72 })
        .flatten({ background: PAGE_BG[bg] }).png().toBuffer();
      write("librsvg", `${id}.${bg}.png`, png);
    }
    const alpha = await sharp(path.join(SVGS, f), { density: 72 })
      .ensureAlpha().png().toBuffer();
    write("librsvg", `${id}.alpha.png`, alpha);
  }
  return { backgrounds: BACKGROUNDS, alpha: true };
});

/* ------------------------------------------------------------------ resvg */
const resvg = () => tryViewer("resvg", () => {
  const pkg = require("@resvg/resvg-js/package.json");
  const native = require("@resvg/resvg-js");
  return pkg.version + " (native " + (native.version || "?") + ")";
}, async () => {
  const { Resvg } = require("@resvg/resvg-js");
  const jsVersion = require("@resvg/resvg-js/package.json").version;
  for (const f of scenes) {
    const id = f.replace(/\.svg$/, "");
    const svg = fs.readFileSync(path.join(SVGS, f), "utf8");
    const m = svg.match(/width="(\d+)" height="(\d+)"/);
    const width = Number(m[1]);
    const base = { fitTo: { mode: "width", value: width }, background: "rgba(0,0,0,0)" };
    for (const bg of BACKGROUNDS) {
      const r = new Resvg(svg, { ...base, background: PAGE_BG[bg] });
      write("resvg", `${id}.${bg}.png`, r.render().asPng());
    }
    const r = new Resvg(svg, base);
    write("resvg", `${id}.alpha.png`, r.render().asPng());
  }
  return { js: jsVersion, backgrounds: BACKGROUNDS, alpha: true };
});

/* ----------------------------------------------------------------- cairo */
const cairosvg = () => tryViewer("cairosvg", () => {
  const out = execFileSync("python3", ["-c", "import cairosvg; print(cairosvg.__version__)"], { encoding: "utf8", env: PYENV });
  return "python cairosvg " + out.trim();
}, async () => {
  fs.mkdirSync(path.join(OUT, "cairosvg"), { recursive: true });
  for (const f of scenes) {
    const id = f.replace(/\.svg$/, "");
    for (const bg of BACKGROUNDS) {
      execFileSync("python3", [path.join(HERE, "cairosvg_render.py"), "--svg", path.join(SVGS, f),
        "--out", path.join(OUT, "cairosvg", `${id}.${bg}.png`), "--background", PAGE_BG[bg]], { env: PYENV });
    }
    execFileSync("python3", [path.join(HERE, "cairosvg_render.py"), "--svg", path.join(SVGS, f),
      "--out", path.join(OUT, "cairosvg", `${id}.alpha.png`)], { env: PYENV });
  }
  return { backgrounds: BACKGROUNDS, alpha: true };
});

/* ---------------------------------------------------------- ImageMagick */
const imagemagick = () => tryViewer("imagemagick", () => {
  return execFileSync("convert", ["-version"], { encoding: "utf8" }).split("\n")[0];
}, async () => {
  // ImageMagick's internal MSVG renderer ignores mix-blend-mode and cross-fades
  // stop colours before stop opacity in several builds.  It is kept as a
  // negative control ("this viewer is demonstrably not adequate"), not as a
  // candidate acceptance viewer.
  fs.mkdirSync(path.join(OUT, "imagemagick"), { recursive: true });
  for (const f of scenes) {
    const id = f.replace(/\.svg$/, "");
    for (const bg of BACKGROUNDS) {
      execFileSync("convert", [path.join(SVGS, f), "-background", PAGE_BG[bg],
        "-flatten", path.join(OUT, "imagemagick", `${id}.${bg}.png`)], { stdio: "ignore" });
    }
    execFileSync("convert", [path.join(SVGS, f), "-background", "none",
      path.join(OUT, "imagemagick", `${id}.alpha.png`)], { stdio: "ignore" });
  }
  return { backgrounds: BACKGROUNDS, alpha: true };
});

/* ------------------------------------------------------------- browsers */
const BROWSER_LIB_PATH = process.env.PRISM_BROWSER_LIB_PATH || "";

/** Pull the actual library names out of Playwright's box-drawing error. */
function missingLibs(message) {
  const out = [];
  for (const line of String(message).split("\n")) {
    const m = line.replace(/[^\x20-\x7e]/g, "").trim().match(/^([A-Za-z0-9_.+-]+\.so[.0-9]*)$/);
    if (m) out.push(m[1]);
  }
  return [...new Set(out)];
}

async function browserViewer(kind) {
  const wrapper = {
    chromium: { mod: "chromium", label: "chromium", args: ["--force-color-profile=srgb", "--disable-gpu", "--disable-lcd-text"] },
    firefox: { mod: "firefox", label: "firefox", args: [] },
    webkit: { mod: "webkit", label: "webkit", args: [] },
  }[kind];
  let playwright;
  try {
    playwright = require("playwright");
  } catch (e) {
    return { name: wrapper.label, version: "playwright missing", dir: wrapper.label, ok: false, error: String(e) };
  }
  const version = (() => { try { return "playwright " + require("playwright/package.json").version; } catch { return "?"; } })();
  let browser;
  try {
    const launch = { args: wrapper.args };
    if (process.env.PRISM_BROWSER_EXECUTABLE) launch.executablePath = process.env.PRISM_BROWSER_EXECUTABLE;
    browser = await playwright[wrapper.mod].launch(launch);
  } catch (e) {
    return { name: wrapper.label, version, dir: wrapper.label, ok: false,
             error: String(e?.message || e).slice(0, 4000),
             missing_libraries: missingLibs(e?.message || "") };
  }
  try {
    let alphaOk = true;
    let alphaError = "";
    const b64Cache = new Map();
    for (const f of scenes) {
      const id = f.replace(/\.svg$/, "");
      const svg = fs.readFileSync(path.join(SVGS, f), "utf8");
      const m = svg.match(/width="(\d+)" height="(\d+)"/);
      const width = Number(m[1]);
      const height = Number(m[2]);
      const b64 = Buffer.from(svg).toString("base64");
      b64Cache.set(id, b64);
      // Two embedding modes.  Inline markup inherits the page's blend backdrop
      // in Chromium; an <img> (and a standalone SVG document) is its own
      // compositing root.  Both are legitimate ways a recipient may look at the
      // file, so both are captured and kept apart in the directory name.
      for (const bg of BACKGROUNDS) {
        const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
        await page.setContent(`<!doctype html><style>html,body{margin:0;padding:0;background:${PAGE_BG[bg]}}svg{display:block}</style>${svg}`);
        await page.screenshot({ path: path.join(OUT, wrapper.label, `${id}.${bg}.png`) });
        await page.close();
        const img = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
        await img.setContent(`<!doctype html><style>html,body{margin:0;padding:0;background:${PAGE_BG[bg]}}img{display:block;width:${width}px;height:${height}px}</style><img src="data:image/svg+xml;base64,${b64}">`);
        await img.waitForTimeout(80);
        await img.screenshot({ path: path.join(OUT, wrapper.label, `${id}.img.${bg}.png`) });
        await img.close();
      }
      // Third mode: open the SVG file itself.  A standalone SVG document has
      // the viewer's default page behind it, so the page colour is whatever the
      // browser picks, not something we can set in CSS.  Captured twice: once
      // opaque to see what the user sees, once with omitBackground to recover
      // the document's own alpha.
      {
        const doc = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
        await doc.goto("data:image/svg+xml;base64," + b64);
        await doc.waitForTimeout(120);
        await doc.screenshot({ path: path.join(OUT, wrapper.label, `${id}.doc.png`) });
        await doc.close();
        if (alphaOk) {
          const docA = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
          await docA.goto("data:image/svg+xml;base64," + b64);
          await docA.waitForTimeout(120);
          try {
            await docA.screenshot({ path: path.join(OUT, wrapper.label, `${id}.doc.alpha.png`), omitBackground: true });
          } catch (e) {
            alphaOk = false;
            alphaError = String(e?.message || e).split("\n")[0];
          }
          await docA.close();
        }
      }
      if (alphaOk) {
        const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
        await page.setContent(`<!doctype html><style>html,body{margin:0;padding:0;background:transparent}svg{display:block}</style>${svg}`);
        try {
          await page.screenshot({ path: path.join(OUT, wrapper.label, `${id}.alpha.png`), omitBackground: true });
        } catch (e) {
          // Firefox on Linux does not implement omitBackground.  Solid-page
          // captures remain usable; the missing alpha channel is recorded.
          alphaOk = false;
          alphaError = String(e?.message || e).split("\n")[0];
        }
        await page.close();
      }
    }
    return { name: wrapper.label, version, dir: wrapper.label, ok: true, backgrounds: BACKGROUNDS,
             embed_modes: ["inline", "img", "doc"], alpha: alphaOk, alpha_error: alphaError };
  } catch (e) {
    // A browser that launches but cannot screenshot is still "unavailable" for
    // acceptance; record why instead of dropping it.
    return { name: wrapper.label, version, dir: wrapper.label, ok: false,
             error: "launched, but capture failed: " + String(e?.message || e).slice(0, 2000) };
  } finally {
    try { await browser.close(); } catch { /* process already gone */ }
  }
}

import { execFileSync } from "node:child_process";
const PYENV = { ...process.env, PYTHONPATH: process.env.PRISM_PYTHONPATH || process.env.PYTHONPATH || "" };

const viewers = [librsvg, resvg, cairosvg, imagemagick,
  () => browserViewer("chromium"), () => browserViewer("firefox"), () => browserViewer("webkit")];

const report = [];
for (const v of viewers) {
  process.stderr.write(`viewer: ${v.name}\n`);
  report.push(await v());
}
const summary = {
  generated_at: new Date().toISOString(),
  node: process.version,
  backgrounds: { page: PAGE_BG, alpha: "omitBackground / transparent canvas" },
  viewers: report,
};
fs.writeFileSync(path.join(OUT, "viewers.json"), JSON.stringify(summary, null, 2) + "\n");
for (const v of report) {
  console.log(`${v.ok ? "OK  " : "FAIL"} ${v.name.padEnd(12)} ${v.ok ? (v.version || "") : (v.error || "").split("\n")[0].slice(0, 120)}`);
}
