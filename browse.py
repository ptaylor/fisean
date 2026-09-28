#!/usr/bin/env python3
"""fisean browse — the browser half of fisean.

Reads an index produced by the indexer and presents it as a library of stills.
Standard library only, one file: the CLI, the HTTP server and the whole front end
live here, so there is nothing to install and no build step.

    python3 browse.py                          # the committed fixture index
    python3 browse.py --index ~/Library/.../videos/mylibrary
    python3 browse.py --index ./index --port 9000 --no-open

What it does and does not do, from docs/index-format.md: it reads the index and
nothing else, except that it will serve the original bytes of a video file,
read-only, so the browser's own decoder can play it. It never runs ffmpeg and
never decodes anything itself.

    /            the interface
    /index/...   the index directory, read-only (records and stills)
    /media/...   the resolved media root, read-only, with range requests

Quit with Ctrl-C, or the ✕ in the interface.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

# Index versions this browser understands. The page is told this set at build
# time, so the check in the interface cannot drift from the server.
SUPPORTED_INDEX_VERSIONS = {1}

FIRST_PORT = 8765
RUNS_ON = "127.0.0.1"

PAGE_CSS = """
:root {
  --bg: #0c0e10;
  --panel: #14181b;
  --panel-2: #191e22;
  --line: #262d32;
  --line-soft: #1e2429;
  --ink: #e9e6e0;
  --dim: #98a1a7;
  --dimmer: #6d767c;
  --accent: #c9a464;
  --accent-soft: rgba(201, 164, 100, 0.14);
  --teal: #6fa8a4;
  --radius: 10px;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

/* ---------------------------------------------------------------- header */
header {
  position: sticky; top: 0; z-index: 30;
  display: flex; align-items: center; gap: 14px;
  padding: 12px 18px;
  background: rgba(12, 14, 16, 0.86);
  backdrop-filter: saturate(140%) blur(10px);
  border-bottom: 1px solid var(--line);
}
h1 {
  margin: 0; font-size: 17px; font-weight: 600; letter-spacing: 0.01em;
  white-space: nowrap;
}
h1 span { color: var(--accent); }
.count { color: var(--dim); font-size: 13px; white-space: nowrap; }
input[type=search], select {
  background: var(--panel); color: var(--ink);
  border: 1px solid var(--line); border-radius: 8px;
  padding: 7px 10px; font: inherit; font-size: 14px;
}
input[type=search] { flex: 1; min-width: 120px; }
input[type=search]:focus, select:focus { outline: none; border-color: var(--accent); }
.spacer { flex: 1; }
button {
  background: var(--panel); color: var(--ink);
  border: 1px solid var(--line); border-radius: 8px;
  padding: 7px 12px; font: inherit; font-size: 14px; cursor: pointer;
}
button:hover { background: var(--panel-2); border-color: var(--dimmer); }
button.primary { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); }
button:disabled { opacity: 0.4; cursor: default; }

/* ------------------------------------------------------------------ shell */
main { display: flex; align-items: flex-start; }
#rail {
  width: 268px; flex: 0 0 268px; padding: 16px 8px 40px 18px;
  position: sticky; top: 53px; max-height: calc(100vh - 53px); overflow-y: auto;
}
#rail.hidden { display: none; }
.facet { margin-bottom: 20px; }
.facet h2 {
  margin: 0 0 8px; font-size: 11px; font-weight: 600; color: var(--dimmer);
  text-transform: uppercase; letter-spacing: 0.09em;
}
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  margin: 0 5px 5px 0; padding: 4px 9px;
  background: var(--panel); border: 1px solid var(--line-soft);
  border-radius: 999px; font-size: 12.5px; color: var(--dim); cursor: pointer;
}
.chip:hover { border-color: var(--dimmer); color: var(--ink); }
.chip[aria-pressed=true] {
  background: var(--accent-soft); border-color: var(--accent); color: var(--accent);
}
.chip.empty { opacity: 0.32; cursor: default; }
.chip .n { font-size: 11px; color: var(--dimmer); }
.chip[aria-pressed=true] .n { color: var(--accent); }
.chip.weak::after {
  content: "~"; color: var(--dimmer); font-size: 11px;
}
.clear {
  background: none; border: none; color: var(--dim); font-size: 12.5px;
  padding: 0; text-decoration: underline; text-underline-offset: 2px;
}

/* ------------------------------------------------------------------- grid */
#content { flex: 1; min-width: 0; padding: 16px 18px 60px; }
#grid {
  display: grid; gap: 16px;
  grid-template-columns: repeat(auto-fill, minmax(216px, 1fr));
}
.card {
  background: var(--panel); border: 1px solid var(--line-soft);
  border-radius: var(--radius); overflow: hidden; cursor: pointer;
  transition: border-color 120ms ease, transform 120ms ease;
}
.card:hover { border-color: var(--dimmer); transform: translateY(-1px); }
.card[data-selected=true] { border-color: var(--accent); }
.thumb {
  position: relative; aspect-ratio: 16 / 9; background: #0a0c0e;
  display: flex; align-items: center; justify-content: center;
}
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.thumb .none {
  color: var(--dimmer); font-size: 12.5px; text-align: center; padding: 12px;
}
.thumb .badges { position: absolute; top: 8px; left: 8px; display: flex; gap: 6px; }
.thumb .still-count {
  position: absolute; bottom: 8px; right: 8px;
  background: rgba(8, 10, 12, 0.72); border-radius: 6px;
  padding: 2px 6px; font-size: 11.5px; color: var(--ink);
}
/* The video's other stills, so the list shows what was extracted without
   opening each one. The cover is already above, so the strip holds the rest
   rather than repeating it — and clicking one opens the drawer on that still. */
.strip { display: flex; gap: 4px; padding: 9px 11px 0; align-items: center; }
.strip img {
  width: 48px; height: 32px; object-fit: cover; border-radius: 4px;
  opacity: 0.55; border: 1px solid transparent; cursor: pointer;
  transition: opacity 120ms ease, border-color 120ms ease;
}
.strip img:hover { opacity: 1; border-color: var(--dimmer); }
.strip .more { font-size: 11.5px; color: var(--dimmer); }

/* --------------------------------------------------------------- list view */
#grid.layout-list { grid-template-columns: 1fr; gap: 10px; }
.row {
  display: flex; gap: 14px; padding: 12px; align-items: flex-start;
  background: var(--panel); border: 1px solid var(--line-soft);
  border-radius: var(--radius); cursor: pointer;
  transition: border-color 120ms ease;
}
.row:hover { border-color: var(--dimmer); }
.row[data-selected=true] { border-color: var(--accent); }
.row-cover {
  flex: 0 0 208px; width: 208px; height: 117px; border-radius: 8px;
  overflow: hidden; background: #0a0c0e;
  display: flex; align-items: center; justify-content: center;
}
.row-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.row-cover .none { color: var(--dimmer); font-size: 12px; text-align: center; padding: 8px; }
/* A row has the width to show the other stills at a size worth looking at,
   which is the whole reason the second layout exists. */
.row-stills { display: flex; flex-wrap: wrap; gap: 5px; flex: 0 0 218px; width: 218px; }
.row-stills img {
  width: 104px; height: 58px; object-fit: cover; border-radius: 6px;
  opacity: 0.78; border: 1px solid transparent; cursor: pointer;
  transition: opacity 120ms ease, border-color 120ms ease;
}
.row-stills img:hover { opacity: 1; border-color: var(--accent); }
.row-meta { flex: 1; min-width: 0; }
.row-meta .name { font-size: 14.5px; margin-bottom: 4px; }
.row-meta .tags { margin-top: 8px; }
.row-meta .path {
  margin-top: 8px; background: none; border: none; padding: 0;
  color: var(--dimmer); font-size: 11.5px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
@media (max-width: 1100px) {
  .row-stills { flex-basis: 109px; width: 109px; }
  .row-stills img { width: 52px; height: 29px; }
}
@media (max-width: 780px) {
  .row { flex-wrap: wrap; }
  .row-cover { flex-basis: 100%; width: 100%; height: auto; aspect-ratio: 16 / 9; }
  .row-stills { flex-basis: 100%; width: 100%; }
}
.pill {
  background: rgba(8, 10, 12, 0.72); border-radius: 6px;
  padding: 2px 6px; font-size: 11.5px; color: var(--ink);
}
.pill.warn { color: var(--accent); }
.card .body { padding: 10px 11px 12px; }
/* Shared by the card and the row: both show the same metadata line, and scoping
   these to .card is how the row silently fell back to body size. */
.when { display: flex; gap: 8px; align-items: baseline; font-size: 12.5px; color: var(--dim); }
.when .approx { color: var(--accent); }
.name { margin-top: 3px; font-size: 13.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tags { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 4px; }
.tag {
  font-size: 11.5px; color: var(--dim); background: var(--panel-2);
  border: 1px solid var(--line-soft); border-radius: 5px; padding: 1px 6px;
}
.tag.weak { color: var(--dimmer); border-style: dashed; }

/* ----------------------------------------------------------------- drawer */
#drawer {
  position: fixed; top: 0; right: 0; bottom: 0; width: min(560px, 100%);
  background: var(--panel); border-left: 1px solid var(--line);
  z-index: 40; display: flex; flex-direction: column;
  box-shadow: -18px 0 40px rgba(0, 0, 0, 0.45);
}
#drawer[hidden] { display: none; }
#backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.45); z-index: 35; }
#backdrop[hidden] { display: none; }
.drawer-head {
  display: flex; align-items: center; gap: 10px;
  padding: 12px 14px; border-bottom: 1px solid var(--line);
}
.drawer-head .who { flex: 1; min-width: 0; }
.drawer-head .who div:first-child {
  font-size: 14.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.drawer-head .who div:last-child { font-size: 12px; color: var(--dim); }
.drawer-body { overflow-y: auto; padding: 14px 16px 40px; }
.stage { background: #0a0c0e; border-radius: 8px; overflow: hidden; margin-bottom: 8px; }
.stage img, .stage video { width: 100%; display: block; }
.filmstrip { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 4px; }
.filmstrip img {
  height: 48px; border-radius: 5px; cursor: pointer; opacity: 0.6;
  border: 1px solid transparent;
}
.filmstrip img[aria-current=true] { opacity: 1; border-color: var(--accent); }
.actions { display: flex; gap: 8px; margin: 12px 0 4px; flex-wrap: wrap; }
.note { color: var(--dim); font-size: 12.5px; }
h3 {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.09em;
  color: var(--dimmer); margin: 20px 0 8px; font-weight: 600;
}
dl { display: grid; grid-template-columns: 108px 1fr; gap: 5px 12px; margin: 0; font-size: 13px; }
dt { color: var(--dim); }
dd { margin: 0; word-break: break-word; }
.path {
  background: var(--panel-2); border: 1px solid var(--line-soft); border-radius: 6px;
  padding: 7px 9px; font-size: 12px; color: var(--dim); word-break: break-all;
}
ul.labels { list-style: none; margin: 0; padding: 0; }
ul.labels li {
  display: flex; align-items: center; gap: 8px; padding: 4px 0;
  border-bottom: 1px solid var(--line-soft); font-size: 13px;
}
ul.labels li:last-child { border-bottom: none; }
ul.labels .prov { color: var(--dimmer); font-size: 11.5px; margin-left: auto; white-space: nowrap; }
.scores { color: var(--dimmer); font-size: 11.5px; }
summary { cursor: pointer; color: var(--ink); font-size: 13.5px; }
.banner {
  margin: 0 18px 8px; padding: 9px 12px; border-radius: 8px; font-size: 13px;
  background: var(--accent-soft); border: 1px solid var(--accent); color: var(--accent);
}
.banner[hidden] { display: none; }
.empty-state { color: var(--dim); padding: 40px 4px; }
#progress { color: var(--dim); font-size: 12.5px; }
#toast {
  position: fixed; bottom: 18px; left: 50%; transform: translateX(-50%);
  background: var(--panel-2); border: 1px solid var(--accent); color: var(--accent);
  padding: 8px 14px; border-radius: 8px; font-size: 13px; z-index: 50;
}
#toast[hidden] { display: none; }
.fatal { max-width: 620px; margin: 80px auto; padding: 0 20px; }
.fatal h1 { font-size: 20px; margin-bottom: 10px; }
.fatal code { background: var(--panel-2); padding: 2px 5px; border-radius: 4px; }

@media (max-width: 900px) {
  #rail { position: static; width: auto; flex-basis: auto; max-height: none; }
  main { flex-direction: column; }
  #rail { padding: 14px 18px 0; }
}
"""

PAGE_JS = """
'use strict';

// Derived facets. The index stores measurements; these thresholds live here on
// purpose, so re-tuning "dark" or "blurry" never means re-indexing a library
// (docs/index-format.md, rule 5). They are guesses until measured against a real
// library, and any of them can be edited without touching the indexer.
const QUALITY = {
  dark:    a => n(a.mean_luma) !== null && a.mean_luma < 0.28,
  bright:  a => n(a.mean_luma) !== null && a.mean_luma > 0.72,
  soft:    a => n(a.sharpness_p10) !== null && a.sharpness_p10 < 0.15,
  sharp:   a => n(a.sharpness_p10) !== null && a.sharpness_p10 >= 0.30,
  still:   a => (n(a.freeze_ratio) !== null && a.freeze_ratio >= 0.5) || a.shot_count === 1,
  damaged: a => n(a.black_ratio) !== null && a.black_ratio >= 0.2,
};

// Containers and codecs a browser will decode. Everything else gets the
// copy-path action instead of a player, rather than a player that fails
// (docs/index-format.md, section 7).
const PLAYABLE_CONTAINERS = new Set(['mp4', 'mov', 'm4v', 'webm']);
const PLAYABLE_CODECS = new Set(['h264', 'hevc', 'vp8', 'vp9', 'av1']);

const DATE_SOURCE_LABEL = {
  container_metadata: 'from the file metadata',
  filename: 'from the file name',
  file_mtime: 'from the file date — copied files can be wrong',
  user_override: 'corrected by hand',
  unknown: 'not known',
};

// Which index versions this page understands. Substituted from the Python
// constant at build time rather than written twice, so the check cannot drift
// from the server that serves the page. A higher major number is refused with an
// explanation rather than rendered wrongly: the contract allows additive fields
// within a version, but a major bump means a field changed meaning, and guessing
// at that is how a browser silently shows the wrong thing.
const SUPPORTED_INDEX_VERSIONS = new Set(__INDEX_VERSIONS__);

const state = {
  manifest: null,
  config: null,
  assets: [],
  selected: null,
  still: 0,
  filters: new Map(),   // axis -> Set(values)
  query: '',
  sort: 'date-desc',
  layout: 'grid',
  playing: false,
};

/* --------------------------------------------------------------- helpers */

const n = v => (typeof v === 'number' && isFinite(v) ? v : null);

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// Everything from the index is inserted as text, never as markup: a summary or a
// label could contain anything, and this is the whole of the defence.
function duration(seconds) {
  const s = n(seconds);
  if (s === null) return '—';
  const total = Math.round(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  return h ? `${h}h ${m}m` : `${m}:${String(sec).padStart(2, '0')}`;
}

function bytes(value) {
  const b = n(value);
  if (b === null) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0, v = b;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

function when(iso) {
  if (!iso) return 'undated';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

const year = iso => (iso ? String(iso).slice(0, 4) : 'undated');
const fileName = path => String(path || '').split('/').pop();
const container = a => (a.technical && a.technical.container) || '?';
const cover = a => {
  const stills = (a.stills || []).filter(s => s && s.path);
  if (!stills.length) return null;
  // Prefer the still the index marked as the cover. When none is marked, fall
  // back to the highest score rather than to the first entry: the stills are in
  // time order, so "first" would silently mean "opening shot"
  // (docs/index-format.md, section 5).
  const marked = stills.find(s => s.cover === true);
  if (marked) return marked;
  return stills.reduce((best, s) => (s.score || 0) > (best.score || 0) ? s : best, stills[0]);
};
const stillURL = rel => `/index/${rel.split('/').map(encodeURIComponent).join('/')}`;

const playable = a => {
  const t = a.technical || {};
  if (!t.has_video) return false;
  return PLAYABLE_CONTAINERS.has(String(t.container || '').toLowerCase())
      && PLAYABLE_CODECS.has(String(t.video_codec || '').toLowerCase());
};

const mediaPath = a => {
  const root = state.config && state.config.media_root;
  const rel = a.source && a.source.path;
  return rel ? `${root || state.manifest.media_root}/${rel}` : null;
};

const labelText = a => (a.labels || []).map(l => l.text);

function searchHaystack(a) {
  return [a.id, a.source && a.source.path, container(a),
          a.captured && a.captured.device ? Object.values(a.captured.device).join(' ') : '',
          a.summary ? a.summary.text : '',
          ...labelText(a)].join(' ').toLowerCase();
}

/* ----------------------------------------------------------------- facets */

// The four browse axes, per requirement 8: when, where, who/what, quality.
// "Where" is GPS only: place names would need reverse geocoding, which the
// indexer cannot do offline.
const AXES = [
  { key: 'when', title: 'When',
    values: a => {
      const v = [];
      if (!a.captured || !a.captured.at) v.push(['date unknown', 'date-unknown']);
      else v.push([year(a.captured.at), `y:${year(a.captured.at)}`]);
      if (a.captured && (a.captured.at_source === 'file_mtime' || a.captured.at_source === 'filename')) {
        v.push(['date approximate', 'date-approximate']);
      }
      return v;
    },
    label: v => v.startsWith('y:') ? v.slice(2) : v },
  { key: 'where', title: 'Where',
    values: a => {
      const gps = a.captured && a.captured.gps;
      return gps ? [['has coordinates', 'gps-yes']] : [['no coordinates', 'gps-no']];
    },
    label: v => v === 'gps-yes' ? 'has coordinates' : 'no coordinates' },
  { key: 'who', title: 'Who & what',
    // Value and display are the same string here: the label text is already the
    // thing a reader recognises, and the drawer's label chips filter on it too.
    values: a => (a.labels || []).map(l => [l.text, l.text, !!l.weak]),
    label: v => v },
  { key: 'quality', title: 'Quality',
    values: a => Object.entries(QUALITY).filter(([, test]) => test(a.analysis || {}))
                                         .map(([name]) => [name, name]),
    label: v => v },
  { key: 'format', title: 'Format',
    values: a => [[container(a), container(a)]],
    label: v => v },
];

const axisFor = key => AXES.find(axis => axis.key === key);
const valuesOf = (axis, a) => axis.values(a).map(entry => ({ value: entry[1], label: entry[0], weak: entry[2] }));

function matches(a, ignoreAxis) {
  if (state.query && !searchHaystack(a).includes(state.query)) return false;
  for (const [key, chosen] of state.filters) {
    if (key === ignoreAxis || chosen.size === 0) continue;
    const values = valuesOf(axisFor(key), a).map(entry => entry.value);
    if (!values.some(value => chosen.has(value))) return false;
  }
  return true;
}

/* ------------------------------------------------------------------- sort */

const SORTS = {
  'date-desc': (a, b) => (b.captured?.at || '').localeCompare(a.captured?.at || ''),
  'date-asc': (a, b) => (a.captured?.at || 'zzz').localeCompare(b.captured?.at || 'zzz'),
  'duration': (a, b) => (n(b.technical?.duration_s) || 0) - (n(a.technical?.duration_s) || 0),
  'sharpest': (a, b) => (n(b.analysis?.sharpness_p10) || -1) - (n(a.analysis?.sharpness_p10) || -1),
  'stills': (a, b) => (b.stills?.length || 0) - (a.stills?.length || 0),
  'name': (a, b) => fileName(a.source?.path).localeCompare(fileName(b.source?.path)),
};

const visible = () => state.assets.filter(a => matches(a)).sort(SORTS[state.sort]);

/* ----------------------------------------------------------------- render */

function render() {
  const items = visible();
  const total = state.assets.length;
  document.getElementById('count').textContent =
    items.length === total ? `${total} assets` : `${items.length} of ${total} assets`;

  const rail = document.getElementById('rail');
  rail.replaceChildren(...AXES.map(renderFacet), renderClear());
  renderItems(items);
}

function renderFacet(axis) {
  // Counts are computed against everything that matches the *other* active
  // filters, which is the usual faceted-search rule: a chip must not read 0 just
  // because its own axis is what you are filtering on.
  const counts = new Map();
  const weak = new Set();
  for (const asset of state.assets) {
    for (const entry of valuesOf(axis, asset)) {
      counts.set(entry.value, (counts.get(entry.value) || 0) + 1);
      if (entry.weak) weak.add(entry.value);
    }
  }
  const chosen = state.filters.get(axis.key) || new Set();
  const values = [...counts.entries()].sort((x, y) => axis.key === 'when'
    ? String(y[0]).localeCompare(String(x[0]))
    : y[1] - x[1] || String(x[0]).localeCompare(String(y[0])));

  const chips = values.map(([value, count]) => {
    const pressed = chosen.has(value);
    const chip = el('button', {
      class: `chip${weak.has(value) ? ' weak' : ''}${!pressed && count === 0 ? ' empty' : ''}`,
      'aria-pressed': String(pressed),
      title: weak.has(value) ? 'weak label — evidence is a guess, not a fact' : null,
      onclick: () => { toggle(axis.key, value); },
    }, axis.label(value), el('span', { class: 'n', text: String(count) }));
    return chip;
  });

  const node = el('div', { class: 'facet' }, el('h2', { text: axis.title }));
  node.append(el('div', {}, chips.length ? chips : el('div', { class: 'note', text: 'nothing here' })));
  return node;
}

function renderClear() {
  const active = state.filters.size > 0 || state.query;
  return el('div', {}, el('button', {
    class: 'clear', text: 'clear filters', disabled: active ? null : 'disabled',
    onclick: () => { state.filters.clear(); state.query = ''; document.getElementById('q').value = ''; render(); },
  }));
}

function renderItems(items) {
  const grid = document.getElementById('grid');
  grid.className = state.layout === 'list' ? 'layout-list' : '';
  if (!items.length) {
    grid.replaceChildren(el('div', { class: 'empty-state', text: 'Nothing matches those filters.' }));
    return;
  }
  grid.replaceChildren(...items.map(state.layout === 'list' ? renderRow : renderCard));
}

// One video per row, so the stills can be shown at a size worth looking at. The
// row is the same asset as a card, with room for the path and the device.
function renderRow(asset) {
  const best = cover(asset);
  const bestIndex = best ? asset.stills.indexOf(best) : 0;
  const extras = (asset.stills || []).filter(still => still !== best).slice(0, 6);

  const coverBox = el('div', { class: 'row-cover' });
  if (best) coverBox.append(el('img', { src: stillURL(best.path), alt: '', loading: 'lazy' }));
  else coverBox.append(el('div', { class: 'none', text: asset.technical?.has_video === false
    ? 'no stills — audio only' : 'no stills' }));

  const stills = extras.length
    ? el('div', { class: 'row-stills' }, extras.map(still => el('img', {
        src: stillURL(still.path), alt: '', loading: 'lazy',
        title: `${still.at_s}s — ${still.reason || 'no reason recorded'}`,
        onclick: event => {
          event.stopPropagation();
          openAsset(asset.id, asset.stills.indexOf(still));
        },
      })))
    : el('div', { class: 'row-stills' }, el('span', { class: 'note', text:
        (asset.stills || []).length ? 'one still' : '' }));

  const source = asset.captured?.at_source;
  const approximate = source === 'file_mtime' || source === 'filename';
  const gps = asset.captured?.gps;

  return el('article', {
    class: 'row', 'data-selected': String(state.selected === asset.id),
    onclick: () => openAsset(asset.id, bestIndex),
  },
    coverBox,
    stills,
    el('div', { class: 'row-meta' },
      el('div', { class: 'name', text: fileName(asset.source?.path) }),
      el('div', { class: 'when' },
        el('span', { class: approximate ? 'approx' : '', text: when(asset.captured?.at) }),
        el('span', { text: '·' }),
        el('span', { text: duration(asset.technical?.duration_s) }),
        el('span', { text: '·' }),
        el('span', { text: container(asset) }),
        el('span', { text: '·' }),
        el('span', { text: `${(asset.stills || []).length} still${(asset.stills || []).length === 1 ? '' : 's'}` }),
        asset.technical?.has_video && !playable(asset)
          ? el('span', { class: 'pill', text: 'player?',
                         title: 'a browser cannot decode this format; use the copied path' })
          : null,
        gps ? el('span', { class: 'pill', text: `${gps.lat.toFixed(3)}, ${gps.lon.toFixed(3)}` }) : null),
      el('div', { class: 'tags' }, (asset.labels || [])
        .slice().sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 8)
        .map(l => el('span', { class: `tag${l.weak ? ' weak' : ''}`, text: l.text }))),
      el('div', { class: 'path mono', text: mediaPath(asset) || '' })));
}

function renderCard(asset) {
  const best = cover(asset);
  const thumb = el('div', { class: 'thumb' });
  if (best) {
    thumb.append(el('img', { src: stillURL(best.path), alt: '', loading: 'lazy' }));
  } else {
    thumb.append(el('div', { class: 'none', text: asset.technical?.has_video === false
      ? 'no stills — audio only' : 'no stills could be extracted' }));
  }

  const badges = el('div', { class: 'badges' });
  const source = asset.captured?.at_source;
  if (source === 'file_mtime' || source === 'filename') {
    badges.append(el('span', { class: 'pill warn', text: 'date ~', title: DATE_SOURCE_LABEL[source] }));
  }
  if (source === 'unknown') {
    badges.append(el('span', { class: 'pill warn', text: 'undated' }));
  }
  if (asset.technical?.has_video && !playable(asset)) {
    badges.append(el('span', { class: 'pill', text: 'player?', title: 'a browser cannot decode this format; use the copied path' }));
  }
  thumb.append(badges);
  if (asset.stills?.length > 1) {
    thumb.append(el('span', { class: 'still-count', text: `${asset.stills.length} stills` }));
  }

  const tags = (asset.labels || [])
    .slice()
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .slice(0, 3)
    .map(l => el('span', { class: `tag${l.weak ? ' weak' : ''}`, text: l.text }));

  // The stills beyond the cover, capped so a video with twenty stills cannot
  // stretch the card; the remainder is counted rather than shown. Filtered by
  // identity, not by position: the cover is marked in the index and may be any
  // entry, so slicing from the front would drop a real still and repeat the cover.
  const extras = (asset.stills || []).filter(still => still !== best).slice(0, 4);
  const remaining = (asset.stills || []).length - 1 - extras.length;
  const strip = extras.length
    ? el('div', { class: 'strip' },
        extras.map(still => el('img', {
          src: stillURL(still.path), alt: '', loading: 'lazy',
          title: `${still.at_s}s — ${still.reason || 'no reason recorded'}`,
          onclick: event => {
            event.stopPropagation();
            openAsset(asset.id, asset.stills.indexOf(still));
          },
        })),
        remaining > 0 ? el('span', { class: 'more', text: `+${remaining}` }) : null)
    : null;

  return el('article', {
    class: 'card', 'data-selected': String(state.selected === asset.id),
    onclick: () => openAsset(asset.id),
  },
    thumb,
    el('div', { class: 'body' },
      strip,
      el('div', { class: 'when' },
        el('span', { class: source === 'file_mtime' || source === 'filename' ? 'approx' : '',
                     text: when(asset.captured?.at) }),
        el('span', { text: '·' }),
        el('span', { text: duration(asset.technical?.duration_s) }),
        el('span', { text: '·' }),
        el('span', { text: container(asset) })),
      el('div', { class: 'name', text: fileName(asset.source?.path), title: asset.source?.path || '' }),
      el('div', { class: 'tags' }, tags)),
  );
}

/* ----------------------------------------------------------------- drawer */

function openAsset(id, index = 0) {
  state.selected = id;
  state.still = index;
  state.playing = false;
  renderDrawer();
  document.getElementById('drawer').hidden = false;
  document.getElementById('backdrop').hidden = false;
  render();
}

function closeDrawer() {
  state.selected = null;
  state.playing = false;
  document.getElementById('drawer').hidden = true;
  document.getElementById('backdrop').hidden = true;
  document.getElementById('backdrop').replaceChildren();
  render();
}

function renderDrawer() {
  const asset = state.assets.find(a => a.id === state.selected);
  const drawer = document.getElementById('drawer');
  if (!asset) { closeDrawer(); return; }
  const stills = asset.stills || [];
  const current = stills[Math.min(state.still, stills.length - 1)];
  const path = mediaPath(asset);

  const stage = el('div', { class: 'stage' });
  if (state.playing && playable(asset)) {
    const video = el('video', { controls: '', src: mediaURL(asset), autoplay: '' });
    // Belt and braces: the container and codec may look playable and still fail
    // (a broken file, an odd profile). Fall back rather than show a dead player.
    video.addEventListener('error', () => {
      state.playing = false;
      toast('the browser could not decode this file — copy the path instead');
      renderDrawer();
    });
    stage.append(video);
  } else if (current) {
    stage.append(el('img', { src: stillURL(current.path), alt: '' }));
  } else {
    stage.append(el('div', { class: 'none', style: 'padding:40px;text-align:center;color:var(--dim)',
      text: asset.technical?.has_video === false
        ? 'This file has no video stream, so there are no stills.'
        : 'No stills were extracted for this file.' }));
  }

  const head = el('div', { class: 'drawer-head' },
    el('div', { class: 'who' },
      el('div', { text: fileName(asset.source?.path) }),
      el('div', { text: `${container(asset)}${asset.technical?.video_codec ? ' · ' + asset.technical.video_codec : ''} · ${asset.id}` })),
    el('button', { text: '✕', title: 'close (Esc)', onclick: closeDrawer }));

  const body = el('div', { class: 'drawer-body' });
  body.append(stage);

  if (stills.length > 1) {
    body.append(el('div', { class: 'filmstrip' }, stills.map((still, index) =>
      el('img', {
        src: stillURL(still.path), alt: '', 'aria-current': String(index === state.still),
        title: `${still.at_s}s — ${still.reason || ''}`,
        onclick: () => { state.still = index; state.playing = false; renderDrawer(); },
      }))));
  }

  const actions = el('div', { class: 'actions' });
  actions.append(el('button', { class: 'primary', text: 'Copy full path', onclick: () => copyPath(asset) }));
  if (playable(asset)) {
    actions.append(el('button', {
      text: state.playing ? 'Show still' : 'Play here',
      onclick: () => { state.playing = !state.playing; renderDrawer(); },
    }));
  }
  body.append(actions);
  if (asset.technical?.has_video && !playable(asset)) {
    body.append(el('div', { class: 'note', text:
      `A browser cannot decode ${asset.technical?.video_codec || 'this codec'} in ${container(asset)}. ` +
      'Copy the path and open it in a player — that is what this button is for.' }));
  }
  body.append(el('div', { class: 'path mono', text: path || '(no path recorded)' }));

  body.append(el('h3', { text: 'When' }));
  body.append(el('dl', {},
    el('dt', { text: 'captured' }),
    el('dd', {}, asset.captured?.at ? when(asset.captured.at) : 'unknown',
       el('span', { class: 'scores', text: ` — ${DATE_SOURCE_LABEL[asset.captured?.at_source] || asset.captured?.at_source || 'unknown'}` })),
    el('dt', { text: 'file date' }), el('dd', { text: asset.source?.mtime ? when(asset.source.mtime) : 'not recorded' }),
    el('dt', { text: 'device' }),
    el('dd', { text: asset.captured?.device ? `${asset.captured.device.make || ''} ${asset.captured.device.model || ''}`.trim() : 'unknown' })));

  body.append(el('h3', { text: 'Where' }));
  const gps = asset.captured?.gps;
  body.append(el('dl', {},
    el('dt', { text: 'coordinates' }),
    el('dd', { text: gps ? `${gps.lat.toFixed(4)}, ${gps.lon.toFixed(4)}` : 'none recorded' }),
    el('dt', { text: 'place name' }),
    el('dd', { text: 'not in the index — it needs a dataset or a network call, so it comes from overrides.yaml' })));

  body.append(el('h3', { text: 'Technical' }));
  const t = asset.technical || {};
  body.append(el('dl', {},
    el('dt', { text: 'container' }), el('dd', { text: t.container || '—' }),
    el('dt', { text: 'video' }), el('dd', { text: t.video_codec || (t.has_video === false ? 'none — audio only' : '—') }),
    el('dt', { text: 'audio' }), el('dd', { text: t.audio_codec || 'none' }),
    el('dt', { text: 'size' }), el('dd', { text: `${t.width && t.height ? t.width + '×' + t.height : '—'} · ${n(t.fps) ? t.fps + ' fps' : '—'}` }),
    el('dt', { text: 'duration' }), el('dd', { text: duration(t.duration_s) }),
    el('dt', { text: 'file' }), el('dd', { text: `${bytes(asset.source?.size_bytes)} · ${asset.source?.path || ''}` })));

  body.append(el('h3', { text: 'Measured' }));
  const a = asset.analysis || {};
  body.append(el('dl', {},
    el('dt', { text: 'sampled' }), el('dd', { text: `${a.sampled_frames ?? 0} frames` }),
    el('dt', { text: 'shots' }), el('dd', { text: a.shot_count ?? '—' }),
    el('dt', { text: 'luminance' }), el('dd', { text: n(a.mean_luma) === null ? 'not measured' : a.mean_luma.toFixed(2) }),
    el('dt', { text: 'sharpness' }), el('dd', { text: n(a.sharpness_p10) === null ? 'not measured' : a.sharpness_p10.toFixed(2) }),
    el('dt', { text: 'black' }), el('dd', { text: n(a.black_ratio) === null ? 'not measured' : (a.black_ratio * 100).toFixed(0) + '%' }),
    el('dt', { text: 'freeze' }), el('dd', { text: n(a.freeze_ratio) === null ? 'not measured' : (a.freeze_ratio * 100).toFixed(0) + '%' }),
    el('dt', { text: 'facets' }),
    el('dd', { text: Object.entries(QUALITY).filter(([, test]) => test(a)).map(([name]) => name).join(', ') || 'none' })));

  if (asset.summary?.text) {
    body.append(el('h3', { text: 'Summary' }));
    body.append(el('details', { open: '' },
      el('summary', { text: 'generated description' }),
      el('div', { style: 'margin-top:8px', text: asset.summary.text }),
      el('div', { class: 'scores', style: 'margin-top:6px',
                  text: `source: ${asset.summary.source} · ${asset.summary.model || 'unknown model'}` })));
  }

  body.append(el('h3', { text: `Labels (${(asset.labels || []).length})` }));
  const labels = (asset.labels || []).slice().sort((x, y) => (y.score || 0) - (x.score || 0));
  body.append(labels.length
    ? el('ul', { class: 'labels' }, labels.map(label => {
        const provenance = [label.source, label.model, n(label.score) === null ? null : label.score.toFixed(2),
                            label.frames ? `${label.frames} frames` : null,
                            label.count ? `${label.count}×` : null].filter(Boolean).join(' · ');
        return el('li', {},
          el('button', { class: `chip${label.weak ? ' weak' : ''}`, text: label.text,
            title: label.weak ? 'weak — treat as a hint, not a fact' : 'filter by this label',
            onclick: () => { toggle('who', label.text); closeDrawer(); } }),
          el('span', { class: 'prov', text: provenance }));
      }))
    : el('div', { class: 'note', text: 'No labels were derived for this file.' }));

  if (stills.length) {
    body.append(el('h3', { text: 'Stills' }));
    body.append(el('dl', {}, stills.map((still, index) => [
      el('dt', { text: `${index + 1}. ${still.at_s}s` }),
      el('dd', { text: `${still.cover ? 'cover — ' : ''}${still.reason || 'no reason recorded'}`
                       + `${still.faces ? ` · ${still.faces} face${still.faces > 1 ? 's' : ''}` : ''}` }),
    ]).flat()));
  }

  return drawer.replaceChildren(head, body);
}

const mediaURL = asset => `/media/${String(asset.source?.path || '').split('/').map(encodeURIComponent).join('/')}`;

function toggle(axisKey, value) {
  const chosen = state.filters.get(axisKey) || new Set();
  if (chosen.has(value)) chosen.delete(value); else chosen.add(value);
  if (chosen.size) state.filters.set(axisKey, chosen); else state.filters.delete(axisKey);
  render();
}

/* --------------------------------------------------------------- clipboard */

async function copyPath(asset) {
  const path = mediaPath(asset);
  if (!path) { toast('no path recorded for this file'); return; }
  try {
    await navigator.clipboard.writeText(path);
    toast('copied — paste into Finder with ⌘⇧G, or into a player');
  } catch (error) {
    // http://127.0.0.1 is a secure context, so this should not normally happen.
    // The fallback deliberately does *not* use window.prompt: a modal that blocks
    // the page is a worse failure than a copy that did not happen. Select the
    // path instead, so the obvious next action works.
    const node = document.querySelector('#drawer .path');
    if (!node) { toast('could not copy the path'); return; }
    const range = document.createRange();
    range.selectNodeContents(node);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    toast('could not copy — the path is selected, press ⌘C');
  }
}

let toastTimer = null;
function toast(message) {
  const node = document.getElementById('toast');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 3200);
}

/* ----------------------------------------------------------------- loading */

async function loadConcurrently(records, limit, onProgress) {
  const out = [];
  let next = 0, done = 0;
  const worker = async () => {
    while (next < records.length) {
      const index = next++;
      const response = await fetch(`/index/videos/${encodeURIComponent(records[index])}.json`);
      if (response.ok) out.push(await response.json());
      done++;
      if (done % 5 === 0 || done === records.length) onProgress(done, records.length);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, records.length) }, worker));
  return out;
}

function fatal(title, detail, hint) {
  document.querySelector('main').replaceChildren(
    el('div', { class: 'fatal' },
      el('h1', { text: title }),
      el('p', {}, detail),
      hint ? el('p', {}, el('code', { text: hint })) : null));
  document.getElementById('count').textContent = '';
}

async function start() {
  let manifest, config;
  try {
    const [manifestResponse, configResponse] = await Promise.all([
      fetch('/index/manifest.json', { cache: 'no-store' }),
      fetch('/config.json', { cache: 'no-store' }),
    ]);
    if (!manifestResponse.ok) throw new Error(`manifest.json returned ${manifestResponse.status}`);
    manifest = await manifestResponse.json();
    // The config is the server's, not the index's: it carries the absolute media
    // root, which the index cannot state when it records a relative one.
    config = configResponse.ok ? await configResponse.json() : {};
  } catch (error) {
    fatal('No index to read',
      'The browser could not load manifest.json from the index directory.',
      'python3 browse.py --index /path/to/an/index');
    return;
  }

  if (!SUPPORTED_INDEX_VERSIONS.has(manifest.index_version)) {
    fatal(`Index version ${manifest.index_version} is not supported`,
      `This browser understands version ${[...SUPPORTED_INDEX_VERSIONS].join(', ')}. ` +
      'A major version change means a field changed meaning, so showing this index ' +
      'would risk showing it wrongly.',
      'update the browser half, or re-generate the index with a matching indexer');
    return;
  }

  state.manifest = manifest;
  state.config = config;

  // Asset ids come from the manifest when it lists them and otherwise from the
  // index directory's listing, so an index written by any indexer loads.
  let names = manifest.assets;
  if (!names) {
    const listing = await fetch('/index/videos/', { cache: 'no-store' });
    names = listing.ok ? (await listing.json()).ids : [];
  }

  if (!names.length) {
    fatal('The index has no assets',
      'manifest.json was read, but no asset records were found in videos/.',
      'python3 fixtures/make_fixtures.py');
    return;
  }

  document.getElementById('progress').textContent = `loading ${names.length} records…`;
  state.assets = await loadConcurrently(names, 8, (done, total) => {
    document.getElementById('progress').textContent = `loading ${done}/${total} records…`;
  });
  document.getElementById('progress').textContent = '';

  const banner = document.getElementById('banner');
  if (config.media_root_available === false) {
    // Expected when the library is on an unmounted disk, and not an error: the
    // stills and the copy action both still work.
    banner.hidden = false;
    banner.textContent = `The media directory is not reachable (${config.media_root || 'unknown'}), `
      + 'so playback is off. Stills and copy-path still work.';
  } else if (manifest.errors && manifest.errors.length) {
    banner.hidden = false;
    banner.textContent = `${manifest.errors.length} file${manifest.errors.length > 1 ? 's' : ''} could not be read: `
      + manifest.errors.map(e => `${e.path} (${e.stage}: ${e.message})`).join('; ');
  }

  render();
}

/* ------------------------------------------------------------------ events */

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('q').addEventListener('input', event => {
    state.query = event.target.value.trim().toLowerCase();
    render();
  });
  document.getElementById('sort').addEventListener('change', event => {
    state.sort = event.target.value;
    render();
  });
  document.getElementById('rail-toggle').addEventListener('click', () => {
    document.getElementById('rail').classList.toggle('hidden');
  });
  document.getElementById('layout-toggle').addEventListener('click', () => {
    state.layout = state.layout === 'grid' ? 'list' : 'grid';
    document.getElementById('layout-toggle').textContent =
      state.layout === 'grid' ? 'List view' : 'Grid view';
    render();
  });
  document.getElementById('backdrop').addEventListener('click', closeDrawer);

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') { closeDrawer(); return; }
    if (event.key === '/' && document.activeElement !== document.getElementById('q')) {
      event.preventDefault();
      document.getElementById('q').focus();
      return;
    }
    const inField = ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName);
    if (inField || !state.selected) return;
    const stills = (state.assets.find(a => a.id === state.selected)?.stills || []).length;
    if (event.key === 'ArrowRight' && state.still < stills - 1) { state.still++; state.playing = false; renderDrawer(); }
    if (event.key === 'ArrowLeft' && state.still > 0) { state.still--; state.playing = false; renderDrawer(); }
  });

  start();
});
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>físeán</title>
<style>__CSS__</style>
</head>
<body>
<header>
  <h1>físe<span>án</span></h1>
  <span class="count" id="count"></span>
  <input type="search" id="q" placeholder="Search labels, summary, path…   /" aria-label="Search">
  <select id="sort" aria-label="Sort">
    <option value="date-desc">Newest first</option>
    <option value="date-asc">Oldest first</option>
    <option value="duration">Longest</option>
    <option value="sharpest">Sharpest</option>
    <option value="stills">Most stills</option>
    <option value="name">File name</option>
  </select>
  <button id="rail-toggle" title="Show or hide the filters">Filters</button>
  <button id="layout-toggle" title="Switch between the grid and a list with larger stills">List view</button>
  <span class="spacer"></span>
  <span id="progress"></span>
</header>
<div class="banner" id="banner" hidden></div>
<main>
  <aside id="rail"></aside>
  <section id="content"><div id="grid"></div></section>
</main>
<div id="backdrop" hidden></div>
<aside id="drawer" hidden></aside>
<div id="toast" hidden></div>
<script>__JS__</script>
</body>
</html>
"""


def build_page() -> bytes:
    """The interface: one page, nothing about the index templated into it.

    The page fetches the manifest from `/index/manifest.json`, which is the file
    on disk, unmodified — the browser half must not edit the index's facts on the
    way past. The one thing the index cannot state when `media_root` is relative
    is the absolute media path that belongs on the clipboard, and that comes from
    the separate `/config.json` instead.
    """
    html = PAGE_TEMPLATE.replace("__CSS__", PAGE_CSS).replace("__JS__", PAGE_JS)
    html = html.replace("__INDEX_VERSIONS__", json.dumps(sorted(SUPPORTED_INDEX_VERSIONS)))
    return html.encode("utf-8")


def safe_resolve(root: Path, relative: str) -> Path | None:
    """Resolve a request path inside root, or return None.

    The only defence against a request reaching outside the two directories the
    browser is allowed to read, so it refuses anything that escapes rather than
    trying to sanitise it.
    """
    relative = unquote(relative).lstrip("/")
    if not relative or relative.startswith("../") or "/../" in relative:
        return None
    candidate = (root / relative).resolve()
    root = root.resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def build_handler(index_root: Path, media_root: Path):
    page = None  # built on first request; cheap, but there is no reason to do it at import

    class FiseanHandler(BaseHTTPRequestHandler):
        server_version = "fisean-browse"

        def log_message(self, fmt, *args):  # quieter, and less useful default noise
            if "/index/videos/" in (self.path or "") and args and str(args[1]).startswith("2"):
                return
            sys.stderr.write(f"fisean browse: {self.address_string()} {fmt % args}\n")

        def do_GET(self) -> None:
            self.serve(include_body=True)

        def do_HEAD(self) -> None:
            self.serve(include_body=False)

        def serve(self, include_body: bool) -> None:
            nonlocal page
            path = urlparse(self.path).path

            if path == "/config.json":
                # Where the halves meet: the server knows the absolute media path
                # and whether it is reachable; the index does not have to.
                payload = json.dumps({
                    "index_root": str(index_root),
                    "media_root": str(media_root),
                    "media_root_available": media_root.is_dir(),
                }).encode("utf-8")
                self.send_bytes(payload, "application/json; charset=utf-8", include_body=include_body)
                return

            if path in ("/", "/index.html"):
                if page is None:
                    page = build_page()
                self.send_bytes(page, "text/html; charset=utf-8", include_body=include_body)
                return

            if path.rstrip("/") == "/index/videos":
                directory = index_root / "videos"
                ids = sorted(item.stem for item in directory.glob("*.json")) if directory.is_dir() else []
                self.send_bytes(json.dumps({"ids": ids}).encode("utf-8"),
                                "application/json; charset=utf-8", include_body=include_body)
                return

            if path.startswith("/index/"):
                target = safe_resolve(index_root, path[len("/index/"):])
                if target is None or not target.is_file():
                    self.send_error(404, "not in the index")
                    return
                # Records are served uncached so a re-index shows up on reload;
                # images are immutable in practice and cache badly if not.
                cache = "no-store" if target.suffix == ".json" else "public, max-age=3600"
                self.send_bytes(target.read_bytes(), self.guess_type(target), cache, include_body)
                return

            if path.startswith("/media/"):
                target = safe_resolve(media_root, path[len("/media/"):])
                if target is None or not target.is_file():
                    # Expected whenever the library is on an unmounted disk; the
                    # interface turns this into "copy the path" rather than a
                    # broken player.
                    self.send_error(404, "media file is not reachable")
                    return
                self.send_file_range(target, include_body)
                return

            self.send_error(404, "not found")

        def guess_type(self, target: Path) -> str:
            guessed, _ = mimetypes.guess_type(target.name)
            return guessed or "application/octet-stream"

        def send_bytes(self, payload: bytes, content_type: str, cache: str = "no-store",
                       include_body: bool = True) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            if include_body:
                self.wfile.write(payload)

        def send_file_range(self, target: Path, include_body: bool = True) -> None:
            """Serve a media file, honouring a single range request.

            Range support is why playback can be seeked at all; without it the
            browser downloads from the start every time the slider moves.
            """
            size = target.stat().st_size
            start, end = 0, size - 1
            range_header = self.headers.get("Range")
            status = 200
            if range_header:
                match = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
                if match:
                    first, last = match.group(1), match.group(2)
                    if first:
                        start = int(first)
                        end = int(last) if last else size - 1
                    elif last:  # a suffix range: the last N bytes
                        start = max(0, size - int(last))
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                    end = min(end, size - 1)
                    status = 206

            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", self.guess_type(target))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            if not include_body:
                return
            with target.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    chunk = handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    return FiseanHandler


def create_server(index_root: Path, media_root: Path, port: int) -> ThreadingHTTPServer:
    """Bind the first free port at or above the one asked for."""
    handler = build_handler(index_root, media_root)
    last_error: OSError | None = None
    for candidate in range(port, port + 20):
        try:
            return ThreadingHTTPServer((RUNS_ON, candidate), handler)
        except OSError as error:
            last_error = error
    raise SystemExit(f"no free port in {port}-{port + 19}: {last_error}")


def resolve_media_root(index_root: Path, manifest_path: Path, override: str | None) -> Path:
    """Where the original media lives.

    `media_root` in the manifest is normally absolute, but may be relative — the
    committed fixture uses `../media` so it survives being cloned anywhere — and a
    relative root is resolved against the index directory.
    """
    if override:
        return Path(override).expanduser().resolve()
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except (OSError, ValueError):
        return index_root
    root = manifest.get("media_root")
    if not root:
        return index_root
    candidate = Path(root).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (index_root / candidate).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Browse a fisean index as a library of stills.",
        epilog="The index is read-only; the browser never writes to it, and never "
               "decodes media. See docs/index-format.md for the format it expects.",
    )
    parser.add_argument("--index", default=None,
                        help="index directory (default: the committed fixture, fixtures/index)")
    parser.add_argument("--media-root", default=None,
                        help="override the media root recorded in the manifest")
    parser.add_argument("--port", type=int, default=FIRST_PORT,
                        help=f"first port to try (default: {FIRST_PORT}, then upwards)")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser window")
    args = parser.parse_args()

    index_root = Path(args.index).expanduser().resolve() if args.index \
        else (Path(__file__).resolve().parent / "fixtures" / "index")
    if not (index_root / "manifest.json").is_file():
        sys.stderr.write(
            f"no index at {index_root}\n"
            "Build the fixture one with: python3 fixtures/make_fixtures.py\n"
            "or point at a real index with: --index /path/to/index\n")
        return 1

    media_root = resolve_media_root(index_root, index_root / "manifest.json", args.media_root)

    server = create_server(index_root, media_root, args.port)
    port = server.server_address[1]
    url = f"http://{RUNS_ON}:{port}/"
    print(f"fisean browse: index {index_root}")
    print(f"fisean browse: media {media_root}"
          f"{'' if media_root.is_dir() else '  (not found — stills and paths only)'}")
    print(f"fisean browse: {url}")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nfisean browse: stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
