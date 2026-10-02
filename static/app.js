// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 Paul Taylor

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

// The duration filter is two range inputs over one track, and what they carry is
// a position rather than a time: the scale is exponential because a video library
// is. Measured over a real 538-video library - median 21s, 90th percentile 1m
// 33s, longest 1h 54m - a linear track 6859 seconds wide puts 90% of the videos
// inside the first 1.4% of its width, where one pixel is half a minute and a
// twenty-second clip cannot be selected at all. On this scale a pixel near the
// short end is worth about a second.
const DURATION_STEPS = 1000;

const DATE_SOURCE_LABEL = {
  container_metadata: 'from the file metadata',
  filename: 'from the file name',
  file_mtime: 'from the file date — copied files can be wrong',
  user_override: 'corrected by hand',
  unknown: 'not known',
};

// Which index versions this page understands. The server states this in
// /config.json rather than the page hard-coding it, so the check cannot drift
// from the server that serves the page. A higher major number is refused with an
// explanation rather than rendered wrongly: the contract allows additive fields
// within a version, but a major bump means a field changed meaning, and guessing
// at that is how a browser silently shows the wrong thing.
let SUPPORTED_INDEX_VERSIONS = new Set();

const state = {
  manifest: null,
  config: null,
  assets: [],
  selected: null,
  still: 0,
  filters: new Map(),   // axis -> Set(values)
  // The duration range as slider positions (0 = no lower bound, DURATION_STEPS =
  // no upper bound), and the library's own extent, which is only known once the
  // records have been read.
  duration: { loPos: 0, hiPos: DURATION_STEPS },
  durationExtent: null,
  query: '',
  sort: 'date-desc',
  // The list is the default because it is the one that shows what was extracted:
  // the stills, the duration, the path and the device all at once, which is what
  // this tool is for. The grid is the overview you switch to.
  layout: 'list',
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
  // A record with a playable copy is playable whatever the original was: the
  // scan writes one for the formats a browser refuses, and then this is the only
  // question. Without it the interface would show the copy and offer no player.
  if (a.playback && a.playback.path) return true;
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
  // 'duration' is not an axis - it is a range, and it filters here rather than
  // through state.filters - but it is ignored by the same rule, so that the
  // readout can ask for "everything except the range" the way a chip asks about
  // its own axis.
  if (ignoreAxis !== 'duration' && !durationMatches(a)) return false;
  for (const [key, chosen] of state.filters) {
    if (key === ignoreAxis || chosen.size === 0) continue;
    const values = valuesOf(axisFor(key), a).map(entry => entry.value);
    if (!values.some(value => chosen.has(value))) return false;
  }
  return true;
}

/* --------------------------------------------------------------- duration */

// The shortest and longest video in the library, in whole seconds. Floored to a
// whole second so the ends of the slider read as times, but kept positive: a
// logarithmic scale has no zero, and flooring a sub-second shortest video to zero
// would divide by it while a floor of one second would hide that video instead.
function measureDurationExtent() {
  let shortest = null, longest = null;
  for (const asset of state.assets) {
    const d = n(asset.technical?.duration_s);
    if (d === null) continue;
    shortest = shortest === null ? d : Math.min(shortest, d);
    longest = longest === null ? d : Math.max(longest, d);
  }
  state.durationExtent = shortest === null ? null : {
    lo: shortest >= 1 ? Math.floor(shortest) : shortest,
    hi: Math.max(2, Math.ceil(longest)),
  };
}

// A slider position as seconds. Position 0 is not the shortest video but "no
// lower bound", so at full width the range excludes nothing - including a file
// whose duration was never measured, which no range can contain.
function positionSeconds(position) {
  const { lo, hi } = state.durationExtent;
  if (position <= 0) return 0;
  return lo * Math.pow(hi / lo, (position - 1) / (DURATION_STEPS - 1));
}

// The selected range in whole seconds, widened to the enclosing second so that
// dragging a thumb back to where it started cannot drop a video through
// rounding. It filters only once it excludes something: the foot of the slider
// is where the scale's own low end floors to zero, and without that rule a file
// whose duration was never measured would leave the library for a range that
// contains everything.
function durationRange() {
  if (!state.durationExtent) return { lo: 0, hi: 0, narrowed: false };
  const { loPos, hiPos } = state.duration;
  const lo = Math.floor(positionSeconds(loPos));
  const hi = Math.ceil(positionSeconds(hiPos));
  return { lo, hi, narrowed: lo > 0 || hiPos < DURATION_STEPS };
}

function durationMatches(asset, range = durationRange()) {
  if (!range.narrowed) return true;
  const d = n(asset.technical?.duration_s);
  return d !== null && d >= range.lo && d <= range.hi;
}

function resetDuration() {
  state.duration.loPos = 0;
  state.duration.hiPos = DURATION_STEPS;
}

function renderDuration() {
  const facet = el('div', { class: 'facet' }, el('h2', { text: 'How long' }));
  if (!state.durationExtent) {
    facet.append(el('div', { class: 'note', text: 'no durations were measured' }));
    return facet;
  }

  const thumb = (id, value, label) => el('input', {
    type: 'range', id, min: '0', max: String(DURATION_STEPS), step: '1',
    value: String(value), 'aria-label': label,
  });
  const low = thumb('duration-lo', state.duration.loPos, 'shortest duration');
  const high = thumb('duration-hi', state.duration.hiPos, 'longest duration');

  const fill = el('div', { class: 'range-fill' });
  const lowText = el('span');
  const countText = el('span', { class: 'n' });
  const highText = el('span');

  const paint = () => {
    const range = durationRange();
    const { loPos, hiPos } = state.duration;
    fill.style.left = `${(loPos / DURATION_STEPS) * 100}%`;
    fill.style.width = `${((hiPos - loPos) / DURATION_STEPS) * 100}%`;
    lowText.textContent = duration(range.lo);
    highText.textContent = duration(range.hi);
    // What the grid would hold if the range were applied now, by the same rule
    // the grid uses, so the number cannot disagree with what a release shows. It
    // counts against the other filters only, the way a chip's own count does.
    countText.textContent = String(state.assets
      .filter(a => matches(a, 'duration') && durationMatches(a, range)).length);
    // A range input reports its position, which means nothing here, so the
    // spoken value is the time it stands for.
    low.setAttribute('aria-valuetext', duration(range.lo));
    high.setAttribute('aria-valuetext', duration(range.hi));
  };

  // The thumbs cannot cross: a low edge above the high edge is not a range, and
  // the control would quietly select nothing.
  const moved = (edge, input) => {
    const key = edge === 'lo' ? 'loPos' : 'hiPos';
    if (edge === 'lo') state.duration.loPos = Math.min(Number(input.value), state.duration.hiPos);
    else state.duration.hiPos = Math.max(Number(input.value), state.duration.loPos);
    input.value = String(state.duration[key]);
  };

  // The readout follows the thumb; the grid follows the release. A rebuild is
  // every row in the library, and one per pixel of a drag is not free.
  const commit = (edge, input) => {
    const keepFocus = document.activeElement === input;
    moved(edge, input);
    render();
    // The rebuild replaced the element under the pointer, so the focus is put
    // back where it was: without this a thumb driven by the arrow keys works
    // exactly once.
    if (keepFocus) document.getElementById(input.id)?.focus({ preventScroll: true });
  };

  low.addEventListener('input', () => { moved('lo', low); paint(); });
  high.addEventListener('input', () => { moved('hi', high); paint(); });
  low.addEventListener('change', () => commit('lo', low));
  high.addEventListener('change', () => commit('hi', high));

  paint();
  facet.append(el('div', { class: 'range' },
    el('div', { class: 'range-track' }), fill, low, high),
    el('div', { class: 'range-readout' }, lowText, countText, highText));
  return facet;
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
  const sections = [];
  for (const axis of AXES) {
    sections.push(renderFacet(axis));
    // "When" and "how long" are the two questions about the file itself, and
    // everything below them is about what is inside it. Duration is a range over
    // a measurement rather than a set of values, so it is not one of the axes and
    // is rendered here instead.
    if (axis.key === 'when') sections.push(renderDuration());
  }
  rail.replaceChildren(...sections, renderClear());
  renderItems(items);
}

function renderFacet(axis) {
  // Counts are computed against everything that matches the *other* active
  // filters, which is the usual faceted-search rule: a chip must not read 0 just
  // because its own axis is what you are filtering on.
  const counts = new Map();
  const weak = new Set();
  for (const asset of state.assets) {
    // The rule the comment above describes. It was missing, so every count was
    // the whole library's and the "empty" state below was unreachable.
    if (!matches(asset, axis.key)) continue;
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
  const active = state.filters.size > 0 || state.query || durationRange().narrowed;
  return el('div', {}, el('button', {
    class: 'clear', text: 'clear filters', disabled: active ? null : 'disabled',
    onclick: () => {
      state.filters.clear();
      state.query = '';
      resetDuration();
      document.getElementById('q').value = '';
      document.getElementById('q-clear').hidden = true;
      render();
    },
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

// One video per row. Every still is the same size here, the cover included, so
// the row reads as a strip of what was extracted rather than a big picture with
// some small ones beside it. The cover is left at full opacity and the rest are
// dimmed, which marks it without spending a border on it. Three at most: the
// tiles are what give the row its height, so a fourth would wrap and leave some
// rows taller than others for nothing the row does not already say.
function renderRow(asset) {
  const best = cover(asset);
  const bestIndex = best ? asset.stills.indexOf(best) : 0;
  const shown = (asset.stills || []).slice(0, 3);

  const strip = el('div', { class: 'row-stills' });
  if (shown.length) {
    strip.append(...shown.map(still => el('img', {
      src: stillURL(still.path), alt: '', loading: 'lazy',
      class: still === best ? 'cover' : '',
      title: `${still.at_s}s — ${still.reason || 'no reason recorded'}`,
      onclick: event => {
        event.stopPropagation();
        openAsset(asset.id, asset.stills.indexOf(still));
      },
    })));
  } else {
    // Nothing to show, so the placeholder takes the tile's place rather than
    // leaving the row ragged - and there is no "one still" label any more: with
    // the still already beside it, saying so was telling the reader what they
    // could see.
    strip.append(el('div', { class: 'none', text: asset.technical?.has_video === false
      ? 'no stills — audio only' : 'no stills' }));
  }

  const source = asset.captured?.at_source;
  const approximate = source === 'file_mtime' || source === 'filename';
  const gps = asset.captured?.gps;

  return el('article', {
    class: 'row', 'data-selected': String(state.selected === asset.id),
    onclick: () => openAsset(asset.id, bestIndex),
  },
    strip,
    el('div', { class: 'row-meta' },
      el('div', { class: 'name', text: fileName(asset.source?.path) }),
      el('div', { class: 'when' },
        el('span', { class: approximate ? 'approx' : '', text: when(asset.captured?.at) }),
        el('span', { text: '·' }),
        el('span', { text: duration(asset.technical?.duration_s) }),
        el('span', { text: '·' }),
        el('span', { text: container(asset) }),
        // The count is only worth a line when there is more than one still to
        // count: the grid card already hides it for a single still, and the
        // strip shows them all anyway.
        (asset.stills || []).length > 1
          ? el('span', { text: `· ${asset.stills.length} stills` })
          : null,
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

// Playback is stopped on purpose rather than left to the browser. Closing the
// drawer hides it, and a hidden <video> carries on playing - and carries on
// asking for ranges - until the next render replaces it: measured in Chromium,
// closing the drawer mid-playback left it playing. That is the "it still plays
// for a bit", and the broken pipe the server then logs part way through a range.
// Dropping the source and calling load() aborts the pending request with it - the
// browser reports it as net::ERR_ABORTED.
function stopPlayback() {
  const video = document.querySelector('#drawer video');
  if (!video) return;
  video.pause();
  video.removeAttribute('src');
  video.load();          // drops the pending range request too
}

function closeDrawer() {
  stopPlayback();
  state.selected = null;
  state.playing = false;
  document.getElementById('drawer').hidden = true;
  document.getElementById('backdrop').hidden = true;
  document.getElementById('backdrop').replaceChildren();
  render();
}

// The play control is the one button in the interface that is a picture rather
// than a word. The triangle is drawn rather than typed: "▶" is rendered by
// whichever font happens to claim the character, at a size and on a baseline that
// font chooses, which is how a glyph ends up sitting off-centre in a round button.
const SVG_NS = 'http://www.w3.org/2000/svg';

function playGlyph(playing) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  // The button carries the label, so the drawing is not read out as well.
  svg.setAttribute('aria-hidden', 'true');
  const shape = document.createElementNS(SVG_NS, playing ? 'rect' : 'polygon');
  if (playing) {
    shape.setAttribute('x', '7');
    shape.setAttribute('y', '7');
    shape.setAttribute('width', '10');
    shape.setAttribute('height', '10');
    shape.setAttribute('rx', '1.5');
  } else {
    // Right of centre on purpose: a shape whose mass is on the left reads as
    // off-centre when it is geometrically central.
    shape.setAttribute('points', '8,5 19,12 8,19');
  }
  svg.append(shape);
  return svg;
}

function renderDrawer() {
  const asset = state.assets.find(a => a.id === state.selected);
  const drawer = document.getElementById('drawer');
  if (!asset) { closeDrawer(); return; }
  // Whatever was playing is about to be replaced by what this call builds, so it
  // is stopped first: switching to a still should silence the video, not leave it
  // playing behind the picture.
  stopPlayback();
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
    const playing = state.playing;
    actions.append(el('button', {
      class: 'play',
      // The words the button used to carry are here instead, so the control is
      // still named for anyone who cannot see the triangle.
      'aria-label': playing ? 'Stop and show the still' : 'Play here',
      title: playing ? 'Stop and show the still' : 'Play here',
      onclick: () => { state.playing = !playing; renderDrawer(); },
    }, playGlyph(playing)));
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

// The copy is served out of the index, the way stills are; the original comes
// from the media root, which is the one place the browser half reads the library
// itself. A record carries a copy only when the browser cannot play the source.
const mediaURL = asset => {
  const copy = asset.playback && asset.playback.path;
  const path = copy || String(asset.source?.path || '');
  const base = copy ? '/index/' : '/media/';
  return base + path.split('/').map(encodeURIComponent).join('/');
};

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

// The overlay is held back briefly. A twelve-record fixture loads in about 20ms,
// and a full-screen overlay that appears and vanishes inside one frame reads as a
// glitch rather than as progress; it is only worth showing once the wait is
// noticeable, which is what a few hundred records is.
const LOADING_DELAY_MS = 150;
const loading = document.getElementById('loading');
let loadingTimer = null;

function showLoading(message) {
  loading.querySelector('.loading-text').textContent = message;
  clearTimeout(loadingTimer);
  if (!loading.hidden) return;
  loadingTimer = setTimeout(() => { loading.hidden = false; }, LOADING_DELAY_MS);
}

function setLoading(done, total) {
  const percent = total ? Math.round((done / total) * 100) : 0;
  loading.setAttribute('aria-valuenow', String(percent));
  loading.querySelector('.loading-fill').style.width = `${percent}%`;
  loading.querySelector('.loading-text').textContent =
    total ? `loading ${done} of ${total} records` : 'reading the index…';
}

function hideLoading() {
  clearTimeout(loadingTimer);
  loading.hidden = true;
}

async function loadConcurrently(records, limit, onProgress) {
  const out = [];
  const failed = [];
  let next = 0, done = 0;
  const worker = async () => {
    while (next < records.length) {
      const index = next++;
      const id = records[index];
      // Every record is fetched inside its own try. Without it, one unreadable
      // record rejected the worker, which rejected Promise.all, which threw out
      // of start() and left the page on "loading … records…" for ever with the
      // grid empty and nothing said. One bad file must cost one card.
      try {
        const response = await fetch(`/index/videos/${encodeURIComponent(id)}.json`);
        if (response.ok) {
          out.push(await response.json());
        } else {
          failed.push({ id, reason: `HTTP ${response.status}` });
        }
      } catch (error) {
        failed.push({ id, reason: error.message });
      }
      done++;
      if (done % 5 === 0 || done === records.length) onProgress(done, records.length);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, records.length) }, worker));
  return { assets: out, failed };
}

/* ------------------------------------------------------------------ banner */

// The notice can be dismissed, and the dismissal lasts for the page rather than
// being remembered: a notice that can be silenced for good is a notice that gets
// missed, and this one names files that could not be read.
function showBanner(message) {
  document.getElementById('banner-text').textContent = message;
  document.getElementById('banner').hidden = false;
}

function hideBanner() {
  document.getElementById('banner').hidden = true;
}

function syncLayoutButton() {
  // The label names the layout a click would switch *to*, derived from the state
  // rather than written into the page as well - two places for one fact is how
  // the button ends up offering the layout already on screen.
  document.getElementById('layout-toggle').textContent =
    state.layout === 'grid' ? 'List view' : 'Grid view';
}

function fatal(title, detail, hint) {
  hideLoading();
  hideBanner();
  document.querySelector('main').replaceChildren(
    el('div', { class: 'fatal' },
      el('h1', { text: title }),
      el('p', {}, detail),
      hint ? el('p', {}, el('code', { text: hint })) : null));
  document.getElementById('count').textContent = '';
}

async function start() {
  let manifest, config;
  showLoading('reading the index…');
  try {
    const [manifestResponse, configResponse] = await Promise.all([
      fetch('/index/manifest.json', { cache: 'no-store' }),
      fetch('/config.json', { cache: 'no-store' }),
    ]);
    if (!manifestResponse.ok) throw new Error(`manifest.json returned ${manifestResponse.status}`);
    manifest = await manifestResponse.json();
    // The config is the server's, not the index's: it carries the absolute media
    // root, which the index cannot state when it records a relative one, and the
    // index versions this page is allowed to read.
    config = configResponse.ok ? await configResponse.json() : {};
    SUPPORTED_INDEX_VERSIONS = new Set(config.supported_index_versions || []);
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

  setLoading(0, names.length);
  const loaded = await loadConcurrently(names, 8, (done, total) => setLoading(done, total));
  state.assets = loaded.assets;
  // The ends of the duration slider are the library's own shortest and longest
  // video, so they cannot be known before the records are.
  measureDurationExtent();

  if (loaded.failed.length) {
    // The full list goes to the console, which is where someone looking into it
    // will be; the banner names a few and says what to do about them.
    console.warn('records that could not be read:', loaded.failed);
  }

  const notices = [];
  if (loaded.failed.length) {
    const named = loaded.failed.slice(0, 3).map(f => f.id).join(', ');
    const rest = loaded.failed.length > 3 ? ` and ${loaded.failed.length - 3} more` : '';
    notices.push(`${loaded.failed.length} record${loaded.failed.length > 1 ? 's' : ''} could not `
      + `be read (${named}${rest}) — the rest of the library is shown. `
      + 'Run "fisean scan" over the library again to rewrite them.');
  }
  if (config.media_root_available === false) {
    // Expected when the library is on an unmounted disk, and not an error: the
    // stills and the copy action both still work.
    notices.push(`The media directory is not reachable (${config.media_root || 'unknown'}), `
      + 'so playback is off. Stills and copy-path still work.');
  } else if (manifest.errors && manifest.errors.length) {
    notices.push(`${manifest.errors.length} file${manifest.errors.length > 1 ? 's' : ''} could not be read: `
      + manifest.errors.map(e => `${e.path} (${e.stage}: ${e.message})`).join('; '));
  }
  if (notices.length) showBanner(notices.join(' '));

  render();
  // Hidden after the first render, not before: a flash of empty grid between the
  // two would be worse than the overlay staying a moment longer.
  hideLoading();
}

/* ------------------------------------------------------------------ events */

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('q').addEventListener('input', event => {
    state.query = event.target.value.trim().toLowerCase();
    document.getElementById('q-clear').hidden = event.target.value === '';
    render();
  });
  document.getElementById('q-clear').addEventListener('click', () => {
    const input = document.getElementById('q');
    input.value = '';
    state.query = '';
    document.getElementById('q-clear').hidden = true;
    input.focus();
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
    syncLayoutButton();
    render();
  });
  document.getElementById('backdrop').addEventListener('click', closeDrawer);
  document.getElementById('banner-close').addEventListener('click', hideBanner);
  syncLayoutButton();

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