# Fixtures

A synthetic video library, used to build and check the browser half before any
real media exists. Nothing here is footage: the media files are FFmpeg test
patterns and the stills are colour gradients.

The point is to prove that the index contract in
[../docs/index-format.md](../docs/index-format.md) is implementable — and to
have something to look at — before the indexer exists to produce a real index.
The records describe a plausible library; the media files exist only so the
playback and copy-path paths can be exercised.

## What is committed, and what is not

| Path | Committed | Why |
| --- | --- | --- |
| `index/manifest.json`, `index/videos/*.json` | yes | 152 KB of contract examples; the browser needs them |
| `index/stills/**` | yes | so the browser works from a fresh clone with no toolchain installed |
| `media/**` | **no** | 2.2 MB of generated test patterns. Rebuild with the command below |
| `GENERATED.txt` | yes | the exact command behind every generated file |

`fixtures/media/` is ignored by git rather than committed because DV alone is
~25 Mbit/s: a committed stand-in library would be pure weight, and it is one
command to rebuild. A fresh clone therefore shows stills and copy-path normally,
with playback unavailable until the media is generated — which is itself the
"drive not mounted" state the browser has to handle gracefully.

An index inside this repository is a deliberate exception: a real library's index
lives outside the media directory, not in the repo.

## Regenerating

```sh
python3 fixtures/make_fixtures.py           # rebuild media, stills and index
python3 fixtures/make_fixtures.py --check   # report what would be generated
```

Requires `ffmpeg` and ImageMagick (`magick`) on `PATH`. It is a development
tool: the tool itself never calls it, and it is not installed anywhere.

The generator is deterministic, so re-running it should produce an identical
index. If it produces a diff, something about the environment changed and the
diff is the report.

## What the records deliberately exercise

The twelve assets are chosen to hit the awkward cases, not just the happy path:

- **every target container** — `avi`, `mov`, `mpeg`, `dv`, `3gp`, `flv`, `mp4`,
  plus one `mp3` that has no video stream at all and therefore no stills;
- **every date source** — `container_metadata`, `filename`, `file_mtime` (which
  the browser has to show as approximate), and one asset whose date is genuinely
  `unknown`, with a null `mtime` to match;
- **GPS present and absent**, and one asset with no device information at all;
- **partial analysis** — one asset has null luminance and sharpness, so the
  quality facets have to treat "not measured" as neither sharp nor dark rather
  than as zero;
- **weak labels** — twelve of them across the set, including at least one asset
  where the only evidence for "children" is a zero-shot score of 0.35;
- **an asset the indexer could not read**, recorded in `manifest.errors` rather
  than dropped;
- **a mixture of playable and unplayable media**, so the "copy full path"
  fallback is exercised by real files rather than simulated.

## Caveat

The media files are stand-ins, so their real resolution and duration do not
match what the records claim. The records are the fiction; the media is only
there to be decoded, or to fail to be decoded.

## AI contributions

This fixture generator, its records and this README were written with GitHub
Copilot (DeepSeek V4 Flash).
