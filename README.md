# videos

Analyse and classify the contents of video files, and extract the stills that
represent them, so that a library of videos can be browsed visually instead of
by filename.

## Status

Nothing is implemented yet, and the technology stack is not chosen. There is
nothing to install and no commands to run: this README describes the intended
tool, not working software.

## Two parts

The tool is deliberately two programs, so that either half can be replaced
without rewriting the other:

| Part | What it does |
| --- | --- |
| **Indexer** | Walks a directory hierarchy offline and writes an index: metadata, classifications, keywords, a summary, and the extracted stills. |
| **Browser** | A web app that reads only the index and lists the library by date, keyword, style and content, with the stills as the browsing surface. |

The index format is the contract between them: the browser never decodes media
and never reads the media directory, and the indexer serves no UI. The source
videos are left untouched, so the library can stay read-only.

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

