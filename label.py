#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Paul Taylor
"""fisean label — ask what is happening in each video, and write it down.

`scan.py` stops at numbers: it knows a video is forty seconds of dark, soft DV
and nothing about what is in it. This is the other half of the indexer. It seeks
a dozen frames from each video, asks a vision-language model how well each
phrase in `vocabulary.yaml` fits them, and writes the phrases that survive into
the record's `labels` — which is what the browser's "who & what" facet has been
waiting for.

    fisean label ~/Videos                label everything not already labelled
    fisean label ~/Videos --limit 20     twenty videos, to look at the results
    fisean label ~/Videos --calibrate    report where the scores actually fall
    fisean label ~/Videos --force        label again, even where it already has

**This is the only part of the project with dependencies.** It needs PyTorch and
open_clip, and PyTorch's last Intel-macOS wheel is 2.2.2 — which stops at Python
3.12, so it cannot be installed into the interpreter the rest of the project
runs on. It therefore has its own virtual environment, and says so rather than
failing with an ImportError:

    python3.12 -m venv ~/.venvs/fisean-labels
    ~/.venvs/fisean-labels/bin/pip install "torch==2.2.2" "numpy<2" open_clip_torch pyyaml

**Labelling writes `labels` and nothing else.** No measurement is taken and none
is invalidated, so `SCAN_VERSION` is untouched and a run can be repeated as often
as the vocabulary is edited without re-measuring a single file. What makes a
record skippable is its own `label` block: the model and the vocabulary hash that
produced the labels it already has.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The sibling indexer, imported rather than copied: these two are the same half
# of the tool, and the terminal output, the ffmpeg call and the atomic write are
# the same in both. scan.py has no import-time side effects.
sys.path.insert(0, str(HERE))
from scan import (  # noqa: E402
    INDEX_DIR_NAME, Glyphs, Palette, Reporter, colour_wanted, existing_records,
    human_duration, resolve_index, run, unicode_ok, write_json,
)

# Measured 2026-10-01 on this machine: 58 ms per frame on CPU, so twelve frames
# is about 0.7s of model time per video and the whole 538-video library is about
# half an hour including seeking the frames.
MODEL = "ViT-B-32"
PRETRAINED = "openai"
FRAMES = 12
FRAME_WIDTH = 384
TIMEOUT = 60.0
# Bumped when the scoring or what a record's `label` block means changes, the
# way scan.py's SCAN_VERSION guards measurements: a record whose labels came
# from the old one-global-softmax scoring must not be mistaken for current.
LABEL_VERSION = 2
# "a photo of X" is the prompt CLIP was trained with. The phrases in the
# vocabulary are written to slot into it — "a photo of family birthday cottage"
# is the only place the model ever sees them.
PROMPT = "a photo of {}"

INSTALL = """fisean label needs PyTorch and open_clip, which are not installed here.
They cannot go into the interpreter this project otherwise runs on: PyTorch's
last Intel-macOS wheel is 2.2.2, and it stops at Python 3.12. Give them their
own environment:

    python3.12 -m venv ~/.venvs/fisean-labels
    ~/.venvs/fisean-labels/bin/pip install "torch==2.2.2" "numpy<2" open_clip_torch pyyaml

Then run this command with that interpreter:

    ~/.venvs/fisean-labels/bin/python label.py <DIR>
"""


def dependencies():
    """The model stack, or None with an explanation.

    Imported here rather than at the top so that `--help` works, and so that a
    missing dependency is a sentence about how to fix it rather than a traceback
    ending in ImportError.
    """
    try:
        import numpy  # noqa: F401
        import open_clip
        import torch
        import yaml
    except ImportError:
        sys.stderr.write(INSTALL)
        return None
    return open_clip, torch, yaml


# ------------------------------------------------------------------ vocabulary


def load_vocabulary(yaml, path: Path) -> tuple[list[dict], list[dict]]:
    """The labels to ask for, and the calibration negatives.

    The negatives are scored alongside the real labels and never written: a
    label that scores as highly on "a plain wall" as on the frames is measuring
    nothing. One of them is what found a video-game menu in a real library.
    """
    data = yaml.safe_load(path.read_text())
    labels = []
    for entry in data["zero_shot"]:
        labels.append({
            "text": entry["text"],
            "group": entry.get("group"),
            "threshold": float(entry.get("threshold", 0.0)),
            "min_frames": int(entry.get("min_frames", 1)),
            "weak": bool(entry.get("weak", False)),
        })
    negatives = [{"text": text} for text in data.get("calibration", {}).get("negatives", [])]
    return labels, negatives


def vocabulary_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------- index


def load_index(directory: Path) -> tuple[Path, dict] | tuple[None, None]:
    """The index directory and its manifest, from either a library root or the
    index directory itself — the same either-way rule the other two commands
    follow."""
    if (directory / "manifest.json").is_file():
        index_dir = directory
    else:
        index_dir = directory / INDEX_DIR_NAME
    manifest_path = index_dir / "manifest.json"
    if not manifest_path.is_file():
        return None, None
    try:
        return index_dir, json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, None


def media_root(index_dir: Path, manifest: dict) -> Path:
    """The library the index describes. `media_root` may be relative, resolved
    against the index directory, which is what lets an index travel with its
    media (docs/index-format.md, rule 2)."""
    value = manifest.get("media_root") or "."
    root = Path(value)
    return root if root.is_absolute() else (index_dir / root).resolve()


# --------------------------------------------------------------------- frames


def sample_times(duration: float, wanted: int) -> list[float]:
    """Where to look. The first and last twelfth are skipped: a frame at 0s is
    often black, and a frame at the end is often a fade, and neither says
    anything about the video."""
    if not duration or duration <= 0:
        return [0.0]
    return [duration * (i + 0.5) / wanted for i in range(wanted)]


def extract(source: Path, at: float, target: Path) -> bool:
    """One frame, by seeking. Seeking before the input is a fast seek, and one
    ffmpeg per frame keeps a two-hour file from being decoded end to end just to
    look at a dozen moments of it."""
    code, _, _ = run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{at:.3f}", "-i", str(source),
         "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "3", "-y", str(target)],
        timeout=TIMEOUT,
    )
    return code == 0 and target.is_file() and target.stat().st_size > 0


# ------------------------------------------------------------------- labelling


def label_record(open_clip, torch, model, preprocess, tokenizer, text_features,
                 by_group: dict[str, list[int]],
                 record: dict, root: Path, vocabulary: list[dict], negatives: list[dict],
                 wanted: int, image_module) -> tuple[list[dict], list[dict], str | None]:
    """Score one video, and return the labels it earned.

    Scored frame by frame, and the strongest frame is what the record's `score`
    reports. Each label is softmaxed against its own group plus the calibration
    negatives — never the whole vocabulary. One softmax over all 61 prompts gave
    the single best label almost the whole probability budget, so a video kept
    labels from one group only; softmaxing per group alone forced a winner out of
    every group, so the two-label "screen" group labelled every video a screen
    recording. The negatives in each group's race are the floor: when nothing in
    a group matches, they win and the group's labels score low, so a video earns
    labels in several groups at once yet none where nothing is really there.

    Averaging the frames' embeddings into one judgement about the whole video
    was tried and measured: it is exactly wrong for the labels that matter most.
    A label true in two frames of twelve is averaged against ten unrelated ones
    and collapses — a bike ride that reads "cycling" 0.93 from its best frame
    reads 0.02 from the mean — while a scene label true in every frame keeps its
    score. Agreement across frames is the vocabulary's own `min_frames`, which
    is a count of frames that passed rather than a diluted average, and it is
    what decides whether a label is kept at all.
    """
    technical = record.get("technical") or {}
    duration = technical.get("duration_s")
    source = root / record["source"]["path"] if record.get("source") else None
    if source is None or not source.is_file():
        return [], [], f"media file is not reachable: {source}"

    times = sample_times(duration, wanted)
    with tempfile.TemporaryDirectory(prefix="fisean-label-") as work:
        frames = []
        for index, at in enumerate(times):
            target = Path(work) / f"{index + 1:02d}.jpg"
            if extract(source, at, target):
                frames.append(target)
        if not frames:
            return [], [], "no frames could be decoded"

        negative_indices = list(range(len(vocabulary), len(vocabulary) + len(negatives)))
        per_label: dict[str, list[float]] = collections.defaultdict(list)
        with torch.no_grad():
            for frame in frames:
                image = preprocess(image_module.open(frame).convert("RGB")).unsqueeze(0)
                vector = model.encode_image(image)
                vector = vector / vector.norm(dim=-1, keepdim=True)
                logits = 100.0 * vector @ text_features.T     # (1, labels + negatives)
                full = logits.softmax(dim=-1)[0]              # the negatives keep this scale
                for indices in by_group.values():
                    within = logits[0, indices + negative_indices].softmax(dim=-1)
                    for offset, index in enumerate(indices):
                        per_label[vocabulary[index]["text"]].append(within[offset].item())
                for entry, score in zip(negatives, full[len(vocabulary):].tolist()):
                    per_label[entry["text"]].append(score)

    kept = []
    for entry in vocabulary:
        scores = per_label[entry["text"]]
        agreed = sum(1 for score in scores if score >= entry["threshold"])
        if agreed >= entry["min_frames"]:
            kept.append({
                "text": entry["text"],
                "group": entry["group"],
                "source": "zero_shot",
                "model": MODEL_LABEL,
                "score": round(max(scores), 4),
                "frames": agreed,
                "weak": entry["weak"],
            })
    kept.sort(key=lambda label: label["score"], reverse=True)

    observed = [{"text": entry["text"], "group": entry["group"],
                 "threshold": entry["threshold"], "scores": per_label[entry["text"]],
                 "weak": entry["weak"]}
                for entry in vocabulary]
    observed += [{"text": entry["text"], "group": "calibration",
                  "threshold": 0.0, "scores": per_label[entry["text"]], "weak": False}
                 for entry in negatives]
    return kept, observed, None


# ---------------------------------------------------------------------- report


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def print_calibration(observed: dict[str, list[float]], thresholds: dict[str, float],
                      groups: dict[str, str], negatives: set[str]) -> None:
    """Where the frame scores actually sit, which is the only way to set a threshold.

    Per frame, not per video, because a threshold is compared with a frame's
    score: `min_frames` counts the frames that cleared it. A column of numbers
    the reader can set a threshold from, rather than a list of what passed —
    which is a decision and hides what it was made from.
    """
    sys.stdout.write("\n  frame scores over the library, per label\n")
    sys.stdout.write("  (a label is kept when `min_frames` frames score at or above "
                     "its threshold)\n\n")
    sys.stdout.write(f"  {'label':36} {'group':10} {'frames':>7} {'p50':>7} {'p90':>7} "
                     f"{'p99':>7} {'max':>7}  threshold\n")
    for text, scores in sorted(observed.items(), key=lambda kv: -percentile(kv[1], 0.90)):
        if text in negatives:
            continue
        sys.stdout.write(
            f"  {text[:35]:36} {(groups.get(text) or '')[:9]:10} {len(scores):>7} "
            f"{percentile(scores, 0.50):>7.3f} {percentile(scores, 0.90):>7.3f} "
            f"{percentile(scores, 0.99):>7.3f} {max(scores):>7.3f}  "
            f"{thresholds.get(text, 0.0):>6.2f}\n")
    if negatives:
        sys.stdout.write("\n  calibration negatives — nothing should sit high here\n")
        for text in sorted(negatives):
            scores = observed.get(text) or []
            if scores:
                sys.stdout.write(f"    {text[:44]:46} p90 {percentile(scores, 0.90):>7.3f}  "
                                 f"max {max(scores):>7.3f}\n")
    sys.stdout.write(
        "\n  Set a threshold near a label's p90 to keep the videos where it is\n"
        "  unmistakable, or near the p99 to keep only the ones it shouts about.\n")


# --------------------------------------------------------------------- command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fisean label",
        description="Ask a vision-language model what is happening in each video and "
                    "record it as labels the browser can filter by.",
        epilog="Labelling writes `labels` only: no measurement is taken and none is "
               "invalidated, so this may be run again whenever vocabulary.yaml changes.",
    )
    parser.add_argument("directory", nargs="?", default=".",
                        help="the library, or the index directory itself (default: .)")
    parser.add_argument("--force", action="store_true",
                        help="label again even where the labels are already current")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many videos")
    parser.add_argument("--match", default=None, metavar="TEXT",
                        help="only videos whose path contains TEXT — a year, a folder, a name")
    parser.add_argument("--frames", type=int, default=FRAMES,
                        help=f"frames to sample per video (default: {FRAMES})")
    parser.add_argument("--model", default=MODEL, help=f"open_clip model (default: {MODEL})")
    parser.add_argument("--pretrained", default=PRETRAINED,
                        help=f"open_clip checkpoint (default: {PRETRAINED})")
    parser.add_argument("--vocabulary", default=str(HERE / "vocabulary.yaml"),
                        help="the labels to ask for (default: vocabulary.yaml beside this program)")
    parser.add_argument("--calibrate", action="store_true",
                        help="report the score distribution at the end")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be labelled, write nothing")
    parser.add_argument("--quiet", action="store_true", help="only failures and the summary")
    parser.add_argument("--verbose", action="store_true", help="also report per-video timing")
    parser.add_argument("--no-colour", "--no-color", dest="no_colour", action="store_true",
                        help="plain output, no ANSI colours")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    colour = Palette(colour_wanted(sys.stdout) and not args.no_colour)
    reporter = Reporter(colour, Glyphs(unicode_ok()), args.verbose, args.quiet, width=38)

    stack = dependencies()
    if stack is None:
        return 1
    open_clip, torch, yaml = stack
    from PIL import Image

    global MODEL_LABEL
    MODEL_LABEL = f"{args.model.lower()}/{args.pretrained.lower()}"

    directory = Path(args.directory).expanduser()
    if not directory.is_dir():
        sys.stderr.write(f"no such directory: {directory}\n")
        return 1
    index_dir, manifest = load_index(directory)
    if index_dir is None:
        sys.stderr.write(f"no index under {directory} — run 'fisean scan {directory}' first\n")
        return 1
    root = media_root(index_dir, manifest)

    vocabulary_path = Path(args.vocabulary).expanduser()
    if not vocabulary_path.is_file():
        sys.stderr.write(f"no vocabulary at {vocabulary_path}\n")
        return 1
    vocabulary, negatives = load_vocabulary(yaml, vocabulary_path)
    if not vocabulary:
        sys.stderr.write(f"{vocabulary_path} asks for no labels\n")
        return 1
    digest = vocabulary_hash(vocabulary_path)

    records, unreadable = existing_records(index_dir)
    if not records:
        sys.stderr.write(f"no records in {index_dir}/videos\n")
        return 1

    todo = []
    for asset_id, record in sorted(records.items()):
        if not (record.get("technical") or {}).get("has_video"):
            continue                       # an mp3 has no frames to look at
        previous = record.get("label") or {}
        if not args.force and previous.get("model") == MODEL_LABEL \
                and previous.get("vocabulary") == digest \
                and previous.get("version") == LABEL_VERSION:
            continue
        todo.append(asset_id)
    if args.match:
        needle = args.match.lower()
        todo = [i for i in todo if needle in records[i]["source"]["path"].lower()]
    if args.limit is not None:
        todo = todo[:args.limit]

    reporter.header([
        ("index", str(index_dir)),
        ("media", str(root)),
        ("model", f"{args.model}/{args.pretrained}  " + colour.dim("open_clip, MIT")),
        ("labels", f"{len(vocabulary)} phrases, {len(negatives)} calibration negatives, "
                   f"{args.frames} frames per video"),
        ("to do", f"{len(todo)} videos  " + colour.dim(f"{len(records) - len(todo)} already current")),
    ])
    if unreadable:
        reporter.warn(f"{len(unreadable)} record(s) could not be read and are left alone")

    if not todo:
        reporter.summary([("nothing to label", "bold"),
                          ("use --force to label again", "dim")])
        if args.calibrate:
            print_calibration({}, {}, {}, set())
        return 0
    if args.dry_run:
        for index, asset_id in enumerate(todo, start=1):
            reporter.skipped(index, len(todo), records[asset_id]["source"]["path"], "would label")
        reporter.summary([(f"{len(todo)} videos", "bold"), ("nothing written", "dim")])
        return 0

    started = time.perf_counter()
    reporter.working(0, len(todo), "loading the model")
    model, _, preprocess = open_clip.create_model_and_transforms(
        args.model, pretrained=args.pretrained)
    tokenizer = open_clip.get_tokenizer(args.model)
    model.eval()
    texts = [PROMPT.format(entry["text"]) for entry in vocabulary]
    texts += [PROMPT.format(entry["text"]) for entry in negatives]
    with torch.no_grad():
        text_features = model.encode_text(tokenizer(texts))
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
    # A label competes against its own group and the calibration negatives, never
    # the whole vocabulary. One softmax over all 61 prompts gave the single best
    # label almost the whole probability budget (one group per video); softmaxing
    # per group alone forced a winner out of every group, so the two-label screen
    # group labelled every video a screen recording. Adding the negatives to each
    # group's race gives it a "is anything here at all?" floor: when nothing in a
    # group matches, the negatives win and the group's labels score low instead of
    # being handed a winner by default.
    by_group: dict[str, list[int]] = collections.defaultdict(list)
    for index, entry in enumerate(vocabulary):
        by_group[entry["group"]].append(index)
    reporter.working(0, len(todo), "")
    reporter.note(f"model ready in {time.perf_counter() - started:.1f}s")

    labelled = failed = 0
    written_labels = 0
    errors: list[dict] = []
    observed: dict[str, list[float]] = collections.defaultdict(list)
    thresholds = {entry["text"]: entry["threshold"] for entry in vocabulary}
    groups = {entry["text"]: entry["group"] for entry in vocabulary}

    for index, asset_id in enumerate(todo, start=1):
        record = records[asset_id]
        relative = record["source"]["path"]
        reporter.working(index, len(todo), relative)
        began = time.perf_counter()
        keep, scores, error = label_record(
            open_clip, torch, model, preprocess, tokenizer, text_features,
            by_group, record, root, vocabulary, negatives, args.frames, Image)
        if error:
            failed += 1
            errors.append({"path": relative, "stage": "label", "message": error})
            reporter.failed(index, len(todo), relative, error)
            continue

        for row in scores:
            observed[row["text"]].extend(row["scores"])
        record["labels"] = keep
        record["label"] = {
            "version": LABEL_VERSION,
            "model": MODEL_LABEL,
            "vocabulary": digest,
            "frames": args.frames,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            write_json(index_dir / "videos" / f"{asset_id}.json", record)
        except ValueError as exc:
            failed += 1
            errors.append({"path": relative, "stage": "label", "message": str(exc)})
            reporter.failed(index, len(todo), relative, str(exc))
            continue
        labelled += 1
        written_labels += len(keep)
        names = ", ".join(label["text"] for label in keep[:4]) or "nothing"
        reporter.done(index, len(todo), relative,
                      f"{len(keep):>2} labels  {colour.dim(names[:52])}  "
                      + colour.dim(f"{time.perf_counter() - began:.1f}s"))

    if errors:
        known = {(e.get("path"), e.get("stage")) for e in manifest.get("errors") or []}
        merged = list(manifest.get("errors") or [])
        for error in errors:
            if (error["path"], error["stage"]) not in known:
                merged.append(error)
        manifest["errors"] = merged
    # The manifest records what produced the index, and now part of that is a
    # model rather than a decoder (docs/index-format.md, rule 8).
    models = [m for m in manifest.get("models") or [] if m.get("name") != "open_clip"]
    models.append({"name": "open_clip", "version": open_clip.__version__, "licence": "MIT"})
    models.append({"name": args.model, "version": args.pretrained, "licence": "MIT"})
    manifest["models"] = models
    if not args.dry_run:
        write_json(index_dir / "manifest.json", manifest)

    elapsed = time.perf_counter() - started
    reporter.summary([
        (f"{labelled} labelled", "green"),
        (f"{written_labels} labels", "bold"),
        (f"{elapsed / 60:.1f} min" if elapsed > 90 else f"{elapsed:.0f}s", "dim"),
    ])
    if failed:
        reporter.warn(f"{failed} video(s) could not be labelled; recorded in the manifest")
    if args.calibrate:
        print_calibration(observed, thresholds, groups,
                          {entry["text"] for entry in negatives})
    return 0


MODEL_LABEL = f"{MODEL}/{PRETRAINED}"


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.stderr.write("\nstopped\n")
        raise SystemExit(130)
