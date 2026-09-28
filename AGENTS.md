# AGENTS.md

Instructions for any human or AI agent working in this repository.

`videos` is a tool for analysing and classifying the content of video files and
for extracting the stills that represent them, so that a library of videos can
be browsed visually instead of by filename.

The project is called **físeán** (Irish for *video*). The name carries its
síneadh fada in prose and in the interface title; file names, commands and
identifiers stay ASCII — `browse.py` now, `fisean-index` later — because accents
in executable names and paths are a portability tax that buys nothing.

## Status

The **browser half exists and runs**: `browse.py`, a standard-library Python
server, and its interface as three ordinary files in [`static/`](static) that it
serves from disk. It is developed against the synthetic fixture library in
[`fixtures/`](fixtures/README.md), so every number on screen describes invented
footage.

**The indexer does not exist.** The requirements, the format and the stack notes
below are decisions and intentions, not descriptions of working code. The one
exception is FFmpeg, which is measured rather than assumed — see the candidate
stack.

## What the tool is for

- **Analyse** each video: inspect frames and metadata to work out what the
  video contains.
- **Classify** it: assign each video to categories and derive keywords, enough
  to list the library by date, keyword, style and content.
- **Extract stills**: pick the handful of frames that best represent the video,
  for cover art and for browsing.
- **Browse**: present the library through those stills and classifications, in a
  web app.

**Correction (2026-09-28):** an earlier version of this file said the analysis
would inspect audio as well. Audio is explicitly **not** a goal — see Non-goals
below — so this now says frames and metadata only.

## Architecture — two parts, one contract

The tool is two programs, not one, and the split is a requirement rather than a
layout preference: either half must be replaceable without rewriting the other.

1. **Indexer** — walks a directory hierarchy offline and writes an *index*:
   metadata, classifications, keywords, a summary, and the extracted stills.
2. **Browser** — a web app that reads **only the index** and presents the
   library.

Rules that keep the seam honest:

- The browser never invokes `ffmpeg` and never decodes media. It does not read
  the media directory either, with **one documented exception**: serving the
  original bytes for playback, read-only. If it needs a *fact*, that fact belongs
  in the index.
- The indexer serves no HTTP and owns no UI.
- **The entry point dispatches; it does not implement.** `fisean.py` maps a
  subcommand to a program and `exec`s it, so signals, exit codes and output are
  the command's own, and neither half can creep into the other by way of a shared
  entry point. A later tool is a new subcommand, not a new branch inside the
  browser.
- The index format is a **versioned, documented contract** —
  [docs/index-format.md](docs/index-format.md), which both halves are held to.
  Letting indexer internals leak into the browser is exactly what would cost us
  the freedom to swap the backend.
- Re-indexing one file must not require touching the others.
- The index and the stills live outside the media directory, which stays usable
  read-only — it may be on a disk the tool has no business writing to.

## Requirements

Numbered, as agreed, so that a later change can be checked against them.

### Indexer

1. Runs over a directory hierarchy of videos, offline, with no network access.
2. Extracts the stills that best represent each video.
3. Classifies each video and derives keywords, enough to list the library by
   date, keyword, style and content.
4. Produces a summary of each video's content.
5. Writes its output outside the media directory, leaving the source files
   untouched.
6. Handles a mixed legacy library: `3gp`, `avi`, `dv`, `flv`, `mov`, `mp4`,
   `mpeg`.

### Browser

7. A browser-based web app over the index.
8. Filters and lists the library along four axes: **when** (date), **where**
   (GPS, and a place label where one is known), **who/what** (people, animals,
   objects, activity), and **quality** (sharp, dark, still).
9. Shows the stills as the primary browsing surface, and looks good doing it.
10. Plays a video where the browser can decode it, and otherwise offers a
    one-click **copy of the full path** so it can be opened in a player.
11. Reads only the index, save for the one documented playback exception.

### Non-goals

- **Audio.** Speech transcription, speaker diarisation and audio classification
  are out of scope. `mp3` is carried because a single file in the library is not
  video, not because audio analysis is wanted.
- Editing, transcoding or de-duplicating the source media.

## Open questions

The four questions that gated this file are answered, and the answers live in
[docs/index-format.md](docs/index-format.md): "style" is dropped, the
classification scheme is a hand-editable vocabulary plus a detector class list,
the index lives outside the media directory, and the browser plays video where
the browser can decode it. What remains open:

- **Place names from coordinates.** GPS is wanted and is stored, but turning
  coordinates into "Cornwall, UK" needs a bundled dataset or a network call, and
  the indexer is offline by requirement. Names come from `overrides.yaml`, or
  from a later opt-in step that is explicitly not part of the offline run.
- **How many stills per video**, and what to do about short or static clips — a
  15-second clip of a birthday cake may want one still, not six.
- **Still size and format**: full resolution, or scaled to a documented maximum
  width. At a few thousand videos the stills become the largest thing the tool
  creates.
- **Hand-picked cover stills**, which is a classification decision that is
  plainly the user's rather than the indexer's.
- **Whether the label thresholds are tuned by hand.** The values in
  `vocabulary.yaml` are starting guesses; running one batch over the real library
  and looking at where the scores actually fall is the obvious next step.

## The index contract — decisions recorded

- **The generated index is JSON, not YAML.** The browser reads it with
  `JSON.parse`, so the front end keeps zero dependencies; YAML would put a parser
  inside the browser half. JSON also has no indentation traps and no implicit
  typing surprises.
- **YAML for what a human edits**: `vocabulary.yaml` (labels — project source,
  in this repo) and `overrides.yaml` (corrections — kept beside the index,
  outside the repo, because it is one person's library data and may name places).
- **One JSON file per asset, plus a manifest**, so re-indexing one video rewrites
  one file and the counters, never its neighbours.
- **The index stores measurements and labels; the browser derives facets.**
  "Dark", "blurry" and "still" are thresholds over numbers, so re-tuning them
  must not require a re-index.
- **The `media_root` is the only path in the index that may be absolute**, and it
  may be relative, resolved against the index directory — which is what lets the
  committed fixture work after being cloned anywhere. So an index survives the
  library being moved.
- **Still selection is by scene change and then by score**, never by keyframe
  flags — see the traps under Target formats.
- **`stills` is a timeline and the cover is a flag.** The entries are in ascending
  `at_s`, and exactly one carries `"cover": true` — the frame the indexer judged
  best. Which still represents a video is therefore stated rather than implied by
  position, which with a timeline ordering would have meant "the opening shot".
  A hand-picked cover is the same flag set from `overrides.yaml`.
- **Playback is expected to fail for most of the legacy library** (browsers do
  not decode DV, MPEG-1, H.263 in 3GP, or Sorenson H.263 in FLV), which is why
  copy-full-path is a primary action rather than a fallback.
- **The browser resolves what the index cannot state.** The absolute media path
  is derived from `media_root` and served to the interface at `/config.json`;
  asset ids come from `manifest.assets` or from listing `videos/`. Both are
  browser-half behaviour, and `/config.json` is deliberately *not* part of the
  format: a derived value must not be stored twice.
- **The browser half was built against the fixture index**, before the indexer
exists, so that the contract was proved implementable first. Everything it shows
is invented footage until the indexer replaces the fixture.

## Target formats

All eight extensions are supported by FFmpeg. Measured on this machine against
FFmpeg 8.1.2, not assumed:

| Extension | FFmpeg's name for it | Note |
| --- | --- | --- |
| `3gp` | `3gp` (3GPP), sharing the `mov,mp4,m4a,3gp,3g2,mj2` demuxer | usually H.263 or MPEG-4 Part 2 inside |
| `avi` | `avi` | wide codec variety; several of them are decoders only |
| `dv` | `dv`, and `DV` in the codec table | often a raw stream with no container |
| `flv` | `flv` | Sorenson H.263 and VP6 are decode-only |
| `mov` | `mov` | |
| `mp3` | `mp3` | audio only — there are no frames to extract |
| `mp4` | `mp4`, plus `m4v`, `ipod`, `psp` variants | |
| `mpeg` | `mpeg` (MPEG-1 Systems / program stream), plus `mpegts`, `vcd`, `svcd`, `vob` | MPEG-2 PS is what `vob`, `dvd` and `svcd` are |

Two traps to know before writing any of this:

- **"Extract keyframes" degenerates on DV and Motion JPEG.** Both are intra-only,
  so every frame is a keyframe: a cheap keyframe-only scan returns either the
  whole video or almost nothing. Stills have to be chosen by scene-change
  detection, or by scoring frames, never by trusting keyframe flags.
- **`mp3` has no frames at all.** Anything downstream that assumes a video
  stream must exclude it — which is why the non-goals say audio is out, rather
  than letting "every file yields stills" be quietly implied.

## Prior art — surveyed 2026-09-28

Nothing here is adopted yet. This records what was looked at and why none of it
is the whole answer; licences are from the projects' own pages.

| Tool | Licence | What it gives us | Why it is not the answer |
| --- | --- | --- | --- |
| FFmpeg / ffprobe | GPL-3.0-or-later (this build) | decoding, probing, scene-change and content filters | a library, not a tool: the substrate everything else sits on |
| PySceneDetect | BSD-3-Clause | shot boundaries, `save-images`, stats files for tuning thresholds | segmentation only — no classification, no index, no UI |
| TransNetV2 | MIT | shot-boundary detection scoring above the classic detectors | repo untouched for about five years; still needs everything else built around it |
| vcsi | MIT | contact sheets built from `ffmpeg` and `ffprobe` | a contact sheet is a fixed grid at a fixed interval, not "the stills that best represent this video" |
| whisper.cpp, faster-whisper | MIT | speech transcription | audio is a non-goal, so these are out |
| Immich | AGPL-3.0 | the closest thing to the browser half: thumbnails, faces, CLIP search across photos and videos | brings a server, Postgres and its own schema; its index is not ours to replace |
| PhotoPrism | AGPL | the same shape as Immich, with video format support documented | as above |
| Katna | — | keyframe extraction | the repository now returns 404; treat as gone |
| MTN (movie thumbnailer) | — | contact sheets | its page would not load, so it is unverified and not an option |

## Candidate stack — nothing chosen yet

Not a Technology Stack entry, because no dependency has been adopted. What has
actually been measured:

- **FFmpeg 8.1.2** at `/usr/local/bin/ffmpeg`, a GPL build (see Licence). It
  covers all eight target extensions. Its filters already include most of the
  frame-selection toolkit: `scdet` (scene change), `thumbnail` (the most
  representative frame in a span), `blackdetect`, `freezedetect`, `mpdecimate`,
  `silencedetect` and `signalstats`.
- **`ffprobe` is present; `mediainfo` and `exiftool` are not installed.** Any
  metadata plan built on the latter two adds a dependency to install and to
  document, which is worth knowing before it is designed in.
- **PySceneDetect** (BSD-3) is the natural scene segmenter and **TransNetV2**
  (MIT) the stronger but frozen alternative. Either way, the `thumbnail` filter
  plus the quality filters above can choose the frame within each shot.

- **Object detection:** Ultralytics YOLO (the YOLO26 family, COCO 80 classes).
  Its licence is **AGPL-3.0**, verified in the repository — compatible with ours
  only because of the relicensing above, and it would have been a blocker under
  MIT.
- **Zero-shot labels and embeddings:** `open_clip` (CLIP and SigLIP2
  checkpoints; the SigLIP2 models report roughly 82–84% zero-shot ImageNet
  accuracy in that project's own table). Check its LICENSE file before adopting —
  the GitHub page links to it rather than stating it.
- **Model weights are a one-time download.** Requirement 1 says the indexer runs
  offline, so that means *no network at index time*: weights are fetched
  beforehand, once, and their directory earns a `.gitignore` entry when it exists.

The design questions this section used to list — the browse dimensions and the
classification scheme — are settled. See the requirements above, and
[the index format document](docs/index-format.md) for the rest.

## Keeping this file current — required

Whenever a new language, library, framework, service or major dependency is
introduced to this repository, **update this AGENTS.md file in the same change**
with:

- **What was added and why** — including the alternatives that were rejected and
  the reason, so the decision is not silently revisited.
- A **Best Practices** sub-section for that technology: idiomatic usage,
  project-specific conventions, and links to authoritative documentation.
- Any new **install / build / run / test / lint** commands, under
  "Development Commands".
- Standard ignore patterns for it in **`.gitignore`** — see the next section,
  which applies to every change, not only to new technologies.

Do not let this file go stale — it is the source of truth for how to work in this
repo. If you are unsure of the current best practice for a technology, check the
official documentation before writing the section rather than relying on
possibly outdated knowledge.

If a note in this file later turns out to be **wrong**, correct it in place and
say so explicitly. A silently deleted wrong theory gets reintroduced; a
corrected one with an explanation does not.

### Technology Stack entry format

```markdown
### <Technology Name>

- **Role**: what it is used for in this project.
- **Version**: pinned or minimum version, and what it is pinned to.
- **Best Practices**:
  - ...
- **Docs**: link(s) to official documentation.
```

## Keeping `.gitignore` current — required

`.gitignore` is part of a change, never a follow-up to it. When a change creates
files that should not be committed, the patterns go in **the same commit** — a
cached thumbnail committed by accident is much harder to remove than a line is
to add.

Keep current, at minimum:

- **Tool and dependency output**: build directories, package caches, virtual
  environments, downloaded models and sample media, generated index files.
- **Local state**: config and env files (`*.env`, `.env.local`), log files, and
  anything naming this machine or a user's home directory.
- **Editor and OS metadata**: `.DS_Store`, `.vscode/`, `.idea/`, `*.swp`.

Rules:

- Prefer the official patterns published for the technology over invented ones;
  check the tool's own documentation rather than guessing.
- Group patterns by reason, each under a comment saying which tool or platform it
  belongs to, so the next reader can tell what a pattern is for.
- **Never** ignore editor tooling by reflex where the team may want it shared: an
  ignore rule is a commitment, and adding an un-ignore afterwards (`!.vscode/…`)
  is confusing. Ignore the specific local artefacts, not the whole directory, when
  that distinction matters.
- **Never commit secrets** — ignore the file that holds them. If one is committed
  by accident, say so in the commit that removes it rather than deleting it
  quietly, because the history still has it.
- The failure to watch for is the opposite of the usual one: ignoring a file the
  tool needs at run time. If it is needed to run or to build, it is source and it
  belongs in git.

## Layout

| Path | What it is |
| --- | --- |
| `fisean.py` | the `fisean` command: one entry point that dispatches to the halves, and implements neither |
| `browse.py` | the browser half: CLI and HTTP server, nothing else |
| `static/` | its interface — `index.html`, `app.css`, `app.js`, served from disk |
| `install.sh` | symlinks `fisean` into `$BIN` (default `~/bin`) and checks that it runs |
| `fixtures/` | the synthetic library the browser is developed against — see [fixtures/README.md](fixtures/README.md) |
| `docs/index-format.md` | the index contract, version 1 |
| `vocabulary.yaml` | the labels the indexer will ask for: project source, hand-edited |
| `README.md` | the user-facing description of the tool |

The indexer will be a sibling file, not a module inside `browse.py`: the two
halves are separate programs by requirement, and the split is easier to keep
honest when it is also a file boundary.

## Technology Stack

### Python (standard library only)

- **Role**: the language of both halves. The browser half is `browse.py` —
  argument parsing, the HTTP server and the range-request handler — serving the
  interface from `static/`. No build step, no third-party import, nothing to
  install.
- **Version**: Python 3.9 or newer; developed against 3.14.6.
  `from __future__ import annotations` keeps the newer annotation spellings legal
  on the older interpreters.
- **Best Practices**:
  - **One program, no dependencies, no build step.** The front end is three
    ordinary files in `static/`, read from disk; do not add a bundler, a
    framework, npm, or a second Python module for the browser half. The heavy
    dependencies (PyTorch, the label models) belong to the indexer, and keeping
    the browser free of them is the whole reason the interface reads JSON.
  - **The front end is files, not strings**, so the second gate below checks the
    real script rather than a copy of it. `py_compile` cannot see into `static/`
    at all. This replaced an earlier one-file design whose JavaScript and CSS were
    Python string constants: 78% of a 1,300-line file that no editor, linter or
    checker could see inside.
  - Build DOM with `createElement` and `textContent`. Never `innerHTML` with
    anything that came from the index: a label or summary is data, and treating it
    as markup would make the index an injection vector.
  - Never block the page with `window.prompt` or `alert`; a modal is a worse
    failure than the thing it reports.
  - `ThreadingHTTPServer`, so a slow media read cannot block the interface, and
    `--no-open` exists so a check can run without hijacking a browser.
  - Values the interface needs but the index must not carry are served at
    `/config.json` — the resolved media root, whether it is reachable, and the
    index versions this server's interface can read — never copied into a second
    place where they can drift.
- **Docs**: https://docs.python.org/3/library/http.server.html ·
  https://docs.python.org/3/library/mimetypes.html

## Development Commands

```sh
python3 fixtures/make_fixtures.py        # rebuild the synthetic media, stills and index
python3 fixtures/make_fixtures.py --check

./install.sh                              # symlink fisean into ~/bin
BIN=/usr/local/bin ./install.sh           # elsewhere; a system path needs sudo

fisean open DIR                           # serve the index in DIR, open a browser
fisean browse DIR                         # the same command, second spelling
fisean browse DIR --port 9000 --no-open

python3 fisean.py browse fixtures/index   # the same thing without installing
python3 browse.py --index fixtures/index  # the browser half on its own
```

`DIR` may be the index directory itself (the one holding `manifest.json`) or a
library root with an `index/` subdirectory; both are accepted, and with no DIR
the committed fixture is used. Anything after `DIR` is passed straight through.

The browser needs nothing but Python. `ffmpeg` and ImageMagick (`magick`) are
needed only to rebuild the fixtures. An edit to `static/app.css` or
`static/app.js` needs only a page reload: both are read from disk per request,
and `index.html` always is.

## Checks before committing

There is no test suite. Two gates must pass, and the second is not optional:
**`py_compile` cannot see anything under `static/`.**

```sh
python3 -m py_compile browse.py fixtures/make_fixtures.py

node --check static/app.js
```

The CSS has no checker at all. It is checked by looking, which is why the next
step is not optional either:

```sh
python3 browse.py --no-open    # then exercise what you changed in a browser
```

Three browser-side mistakes have already cost time in this repo, and all three
were invisible to the gates above: a version check that referenced a Python
constant, a facet whose chip labels were built from the wrong field of a tuple,
and a card strip that sliced stills by position after the cover stopped being
first. A fourth was caught *by* a gate rather than by the browser — the startup
check for the front-end files looked for them in the wrong directory, and said
so on the first run — which is the argument for making a half-installed copy fail
loudly instead of serving a page with no stylesheet.

The fixtures must also regenerate cleanly: a second run of `make_fixtures.py`
should leave `git status` unchanged.

## Licence — AGPL-3.0-or-later

This repo was MIT until 2026-09-28 and is now **AGPL-3.0-or-later** (see
[LICENSE](LICENSE)). The decoder forced the change; the preference did not
decide it:

- **FFmpeg is GPL here.** The build at `/usr/local/bin/ffmpeg` is 8.1.2,
  configured `--enable-gpl --enable-version3`, so it is GPL-3.0-or-later.
  Shelling out to the `ffmpeg` / `ffprobe` **CLI** keeps it at arm's length, but
  linking `libav*` — through PyAV or any other binding — makes this program
  GPL-3.0-or-later as well. MIT could not survive that, and "never link the
  libraries" is a heavier rule to live under than the licence is.
- **AGPL keeps the prior art reachable.** Immich and PhotoPrism are both AGPL and
  are the closest existing answers to the browser half. GPL-3.0 cannot
  incorporate AGPL code; AGPL-3.0 can. (GPL-3.0 §13 runs one way only: GPL code
  may be combined into an AGPL work, not the reverse.) Choosing plain GPL-3.0
  would have closed that door for nothing.
- **The network clause costs nothing today.** The tool is offline and local, and
  the user of such a tool is the copyright holder anyway.

Every dependency added from here must be AGPL-compatible: BSD-3-Clause
(PySceneDetect), MIT (TransNetV2, vcsi) and Apache-2.0 (OpenCV) all are. An
AGPL-incompatible component is a rejected option, and the reason belongs with it
in the prior art survey under "Prior art".

## Level of existing art

Neighbouring repositories under `/Users/paul/github/pftylr/` show the preferred
house style: small, single-purpose tools, plain standard-library code where
possible, minimal dependencies, an MIT `LICENSE`, and a `README.md` that is the
user-facing contract. Read the README before changing behaviour and keep it in
step with what you change.

**This repo is the deliberate exception on licence** — AGPL-3.0-or-later, for the
FFmpeg and prior-art reasons above, not by drift. Do not "correct" it back to MIT
to match the neighbours.

## Commit conventions

- Imperative sentences, no conventional-commit prefix: "Extract keyframes with a
  scene-change threshold".
- The subject says what changed; the body says **why**, and records the trap that
  was avoided or the measurement that justified the choice.
- One logical change per commit.

## AI attribution — required

Any commit produced with an AI coding assistant — for the code, the docs, the
artwork, or the commit message itself — must end with a trailer naming the
assistant **and** the exact model and version:

```
Assisted-by: <assistant> (<model> <version>)
```

```
Assisted-by: GitHub Copilot (DeepSeek V4 Flash)
```

Rules:

- Name the model you were actually running, and its version when it has one.
  "AI", "Copilot", "an LLM" or a bare tool name is not attribution.
- One trailer per assistant: two assistants on one commit means two
  `Assisted-by:` trailers.
- Use `git commit --trailer "Assisted-by=GitHub Copilot (DeepSeek V4 Flash)"`
  rather than hand-placing the line, so it lands in the trailer block.
- No trailer means the work was written by hand. Never add one for a change you
  did not make.

### Elsewhere, not only in commits

Git history is not the only place this is read, so the same attribution applies
wherever the work is visible without it:

- **Documents and prose** substantially written by an assistant — this file,
  `README.md`, design notes — carry the assistant, model and version, either as a
  note in the document or in a short "AI contributions" list.
- **Release notes, changelogs, tags and pull-request descriptions** written with
  an assistant end with the same `Assisted-by:` trailer.
- **Generated artefacts** that are committed (thumbnails, classification output,
  fixtures) record in their header or in the command that regenerates them which
  tooling and which assistant produced them, so the result can be reproduced.

When in doubt, attribute. An unnecessary trailer costs a line; a missing one
misrepresents who wrote the work.
