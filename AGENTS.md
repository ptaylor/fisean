# AGENTS.md

Instructions for any human or AI agent working in this repository.

`videos` is a tool for analysing and classifying the content of video files and
for extracting the stills that represent them, so that a library of videos can
be browsed visually instead of by filename.

## Status

**Nothing is implemented yet, and the technology stack is not chosen.** This
file therefore records process and intent only. The concrete sections —
technology, layout, commands, checks — are added as the decisions are actually
made (see "Keeping this file current").

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

- The browser never invokes `ffmpeg`, never decodes media, and never reads the
  media directory. If it needs a fact, that fact belongs in the index.
- The indexer serves no HTTP and owns no UI.
- The index format is a **versioned, documented contract**, in its own document
  once the format is settled. Letting indexer internals leak into the browser is
  exactly what would cost us the freedom to swap the backend.
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
8. Filters and lists the library by date, keyword, style and content.
9. Shows the stills as the primary browsing surface, and looks good doing it.
10. Reads only the index — see the rules above.

### Non-goals

- **Audio.** Speech transcription, speaker diarisation and audio classification
  are out of scope. `mp3` is carried because a single file in the library is not
  video, not because audio analysis is wanted.
- Editing, transcoding or de-duplicating the source media.

## Open questions

Recorded rather than guessed at:

- **What "style" means.** It is a browse dimension, so it needs a definition
  before anything can be indexed by it. Genre? Footage type — home movie,
  animation, screen recording, surveillance?
- **The classification scheme** — free tags, topics, or a fixed taxonomy — and
  where keywords come from: filename and container metadata, or visual
  embeddings, or both.
- **Where the index lives**: beside the media, or in a central cache. This
  decides whether the media directory is ever written to, and whether
  `.gitignore` needs patterns for the index and stills.
- **Whether the browser plays video**, or only shows stills.
- **The index format**, which is the contract the split rests on.

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

The decisions that actually gate the design are the **catalogue dimensions**
(what "style" means, and what the classification scheme is) and the **index
format** — not the decode layer, which is settled.

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

## Development Commands

None yet. Record the toolchain commands here as soon as they exist — how to
install dependencies, build, run the tool over a sample video, and test.

## Checks before committing

No test suite yet. Once there is one, every change runs it in full, and the
commands that gate a commit (formatting, linting, type checking, tests) belong in
this section. Prefer checks that can be run non-interactively from a terminal.

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
