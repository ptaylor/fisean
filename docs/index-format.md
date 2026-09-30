# Index format — version 1

The contract between the **indexer** and the **browser**. Either half may be
replaced by a different implementation as long as this document is honoured; the
browser must never need to know how the index was produced, and must never need
to know what a video file is.

**Status: draft.** This document and `manifest.json`'s `index_version` move
together. Version 1 is not frozen until the indexer exists and has been run over
a real library; until then, treat field names as provisional and say so in the
commit that changes them.

## 1. Rules the format exists to enforce

1. **The browser reads the index, and nothing else.** The single exception is
   playback — see section 7 — and it is narrowly drawn: bytes are served, never
   decoded.
2. **`media_root` is the only path in the index that may be absolute**, and it may
   also be relative — resolved against the index directory — so an index can
   travel with its media. Everything else is relative to it, with POSIX
   separators, so an index survives the library being moved or copied to another
   machine.
3. **`null` means "not known", never "probably not".** A field that could not be
   measured is `null`. No defaults that look like data.
4. **Every derived fact carries provenance**: which model, which version, which
   source. Two classifiers may both have an opinion about one video, and a
   later run may add a third.
5. **The index stores measurements and labels; the browser derives facets.**
   "Dark", "blurry" and "still" are thresholds over numbers, and a threshold is a
   presentation decision — so it belongs in the browser, where it can be changed
   without a re-index.
6. **One file per asset**, plus a manifest. Re-indexing a single video rewrites
   that one file and the manifest's counters. It never rewrites its neighbours.
7. **Additions are safe, changes are not.** The browser ignores fields it does
   not recognise, so the indexer may write extra fields within version 1. The
   browser refuses any `index_version` whose major number it does not know.
8. **Generated artefacts record their generator.** `manifest.generator` and
   `manifest.models` name the tooling and model versions that produced the index,
   which is both the reproducibility record and the licence attribution AGENTS.md
   requires of committed generated output.

## 2. Where the index lives

Beside the library it describes, in a directory named `fisean-index`:

```
<library>/
├── 2018/
│   └── img9981.mov
└── fisean-index/
    ├── manifest.json
    ├── overrides.yaml      # hand-edited; see below
    ├── stills/<id>/01.jpg …
    └── videos/<id>.json
```

The media itself is never written to; the index directory is a sibling of it.
`fisean scan <DIR> --index <SOMEWHERE>` puts the index anywhere else, under any
name — which is what a library on a read-only disk needs, and the scan says so
rather than failing obscurely when it cannot write. Both commands accept either
the library root or the index directory, so the same path works for both.

**Correction (2026-09-29):** this section previously specified
`~/Library/Application Support/videos/<library-name>/`, *outside* the media
directory, and said `--out` overrode it. That was reversed when the indexer was
written: an index that travels with its library is worth more than a pristine
media directory, because it can be moved, copied and backed up as one thing. The
flag is `--index`, and the media is still never modified.

`overrides.yaml` lives **here, not in the repo**:
it is a correction list for one person's library, it may name places and people,
and it is not project source. The label vocabulary is project source and does
live in the repo (`vocabulary.yaml`). Nothing reads `overrides.yaml` yet.

## 3. `manifest.json`

| Field | Type | Notes |
| --- | --- | --- |
| `index_version` | integer, required | major number only; `1` |
| `generated` | string, required | ISO 8601 UTC |
| `media_root` | string, required | absolute path as the indexer saw it; the one exception to rule 2 |
| `asset_count` | integer, required | number of `videos/*.json` files |
| `generator` | object, required | `{ "name", "version" }` — no host names, no user names |
| `models` | array, required | `{ "name", "version", "licence" }` per model used, including detector and any embedder |
| `vocabulary` | object, optional | `{ "name": "vocabulary.yaml", "sha256": "…" }`, so a reader can tell whether the labels reflect the current vocabulary |
| `errors` | array, optional | per-asset failures: `{ "path", "stage", "message" }`. An unreadable file must be recorded, not silently dropped |
| `assets` | array of strings, optional | asset ids. When absent, the browser lists `videos/` for `*.json` instead, so an indexer may leave it out |

Two things the browser needs that are deliberately **not** in the index. Asset
ids come from `assets` above or from listing `videos/`. And the absolute media
path — needed to build a path for the clipboard, since `media_root` may be
relative — is resolved by the browser half and served to its own interface at
`/config.json`. That endpoint is part of the browser, not the format: the index
must not gain a second copy of a path that can be derived.

## 4. `videos/<id>.json`

`id` is stable and filesystem-safe: the first 8 hex characters of the SHA-256 of
the media-root-relative path, then a slug from the file name — for example
`3f9a1c07-dsc01234`. **Known consequence:** renaming or moving a file produces a
new id, and any `overrides.yaml` entry keyed to the old id is lost. Recorded
rather than fixed, because content hashing would change the id every time a file
is re-encoded, which is worse.

```jsonc
{
  "asset_version": 1,
  "id": "3f9a1c07-dsc01234",

  "source": {
    "path": "2011/summer/dsc01234.avi",  // relative to media_root, POSIX separators
    "size_bytes": 12345678,
    "mtime": "2011-08-02T14:03:11Z",
    "sha256": null                        // optional; null when not computed
  },

  "technical": {
    "container": "avi",
    "video_codec": "mpeg4",
    "audio_codec": "mp3",
    "width": 640, "height": 480, "fps": 25.0, "duration_s": 42.3,
    "has_video": true                      // false for mp3; every frame-derived field then null
  },

  "captured": {
    "at": "2011-08-02T14:03:11Z",
    "at_source": "file_mtime",            // see below — never assume metadata exists
    "device": { "make": "Sony", "model": "HDR-CX115" },   // null when unknown
    "gps": { "lat": 51.5074, "lon": -0.1278 }             // null when unknown
  },

  "analysis": {
    "sampled_frames": 20,                 // how many frames the numbers below describe
    "shot_count": 12,
    "mean_luma": 0.42,                    // 0–1
    "sharpness_p10": 0.18,                // 10th percentile, so a few sharp frames do not hide a soft video
    "black_ratio": 0.01,
    "freeze_ratio": 0.04
  },

  "labels": [
    { "text": "outdoors", "group": "setting", "source": "zero_shot",
      "model": "siglip2-so400m-384", "score": 0.91, "frames": 9, "weak": false },
    { "text": "bicycle", "group": "what", "source": "detector",
      "model": "yolo26n", "score": 0.77, "frames": 4, "count": 3, "weak": false },
    { "text": "children playing", "group": "activity", "source": "caption",
      "model": "…", "score": null, "frames": 2, "weak": true }
  ],

  "summary": { "text": "…", "source": "caption", "model": "…" },  // or null

  "stills": [
    { "path": "stills/3f9a1c07-dsc01234/01.jpg", "at_s": 3.2,
      "score": 0.88, "faces": 2, "cover": true,
      "reason": "sharpest frame of shot 1" },
    { "path": "stills/3f9a1c07-dsc01234/02.jpg", "at_s": 18.7,
      "score": 0.74, "faces": 0, "cover": false,
      "reason": "scene change, most central frame of shot 7" }
  ]
}
```

### `captured.at_source`

| Value | Meaning |
| --- | --- |
| `container_metadata` | from the file's own metadata; trustworthy |
| `filename` | parsed from a date in the file name (camcorders and phones do this) |
| `file_mtime` | last resort. **Wrong for files that were copied or restored from backup** — the browser should show this date as approximate |
| `user_override` | from `overrides.yaml` |
| `unknown` | `at` is then `null` |

Legacy `3gp`, `dv`, `flv` and `avi` files usually carry no capture date at all,
which is why this field exists rather than a bare timestamp.

### `captured.gps`

`null` when the file carries no position. A position is stored only when the tag
is one that stands up as a location:

- **A zero coordinate is not a fix.** A phone without a GPS lock writes the tag
  anyway: 75 files in one real library read `+00.0000+000.0000/`, with an empty
  altitude where a value would end. Stored as a position, every one of them was
  drawn at `0.0000, 0.0000` — a point in the Atlantic, and a claim the file does
  not make. A `0.0` in either coordinate is stored as `null`: the sentinel writes
  both, and an exact `0.0000` from a real fix is a coincidence rather than a
  position — a phone on the Greenwich meridian reads `0.0003`, not `0.0000`.
- **Out of range is not a position.** A latitude outside ±90 or a longitude
  outside ±180 is discarded rather than stored.

In that library only `3gp` files carry the tag at all, so a library whose
cameras never recorded a position gets an empty `where` facet rather than a row
of zeroes.

### `labels[].source`

`detector` (an object detector's class), `zero_shot` (a text label asked of a
vision-language model), `caption` (extracted from a generated description),
`metadata` or `filename` (derived from the container or the name), `user` (from
`overrides.yaml`). `user` always wins over a machine label with the same `text`.

`weak: true` marks a label that must never be used as a hard filter — age and
activity judgements made from single frames are hints, not facts. Weak labels are
shown in the interface with that caveat and are opt-in when filtering.

### `analysis`

Measurements only, all nullable, all describing the `sampled_frames` that were
looked at. Facets such as "dark" or "jerky" are derived by the browser from these
numbers, per rule 5.

## 5. Stills

- One directory per asset under `stills/<id>/`, files numbered `01.jpg` upwards.
- **`stills` is a timeline**: entries are in ascending `at_s`, so the filmstrip
  reads as the video plays. It is *not* ordered by preference.
- **Which still represents the video is stated, not implied.** Exactly one still
  may carry `"cover": true`. The indexer sets it on the frame it judges best —
  sharpest and most representative within its shot, scored across the sampled
  frames — and that is the one shown in the list. A hand-picked cover, which the
  user will want eventually, is the same flag set from `overrides.yaml`.
- A browser reading an index with no marked cover falls back to the highest
  `score`, and only then to the first entry. Position is the last resort, never
  the rule: with a timeline ordering, "first" means "opening shot", which is not
  a claim the indexer made.
- Chosen by scene-change detection and then by score within a shot — never by
  keyframe flags, which are meaningless on the all-intra formats (DV, Motion
  JPEG) where every frame is a keyframe.
- `reason` is a short human-readable string. It is deliberately free text and is
  not used for filtering; it exists so a later reader can tell why a still was
  picked without re-deriving it.

## 6. What the index deliberately does not contain

- No reverse-geocoded place names. Turning coordinates into "Cornwall, UK" needs
  either a large bundled dataset or a network call, and the indexer is offline by
  requirement. The index stores coordinates; names come from `overrides.yaml`, or
  from a later opt-in step that is explicitly not part of the offline run.
- No thumbnails sized for the grid, unless stills turn out to be too large to
  serve as-is. See the open questions.
- No absolute paths other than `media_root`, and no user or machine names.

## 7. Playback — the one exception

Playback is wanted, and it is the sole reason the browser may touch the media
directory. The exception is drawn narrowly:

- The browser may serve the **original bytes** of one asset, by `id`, read-only,
  with HTTP range requests, so the browser's own decoder can seek. It must not
  decode, transcode, remux or invoke `ffmpeg`.
- The path is `media_root` joined with `source.path`. The browser must reject any
  path that escapes the root (`..`, absolute paths, symlinks pointing out).
- **Playback will fail for most of the legacy library, and that is expected.**
  Browsers do not decode DV, MPEG-1, H.263-in-3GP or Sorenson-H.263-in-FLV. Only
  the `mov`/`mp4` material, and whatever modern codecs appear in them, will play.
- Therefore **copy-full-path is not a fallback of last resort, it is the primary
  action for the legacy formats**, and the interface must offer it plainly next to
  every asset, including when a still is all that could be extracted.
- When the media is not reachable (unmounted disk, moved library), playback
  degrades to stills plus copy-path, and the browser must not present a broken
  player.

**Correction (2026-09-28):** this document previously said the browser could be
run two ways, one of them from `file://` with playback off but stills and
copy-path working. That was wrong: the interface fetches `/index/manifest.json`,
and a `file://` origin cannot fetch. There is **one** way to run the browser, and
it is the small local server — which serves the index read-only and the media
read-only with range requests, and is therefore also the mode that can play
video.

## 8. Open questions in this contract

Recorded, not decided:

- **How many stills per video** — answered 2026-09-30: one still per shot, up to
  five, and a single-shot clip gets one still however long it runs. **What the
  indexer does:** one still per shot, and when there are more shots than stills
  the timeline is divided into that many spans, the best frame in each span being
  taken so the stills describe the whole video. A span that lands in a shot
  already represented is dropped as a duplicate, then made up from a shot that has
  no still yet, so the count is the number a video gets rather than a ceiling it
  falls short of. `--stills N` changes the ceiling; changing it re-extracts
  without `--force`. Five was chosen by measurement over a real 536-video library:
  1010 stills against the old fixed three's 833, never fewer for any one video. A
  duration cap was measured and rejected — one still per 5, 10 or 20 seconds took
  stills away from 45, 92 and 145 videos respectively.
- **Still size and format.** Extracted at full resolution as JPEG, or scaled to a
  documented maximum width? At a few thousand videos, full-resolution stills
  become the single largest thing the tool creates. **What the indexer does
  meanwhile:** JPEG, scaled to a maximum width of 1280px, never upscaled.
  `--still-width N` changes it.
- **Whether the stills are covered by `overrides.yaml`** — a hand-picked cover
  still per video is very likely to be wanted, and that is a user-owned choice
  the index cannot make.
- **Multiple media roots** per index. Version 1 has exactly one; a second disk
  means a second index, unless a `roots[]` array proves necessary.
