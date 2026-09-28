# físeán

Analyse and classify the contents of video files, and extract the stills that
represent them, so that a library of videos can be browsed visually instead of
by filename. *Físeán* is Irish for *video*.

## Status

**The browser half runs; the indexer does not exist yet.** You can look at the
interface today — it reads a synthetic fixture library of twelve invented videos
(see [fixtures/README.md](fixtures/README.md)), so everything on screen is fake
until the indexer is written.

```sh
python3 browse.py            # opens http://127.0.0.1:8765/
```

Nothing to install: the browser half is one Python file using only the standard
library, plus a `static/` directory holding its stylesheet and script.

## Two parts

The tool is deliberately two programs, so that either half can be replaced
without rewriting the other:

| Part | What it does |
| --- | --- |
| **Indexer** | Walks a directory hierarchy offline and writes an index: metadata, classifications, keywords, a summary, and the extracted stills. **Not written yet.** |
| **Browser** | A web app that reads only the index, and lists the library by when, where, who/what and quality — as a grid of covers or a list with larger stills, showing every extracted still rather than only the cover. It plays a video where the browser can decode it, and offers the full path for opening in a player where it cannot. |

The index format is the contract between them, specified in
[docs/index-format.md](docs/index-format.md) so that either half can be replaced.
The browser never decodes media — the single exception, serving the original
bytes for playback, is documented there. The source videos are left untouched, so
the library can stay read-only.

Because it reads JSON and nothing else, the browser half has no machine-learning
dependencies at all: the heavy work belongs to the indexer, where it can be slow
and repeated without slowing down the interface.

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

This README and [AGENTS.md](AGENTS.md) were written with GitHub Copilot
(DeepSeek V4 Flash).

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).

