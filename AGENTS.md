# AGENTS.md

Instructions for any human or AI agent working in this repository.

`videos` is a tool for analysing and classifying the content of video files and
for extracting key thumbnails from them, so that a library of videos can be
browsed visually instead of by filename.

## Status

**Nothing is implemented yet, and the technology stack is not chosen.** This
file therefore records process and intent only. The concrete sections —
technology, layout, commands, checks — are added as the decisions are actually
made (see "Keeping this file current").

## What the tool is for

- **Analyse** each video: inspect frames, audio and metadata to work out what the
  video contains.
- **Classify** it: assign each video to categories, whatever the chosen scheme
  turns out to be (tags, topics, a fixed taxonomy).
- **Extract key thumbnails**: pick the handful of frames that best represent the
  video, for use as library cover art and for scrubbing through the content.
- **Browse**: present a library of videos through those thumbnails and
  classifications.

Requirements will be added here as numbered lists as they are agreed, so that
later changes can be checked against them.

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

## Level of existing art

Neighbouring repositories under `/Users/paul/github/pftylr/` show the preferred
house style: small, single-purpose tools, plain standard-library code where
possible, minimal dependencies, an MIT `LICENSE`, and a `README.md` that is the
user-facing contract. Read the README before changing behaviour and keep it in
step with what you change.

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
