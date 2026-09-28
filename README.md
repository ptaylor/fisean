# videos

Analyse and classify the contents of video files, and pull out key thumbnails so
that a library of videos can be browsed visually instead of by filename.

## Status

Nothing is implemented yet, and the technology stack is not chosen. There is
nothing to install and no commands to run: this README describes the intended
tool, not working software.

## What the tool is for

- **Analyse** each video — inspect frames, audio and metadata to work out what
  it contains.
- **Classify** it — assign each video to categories, whatever scheme is settled
  on.
- **Extract key thumbnails** — pick the handful of frames that best represent
  the video, for cover art and for scrubbing through the content.
- **Browse** — present a library of videos through those thumbnails and
  classifications.

## Working on this repo

Read [AGENTS.md](AGENTS.md) before changing anything. It carries the
conventions, the requirement to update itself in the same change that introduces
a new technology, and the required form of AI attribution.

## AI contributions

This README and [AGENTS.md](AGENTS.md) were written with GitHub Copilot
(DeepSeek V4 Flash).

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).

