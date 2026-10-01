<img src="static/icon.svg" width="76" height="76" alt="">

# físeán

Analyse and classify the contents of video files, and extract the stills that
represent them, so that a library of videos can be browsed visually instead of
by filename. *Físeán* is Irish for *video*.

## Status

**Both halves work.** `fisean scan DIR` indexes the videos under `DIR` —
technical metadata, duration, capture dates, quality measurements and the stills
that represent each one — `fisean label DIR` asks a vision-language model what is
happening in each video so the library can be browsed by what is in it, and
`fisean browse DIR` shows the result. Summaries are not written yet: `summary` is
null for every asset, so a video is described by its measurements and its labels
rather than by a sentence.

```sh
./install.sh                 # puts the fisean command in ~/bin

fisean scan DIR              # index the videos under DIR
fisean label DIR             # classify them: what is happening in each one
fisean browse DIR            # serve that index and open it
fisean open DIR              # the same command, under its other name
```

`fisean label` is the only command with dependencies — PyTorch and open_clip —
and they cannot live in the interpreter the rest of the project uses, because
PyTorch has no wheel for it. It keeps them in its own environment at
`~/.venvs/fisean-labels` and says how to build that environment if it is missing:

```sh
python3.12 -m venv ~/.venvs/fisean-labels
~/.venvs/fisean-labels/bin/pip install "torch==2.2.2" "numpy<2" open_clip_torch pyyaml
```

To try it on part of a library before committing to all of it, `--limit` takes the
first N and `--match` takes a year or a folder — `fisean label DIR --match 2013/`.
Labelling writes labels and nothing else, so it never costs you a re-scan.

Or without installing anything:

```sh
python3 fisean.py scan DIR      # or: python3 scan.py DIR --index /tmp/idx
python3 fisean.py browse DIR    # or: python3 browse.py DIR
python3 fisean.py browse        # the committed fixture, for a look around
```

`DIR` is either a library root or the index directory itself, and both commands
accept either, so the same path can be handed to both. A library root is looked
for a `fisean-index` subdirectory — that is where `scan` writes unless `--index`
says otherwise, and any name works when you point at the index directly. Options
after `DIR` go straight to the command:
`fisean scan DIR --force --jobs 4`, `fisean browse DIR --port 9000 --no-open`.

## Two parts

The tool is deliberately two programs, so that either half can be replaced
without rewriting the other:

| Part | What it does |
| --- | --- |
| **Indexer** | `scan.py` walks a directory hierarchy offline and writes an index — technical metadata, duration, capture dates, quality measurements, and the stills that best represent each video. `label.py` then asks a vision-language model what is happening in each video and writes the answers as labels. Summaries are **not** written yet. |
| **Browser** | A web app that reads only the index, and lists the library by when, where, who/what and quality, and by how long — a min–max slider whose ends are the library's own shortest and longest video — as a grid of covers or a list with larger stills, showing every extracted still rather than only the cover. It plays a video where the browser can decode it, and offers the full path for opening in a player where it cannot. |

The index format is the contract between them, specified in
[docs/index-format.md](docs/index-format.md) so that either half can be replaced.
The browser never decodes media — the single exception, serving the original
bytes for playback, is documented there. The source videos are left untouched, so
the library can stay read-only.

Because it reads JSON and nothing else, the browser half has no machine-learning
dependencies at all: the heavy work belongs to the indexer, where it can be slow
and repeated without slowing down the interface. The model the labeller uses is
kept out of the browser and out of `scan` too — it lives in its own virtual
environment, and only `fisean label` ever touches it.

## Formats

`3gp` `avi` `dv` `flv` `mov` `mp3` `mp4` `mpeg`

FFmpeg handles all of them, so one decoder covers the whole legacy spread.
`mp3` is carried because one file in the library is audio rather than video: it
has no frames, so it yields no stills.

## Not a goal

Audio analysis — no speech transcription, diarisation or audio classification.
What a video *looks* like is the point; what it sounds like is not.

## Working on this repo

Read [AGENTS.md](AGENTS.md) before changing anything. It carries the agreed
requirements, the architecture, the open questions, the conventions, the
requirement to update itself in the same change that introduces a new
technology, and the required form of AI attribution.

## AI contributions

This README, [AGENTS.md](AGENTS.md) and the icon (`static/icon.svg`, and the
`static/favicon.ico` generated from it) were written with GitHub Copilot
(DeepSeek V4 Flash).

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).

