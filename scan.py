#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Paul Taylor
"""fisean scan — the indexer half of fisean.

Walks a directory hierarchy of video files, extracts what can be measured from
each one with `ffprobe` and `ffmpeg`, picks the stills that represent it, and
writes an index that the browser half reads. Standard library only; ffmpeg and
ffprobe are the only external programs, and they are run as **CLI processes**,
never linked. Linking `libav*` — through PyAV or any other binding — would make
this program GPL-3.0-or-later as well; see the licence section of AGENTS.md.

    fisean scan ~/Videos                    # index beside the library
    fisean scan ~/Videos --index /tmp/idx   # put the index elsewhere
    fisean scan ~/Videos --force            # re-scan files that have not changed
    fisean scan ~/Videos --dry-run          # say what would happen, write nothing

One process per file, and nothing is decoded that is not needed:

    ffprobe     technical metadata, tags, GPS, capture time
    ffmpeg      one sampling pass over a scaled copy, for scene changes, motion,
                blur and brightness; then one seek per still

The index format is the contract in docs/index-format.md. Where this file makes a
choice the contract leaves open, the choice is named as a constant below with the
reason, and the raw measurement is stored alongside the derived one so a
threshold can be re-tuned later without re-scanning the library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- constants

# Bumped to 2 on 2026-09-29: a measurement that is not a number is now refused
# rather than stored, so a record written under version 1 can hold a value this
# version would not write. The bump is what makes the next scan re-measure the
# files affected - three records in one real library were unreadable in the
# browser until it was done.
SCAN_VERSION = "2"
INDEX_VERSION = 1
ASSET_VERSION = 1
INDEX_DIR_NAME = "fisean-index"

VIDEO_EXTENSIONS = ("3gp", "avi", "dv", "flv", "mov", "mp3", "mp4", "mpeg")

# Stills are scaled down rather than stored at full resolution: at a few thousand
# videos the stills become the largest thing the tool creates, and 1280 is more
# than any grid or filmstrip asks for. Changing this only affects stills
# extracted afterwards; it does not require a re-scan of the measurements.
STILL_WIDTH = 1280
STILL_JPEG_Q = 3
# One still per shot, up to this many. A single-shot clip therefore gets one
# still rather than three near-identical ones.
STILLS_PER_VIDEO = 3
# Minimum seconds between two chosen stills, so a scene change a fraction of a
# second after the previous choice does not produce a duplicate.
STILL_MIN_GAP_S = 0.5

# Everything is measured on frames scaled to this width, so the numbers are
# comparable between a 3GP file and a 4K one. A video narrower than this is
# upscaled, which inflates its blur slightly; recorded rather than corrected.
ANALYSIS_WIDTH = 320
# Target number of sampled frames across the whole video.
SAMPLE_TARGET = 24
# ...but no fewer than one frame every this many seconds, so a long video does
# not get four samples and no shot boundaries worth the name.
MIN_SAMPLE_PERIOD_S = 30.0

# scdet's score, measured rather than guessed: a hard cut scores ~27, and
# intra-shot motion on animated test footage stays under ~4, with a start-of-file
# artefact up to ~4. 10 sits between them with room on both sides.
SCENE_THRESHOLD = 10.0
# Mean absolute frame difference below which a frame counts as frozen. The first
# frame of any file reports mafd 0 because it has no predecessor, so it is
# excluded. This is a starting guess in the same sense as the browser's quality
# thresholds: run a batch, look at where the numbers fall, and re-tune. Because
# mafd_p50 is stored beside the derived ratio, that re-tuning needs no re-scan.
FREEZE_MAFD = 1.0
# A frame whose mean luma is below this fraction of full scale counts as black.
BLACK_LUMA = 0.03
# Sharpness is reported on the browser's 0-1 scale, where its thresholds are
# 0.15 for "soft" and 0.30 for "sharp". blurdetect reports a blur estimate
# instead - larger is blurrier, ~4.6 for a crisp test pattern and ~40 for the
# same frame box-blurred - so sharpness = REFERENCE / (REFERENCE + blur) is used
# to invert and normalise it. At REFERENCE 8 the browser's 0.30 threshold falls
# at a blur estimate of ~18.7. The raw blur is stored as blur_p10.
SHARPNESS_REFERENCE = 8.0

# ---------------------------------------------------------------- output


class Palette:
    """ANSI colours, off unless the destination is a terminal that wants them."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)


class Glyphs:
    """Progress markers, in ASCII if the terminal cannot be trusted with more."""

    def __init__(self, unicode_ok: bool) -> None:
        self.ok = "✓" if unicode_ok else "ok"
        self.skip = "·" if unicode_ok else "-"
        self.fail = "✗" if unicode_ok else "x"
        self.dash = "—" if unicode_ok else "-"
        self.times = "×" if unicode_ok else "x"


class Reporter:
    """Progress in the form a human reads while waiting.

    One line per file as it finishes, with a transient line for the file being
    worked on so a slow video does not look like a hang. The transient line is
    only written when stdout is a terminal - piped or redirected output gets one
    clean line per file and nothing that has to be erased.
    """

    def __init__(self, colour: Palette, glyphs: Glyphs, verbose: bool, quiet: bool,
                 width: int) -> None:
        self.c = colour
        self.g = glyphs
        self.verbose = verbose
        self.quiet = quiet
        self.width = width
        self.live = colour.enabled and not quiet
        self.transient = False

    def _line(self, text: str) -> None:
        if self.transient:
            sys.stdout.write("\r\033[K")
            self.transient = False
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    def _pending(self, text: str) -> None:
        if not self.live:
            return
        sys.stdout.write("\r\033[K" + text)
        sys.stdout.flush()
        self.transient = True

    def header(self, rows: list[tuple[str, str]]) -> None:
        if self.quiet:
            return
        pad = max(len(label) for label, _ in rows)
        for label, value in rows:
            self._line(f"{self.c.dim(label.ljust(pad))}  {value}")
        self._line("")

    def working(self, index: int, total: int, path: str) -> None:
        o, t, p = self.g.dash, self.g.dash, path[:self.width].ljust(self.width)
        self._pending(f"{self.c.dim(f'{index:>4}/{total}')}  {o} {p} {self.c.dim(t)}")

    def done(self, index: int, total: int, path: str, detail: str) -> None:
        if self.quiet:
            return
        counter = self.c.dim(f"{index:>4}/{total}")
        self._line(f"{counter}  {self.c.green(self.g.ok)} "
                   f"{path[:self.width].ljust(self.width)} {detail}")

    def skipped(self, index: int, total: int, path: str, detail: str) -> None:
        if self.quiet:
            return
        counter = self.c.dim(f"{index:>4}/{total}")
        self._line(f"{counter}  {self.c.dim(self.g.skip)} "
                   f"{self.c.dim(path[:self.width].ljust(self.width))} {self.c.dim(detail)}")

    def failed(self, index: int, total: int, path: str, detail: str) -> None:
        # Never suppressed: a file that could not be indexed is the thing a
        # caller most needs to see.
        counter = self.c.dim(f"{index:>4}/{total}")
        self._pending("")
        self._line(f"{counter}  {self.c.red(self.g.fail)} "
                   f"{path[:self.width].ljust(self.width)} {self.c.red(detail)}")

    def note(self, text: str) -> None:
        if self.verbose and not self.quiet:
            self._line(self.c.dim("      " + text))

    def warn(self, text: str) -> None:
        if not self.quiet:
            self._line(self.c.yellow("  ! ") + text)

    def summary(self, parts: list[tuple[str, str]]) -> None:
        if self.transient:
            sys.stdout.write("\r\033[K")
            self.transient = False
        chunks = []
        for value, colour in parts:
            chunks.append(getattr(self.c, colour)(value))
        self._line("")
        self._line("  ".join(chunks))


def unicode_ok() -> bool:
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in encoding


def colour_wanted(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return hasattr(stream, "isatty") and stream.isatty()


# ---------------------------------------------------------------- helpers


def run(args: list[str], timeout: float | None = None) -> tuple[int, str, str]:
    """Run a program, capturing both streams. Never raises on a non-zero exit.

    The timeout is not paranoia: a truncated or badly interlaced file can leave
    ffmpeg reading forever, and one such file must not be able to stall a scan of
    a whole library.

    stdin is closed rather than passed `-nostdin`: ffprobe rejects that flag as
    an unknown option, and closing the pipe is the mechanism that works for both
    programs - which also means a stray keystroke cannot be eaten by ffmpeg while
    the user is typing in the terminal that started the scan.
    """
    try:
        done = subprocess.run(args, capture_output=True, text=False,
                              stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"gave up after {timeout:.0f}s"
    except OSError as exc:
        return 1, "", str(exc)
    return (done.returncode,
            done.stdout.decode("utf-8", "replace").strip(),
            done.stderr.decode("utf-8", "replace").strip())


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


# ffmpeg prefixes a component's complaint with the component and a heap address:
#   [mov,mp4,m4a,3gp,3g2,mj2 @ 0x7f7960904140] moov atom not found
# The address is different on every run, so leaving it in would make the manifest
# differ from itself between two scans of an unchanged library - noise in the
# index and a spurious diff in any repository that holds one. It tells a reader
# nothing, so the whole bracketed prefix goes.
FFMPEG_CONTEXT = re.compile(r"^\[[^\]]*\]\s*")


def tidy_error(text: str, path: Path) -> str:
    """Make a message from ffprobe or ffmpeg fit to go in the index.

    Two things have to go. The input path, because the index may contain no
    absolute path but `media_root` and no machine or user names (rule 2), so a
    message reading "/Users/someone/Videos/clip.mp4: Invalid data found" must not
    reach it. And ffmpeg's bracketed log context, for the reason above.

    What is kept is the first line, which is also the most specific one: a
    truncated MP4 gives "[mov,... @ 0x...] moov atom not found" followed by the
    generic "Invalid data found when processing input", and the first of those is
    the one that says what is actually wrong.
    """
    cleaned = first_line(text)
    for prefix in (f"{path}: ", f"{path.resolve()}: "):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    cleaned = FFMPEG_CONTEXT.sub("", cleaned, count=1).strip()
    return cleaned or "failed"


def parse_rate(value: str | None) -> float | None:
    """`avg_frame_rate` and friends arrive as "30000/1001", or as "0/0"."""
    if not value or "/" not in value:
        return None
    num, den = value.split("/", 1)
    try:
        num_f, den_f = float(num), float(den)
    except ValueError:
        return None
    if den_f == 0 or num_f <= 0:
        return None
    return num_f / den_f


def as_float(value) -> float | None:
    """A number, or None when the value is not one.

    `nan` and `inf` are refused rather than returned. ffmpeg reports `blur=nan`
    for a frame it cannot measure, and one such value in a sample list made
    percentile() return nan, which Python then writes as a bare NaN - not JSON,
    so JSON.parse rejects the whole record and the browser loses that card. That
    is not hypothetical: three records in a real library, all of them with one
    blur that came back nan. A value that is not a number is treated as no
    measurement, which is what null already means in this index.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def iso_z(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime | None:
    cleaned = text.strip().replace("Z", "+00:00")
    # FFmpeg writes tags such as "2018-12-31T23:58:12.000000Z".
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(text.strip()[:19], pattern)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


DATE_IN_NAME = (
    re.compile(r"(?P<y>19\d{2}|20\d{2})[-_.]?(?P<m>0[1-9]|1[0-2])[-_.]?(?P<d>0[1-9]|[12]\d|3[01])"
               r"[T_\- ]?(?P<H>[01]\d|2[0-3])[-_.:]?(?P<M>[0-5]\d)[-_.:]?(?P<S>[0-5]\d)?"),
    re.compile(r"(?P<y>19\d{2}|20\d{2})[-_.](?P<m>0[1-9]|1[0-2])[-_.](?P<d>0[1-9]|[12]\d|3[01])"),
)


def date_from_name(name: str) -> datetime | None:
    """Camcorders and phones write the date into the file name, which is often
    the only place it survives - most of the legacy formats in this library carry
    no capture metadata at all."""
    for pattern in DATE_IN_NAME:
        match = pattern.search(name)
        if not match:
            continue
        parts = match.groupdict()
        hour = int(parts.get("H") or 0)
        minute = int(parts.get("M") or 0)
        second = int(parts.get("S") or 0)
        try:
            return datetime(int(parts["y"]), int(parts["m"]), int(parts["d"]),
                            hour, minute, second, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


ISO6709 = re.compile(r"(?P<lat>[+-]\d{1,2}(?:\.\d+)?)(?P<lon>[+-]\d{1,3}(?:\.\d+)?)")


def parse_gps(tags: dict) -> dict | None:
    """ISO 6709, as written by phones: "+55.9533-003.1883/"."""
    for key in ("com.apple.quicktime.location.ISO6709", "location", "location-eng"):
        value = tags.get(key)
        if not isinstance(value, str):
            continue
        match = ISO6709.search(value)
        if match:
            return {"lat": float(match.group("lat")), "lon": float(match.group("lon"))}
    return None


def parse_device(tags: dict) -> dict | None:
    make = tags.get("com.apple.quicktime.make") or tags.get("make")
    model = tags.get("com.apple.quicktime.model") or tags.get("model")
    if not make and not model:
        return None
    return {"make": make or None, "model": model or None}


def slugify(stem: str, limit: int = 32) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()
    return (slug or "asset")[:limit]


def asset_id(relative_path: str, stem: str) -> str:
    """The contract's rule: the first 8 hex characters of the SHA-256 of the
    media-root-relative path, then a slug from the file name. Renaming a file
    therefore produces a new id - recorded in the contract as a known cost."""
    digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:8]
    return f"{digest}-{slugify(stem)}"


def human_bytes(count: int) -> str:
    size = float(count)
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}T"


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{rest:02d}s"


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return round(ordered[low] * (1 - weight) + ordered[high] * weight, 4)


def sharpness_from_blur(blur: float) -> float:
    return round(max(0.0, min(1.0, SHARPNESS_REFERENCE / (SHARPNESS_REFERENCE + blur))), 4)


# ---------------------------------------------------------------- ffmpeg work


def probe(path: Path, timeout: float) -> tuple[dict, dict, str | None]:
    """Technical facts and capture metadata. Returns (technical, captured, error)."""
    code, out, err = run([
        "ffprobe", "-v", "error",
        "-show_format", "-show_streams", "-of", "json", str(path),
    ], timeout)
    if code != 0:
        return {}, {}, tidy_error(err, path) or f"ffprobe exited {code}"
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        return {}, {}, f"ffprobe output was not JSON: {exc}"

    streams = payload.get("streams") or []
    fmt = payload.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    # The container name comes from the extension, not from format_name:
    # mov, mp4, 3gp, 3g2 and m4a all report the same demuxer, and the extension is
    # what the library is organised by.
    container = path.suffix.lstrip(".").lower()

    # avg_frame_rate is unreliable on some formats - DV reports "60000/1" for a
    # 25fps stream - so a sane r_frame_rate wins when avg is absurd.
    fps = parse_rate(video.get("avg_frame_rate") if video else None)
    raw = parse_rate(video.get("r_frame_rate") if video else None)
    if fps is None or fps > 1000:
        fps = raw
    if fps is not None:
        fps = round(fps, 4)

    duration = as_float(fmt.get("duration"))
    if duration is None and video:
        duration = as_float(video.get("duration"))
    if duration is None and audio:
        duration = as_float(audio.get("duration"))

    technical = {
        "container": container,
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "width": as_int(video.get("width")) if video else None,
        "height": as_int(video.get("height")) if video else None,
        "fps": fps,
        "duration_s": round(duration, 3) if duration is not None else None,
        "has_video": video is not None,
    }

    tags: dict = {}
    for source in (fmt.get("tags") or {}, (video or {}).get("tags") or {},
                   (audio or {}).get("tags") or {}):
        for key, value in source.items():
            tags.setdefault(key, value)

    captured: dict = {}
    stamp = None
    for key in ("creation_time", "com.apple.quicktime.creationdate", "date"):
        if isinstance(tags.get(key), str):
            stamp = parse_iso(tags[key])
            if stamp:
                break
    if stamp:
        captured["at"] = stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        captured["at_source"] = "container_metadata"
    else:
        named = date_from_name(path.name)
        if named:
            captured["at"] = named.strftime("%Y-%m-%dT%H:%M:%SZ")
            captured["at_source"] = "filename"
        else:
            captured["at"] = iso_z(path.stat().st_mtime)
            captured["at_source"] = "file_mtime"
    captured["device"] = parse_device(tags)
    captured["gps"] = parse_gps(tags)
    return technical, captured, None


SAMPLE_LINE = re.compile(r"^frame:(?P<n>\d+)\s+pts:\S*\s+pts_time:(?P<t>\S+)")
KEY_LINE = re.compile(r"^(?P<key>[\w.]+)=(?P<value>\S+)$")


def analyse(path: Path, duration: float | None, native_fps: float | None,
            sample_fps: float | None,
            timeout: float) -> tuple[list[dict], float, str | None]:
    """One sampling pass over a scaled copy of the video.

    Returns the samples, the rate used, and an error. Each sample carries the
    measurements the browser derives its quality facets from, plus the raw values
    they were derived from: scene score, motion, blur and mean luma.

    `freezedetect` is deliberately not used. It reported every interval of an
    animated clip as frozen - with `fps=2` in front of it, apparently comparing
    the wrong frames - so motion comes from `scdet`'s mafd instead, which is a
    plain mean absolute frame difference and behaves as expected.
    """
    if duration is None or duration <= 0:
        return [], 0.0, "duration unknown, nothing to sample"

    rate = sample_fps
    if rate is None:
        # Evenly across the video, but never sparser than one frame every
        # MIN_SAMPLE_PERIOD_S, so a long video still gets usable shot boundaries.
        rate = max(SAMPLE_TARGET / duration, 1.0 / MIN_SAMPLE_PERIOD_S)
        # Above the source rate the fps filter would only duplicate frames.
        if native_fps:
            rate = min(rate, native_fps)
    graph = (f"fps={rate:.6f},scale={ANALYSIS_WIDTH}:-2,"
             f"scdet=threshold={SCENE_THRESHOLD},blurdetect,signalstats,"
             f"metadata=print:file=-")
    code, out, err = run([
        "ffmpeg", "-v", "error", "-i", str(path),
        "-map", "0:v:0", "-an", "-vf", graph, "-f", "null", "-",
    ], timeout)
    if code != 0 and not out:
        return [], rate, tidy_error(err, path) or f"ffmpeg exited {code}"

    samples: list[dict] = []
    current: dict | None = None
    for line in out.splitlines():
        match = SAMPLE_LINE.match(line)
        if match:
            current = {"at_s": float(match.group("t"))}
            samples.append(current)
            continue
        if current is None:
            continue
        key_match = KEY_LINE.match(line)
        if not key_match:
            continue
        key, value = key_match.group("key"), key_match.group("value")
        if key == "lavfi.scd.score":
            current["scene"] = as_float(value)
        elif key == "lavfi.scd.mafd":
            current["mafd"] = as_float(value)
        elif key == "lavfi.blur":
            current["blur"] = as_float(value)
        elif key == "lavfi.signalstats.YAVG":
            yavg = as_float(value)
            if yavg is not None:
                current["luma"] = yavg / 255.0
    return samples, rate, None


def extract_still(path: Path, at_s: float, target: Path, width: int,
                  timeout: float) -> str | None:
    """One frame, seeked before the input so a long video is not decoded from the
    start for every still. Returns an error message, or None on success."""
    target.parent.mkdir(parents=True, exist_ok=True)
    code, _, err = run([
        "ffmpeg", "-v", "error", "-ss", f"{at_s:.3f}", "-i", str(path),
        "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2",
        "-q:v", str(STILL_JPEG_Q), "-y", str(target),
    ], timeout)
    if code != 0 or not target.is_file() or target.stat().st_size == 0:
        return tidy_error(err, path) or f"ffmpeg exited {code} extracting a still"
    return None


# ---------------------------------------------------------------- still choice


def split_shots(samples: list[dict]) -> list[list[dict]]:
    """Shots from the scene scores. scdet attributes a change to the frame that
    begins the new shot, so a sample above the threshold opens a shot."""
    shots: list[list[dict]] = []
    for index, sample in enumerate(samples):
        if index == 0 or (sample.get("scene") or 0.0) >= SCENE_THRESHOLD:
            shots.append([])
        shots[-1].append(sample)
    return [shot for shot in shots if shot]


def sample_score(sample: dict) -> float:
    """What makes a good still: sharp, and not black. A black frame is excluded
    from the cover rather than penalised, because it is never the frame that
    represents a video."""
    luma = sample.get("luma")
    if luma is not None and luma < BLACK_LUMA:
        return 0.0
    blur = sample.get("blur")
    return sharpness_from_blur(blur) if blur is not None else 0.0


def choose_stills(samples: list[dict], duration: float, wanted: int) -> list[dict]:
    """Up to `wanted` stills, spread across the timeline, one per shot.

    Two rules do the work. Only one still per shot, so a single-shot clip yields
    one still rather than three near-identical ones. And when there are more
    shots than stills, the timeline is divided into that many spans and the best
    frame in each span is taken, so the stills describe the whole video rather
    than clustering at the start.
    """
    shots = split_shots(samples)
    if not shots:
        return []
    wanted = max(1, min(wanted, len(shots)))

    for shot in shots:
        best = max(shot, key=sample_score)
        for sample in shot:
            sample["shot_best"] = sample is best
    shot_of: dict[int, int] = {}
    for number, shot in enumerate(shots, start=1):
        for sample in shot:
            shot_of[id(sample)] = number

    if len(shots) <= wanted:
        chosen = [max(shot, key=sample_score) for shot in shots]
    else:
        chosen = []
        span = duration / wanted
        for index in range(wanted):
            low, high = index * span, (index + 1) * span
            inside = [s for s in samples if low <= s["at_s"] < high]
            if not inside:
                if index == wanted - 1:                     # the last span, half-open
                    inside = [s for s in samples if s["at_s"] >= low]
                if not inside:
                    continue
            chosen.append(max(inside, key=sample_score))

    # One still per shot, so a span that landed twice in the same shot keeps only
    # its best; and no two stills closer together than the minimum gap.
    kept: list[dict] = []
    seen_shots: set[int] = set()
    for sample in sorted(chosen, key=lambda s: s["at_s"]):
        shot = shot_of.get(id(sample))
        if shot in seen_shots:
            continue
        if kept and sample["at_s"] - kept[-1]["at_s"] < STILL_MIN_GAP_S:
            if sample_score(sample) > sample_score(kept[-1]):
                kept[-1] = sample
                seen_shots = {shot_of.get(id(s)) for s in kept}
            continue
        kept.append(sample)
        seen_shots.add(shot)

    stills = []
    for sample in kept:
        shot = shot_of.get(id(sample))
        if sample.get("shot_best") and shot is not None:
            reason = f"sharpest frame of shot {shot}"
        else:
            reason = f"sharpest frame near {sample['at_s']:.1f}s"
        stills.append({
            "at_s": round(sample["at_s"], 3),
            "score": round(sample_score(sample), 4),
            "faces": None,
            "reason": reason,
        })
    return stills


# ---------------------------------------------------------------- one asset


def settings_fingerprint(args) -> dict:
    """What a record's measurements and stills depend on. When any of it changes,
    a skipped file's record is stale even though the file itself has not been
    touched - so a different still count re-extracts, and a re-tuned threshold
    re-measures, without --force over the whole library."""
    return {
        "scan_version": SCAN_VERSION,
        "analysis_width": ANALYSIS_WIDTH,
        "scene_threshold": SCENE_THRESHOLD,
        "freeze_mafd": FREEZE_MAFD,
        "sample_fps": args.sample_fps,
        "stills": args.stills,
        "still_width": args.still_width,
        "no_stills": bool(args.no_stills),
    }


def scan_one(path: Path, root: Path, index_dir: Path, existing: dict | None,
             args) -> tuple[dict | None, tuple[str, str] | None, list[dict]]:
    """Index one file. Returns (record, (stage, message), warnings).

    A warning is a failure that did not stop the record being written - a still
    that could not be extracted, an analysis pass that gave up. It is carried out
    to the manifest rather than dropped: a still that quietly went missing is the
    kind of thing nobody notices for a year.
    """
    relative = path.relative_to(root).as_posix()
    name = asset_id(relative, path.stem)
    stat = path.stat()

    if existing is not None and not args.force:
        source = existing.get("source") or {}
        fingerprint = existing.get("scan") or {}
        same_file = (source.get("size_bytes") == stat.st_size
                     and source.get("mtime_ns") == stat.st_mtime_ns)
        wanted = settings_fingerprint(args)
        same_rules = all(fingerprint.get(key) == value for key, value in wanted.items())
        if same_file and same_rules and existing.get("asset_version") == ASSET_VERSION:
            return None, None, []

    started = time.monotonic()
    technical, captured, error = probe(path, args.timeout)
    if error:
        return None, ("probe", error), []

    samples: list[dict] = []
    analysis = {
        "sampled_frames": 0, "shot_count": None, "mean_luma": None,
        "sharpness_p10": None, "black_ratio": None, "freeze_ratio": None,
    }
    warnings: list[dict] = []
    if technical.get("has_video"):
        samples, rate, sample_error = analyse(path, technical.get("duration_s"),
                                              technical.get("fps"), args.sample_fps,
                                              args.timeout)
        if sample_error:
            warnings.append({"stage": "analysis", "message": sample_error})
        elif samples:
            blurs = [s["blur"] for s in samples if s.get("blur") is not None]
            lumas = [s["luma"] for s in samples if s.get("luma") is not None]
            # The first sample has no predecessor, so mafd 0 there says nothing.
            movers = [s["mafd"] for s in samples[1:] if s.get("mafd") is not None]
            shots = split_shots(samples)
            analysis = {
                "sampled_frames": len(samples),
                "shot_count": len(shots) or None,
                "mean_luma": round(sum(lumas) / len(lumas), 4) if lumas else None,
                "sharpness_p10": percentile([sharpness_from_blur(b) for b in blurs], 0.10),
                "black_ratio": round(sum(1 for l in lumas if l < BLACK_LUMA) / len(lumas), 4)
                if lumas else None,
                "freeze_ratio": round(sum(1 for m in movers if m < FREEZE_MAFD) / len(movers), 4)
                if movers else None,
                # Raw measurements beside the derived ones, so the thresholds
                # above can be re-tuned without re-scanning the library.
                "blur_p10": percentile(blurs, 0.10),
                "mafd_p50": percentile(movers, 0.50),
                "sample_fps": round(rate, 6) if rate else None,
            }

    stills: list[dict] = []
    if samples and not args.no_stills:
        chosen = choose_stills(samples, technical.get("duration_s") or 0.0, args.stills)
        for number, still in enumerate(chosen, start=1):
            target = index_dir / "stills" / name / f"{number:02d}.jpg"
            still_error = extract_still(path, still["at_s"], target, args.still_width,
                                        args.timeout)
            if still_error:
                warnings.append({"stage": "stills", "message": still_error})
                continue
            still["path"] = f"stills/{name}/{number:02d}.jpg"
            still["cover"] = False
            stills.append(still)
        if stills:
            best = max(range(len(stills)), key=lambda i: stills[i]["score"])
            stills[best]["cover"] = True

    record = {
        "asset_version": ASSET_VERSION,
        "id": name,
        "source": {
            "path": relative,
            "size_bytes": stat.st_size,
            "mtime": iso_z(stat.st_mtime),
            "sha256": None,
            # Extra, beyond the contract: exact nanoseconds, so a file modified
            # twice within the same second is still noticed next run.
            "mtime_ns": stat.st_mtime_ns,
        },
        "technical": technical,
        "captured": captured,
        "analysis": analysis,
        "labels": [],
        "summary": None,
        "stills": stills,
        # Extra, beyond the contract: what these measurements depend on, so a
        # changed threshold invalidates a skip without touching the file.
        "scan": {**settings_fingerprint(args), "seconds": round(time.monotonic() - started, 2)},
    }
    return record, None, warnings


def write_json(path: Path, payload) -> None:
    """Write JSON so an interrupted write cannot leave half a file behind.

    Written under a temporary name and renamed into place. A single write_text is
    not safe here: a record truncated at 632 bytes by a killed scan made one real
    library unloadable in the browser, because the browser gave up on the whole
    page at the first record it could not parse. The rename is atomic, so a reader
    sees either the old file or the new one and never a fragment, and a scan that
    dies mid-write leaves a .tmp file that nothing globs for and the next scan
    does not touch.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    # allow_nan=False, so a number that is not a number is refused here instead
    # of being written as a bare NaN. JSON has no NaN, and the browser's
    # JSON.parse rejects the whole record rather than the one field, which costs
    # a card. Refusing names the fault at scan time; write_record below turns
    # that into a manifest error against the one file rather than a dead scan.
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False,
                                    allow_nan=False) + "\n")
    os.replace(temporary, path)


def write_record(index_dir: Path, record: dict) -> str | None:
    """Write one record, returning a message if it could not be written."""
    try:
        write_json(index_dir / "videos" / f"{record['id']}.json", record)
    except ValueError as error:
        return f"record not written: {error}"
    return None


# ---------------------------------------------------------------- traversal


def find_media(root: Path, index_dir: Path, include_audio: bool) -> list[Path]:
    """Every supported file under root, in a stable order.

    The index directory is skipped - by default it sits *inside* the library, and
    a second run would otherwise find its own stills - and so is any dot
    directory, because .git and friends are never media.
    """
    found: list[Path] = []
    index_resolved = index_dir.resolve()
    for current, directories, files in os.walk(root):
        here = Path(current)
        directories[:] = sorted(
            d for d in directories
            if not d.startswith(".") and (here / d).resolve() != index_resolved
        )
        if here.resolve() == index_resolved:
            directories[:] = []
            continue
        for name in sorted(files):
            if name.startswith("."):
                continue
            suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if suffix not in VIDEO_EXTENSIONS:
                continue
            if suffix == "mp3" and not include_audio:
                continue
            found.append(here / name)
    return found


def existing_records(index_dir: Path) -> tuple[dict[str, dict], list[str]]:
    """The records already in the index, and the ids of any that would not parse.

    A record that cannot be read is not quietly skipped. It is reported, and
    because it is absent from the returned map the asset it belongs to counts as
    new and is indexed again - which is also what repairs it.
    """
    records: dict[str, dict] = {}
    unreadable: list[str] = []
    directory = index_dir / "videos"
    if not directory.is_dir():
        return records, unreadable
    for path in sorted(directory.glob("*.json")):
        try:
            records[path.stem] = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            unreadable.append(path.stem)
    return records, unreadable


def ffmpeg_facts() -> list[dict]:
    """The tooling and its licence, for the manifest. AGENTS.md requires
    committed generated output to record what produced it, and the licence of the
    decoder matters here: a GPL FFmpeg build is why this project is AGPL."""
    code, out, _ = run(["ffmpeg", "-version"])
    if code != 0:
        return []
    lines = out.splitlines()
    version = "unknown"
    licence = "unknown"
    if lines:
        parts = lines[0].split()
        if len(parts) > 2:
            version = parts[2]
    configuration = next((line for line in lines if line.startswith("configuration:")), "")
    if "--enable-gpl" in configuration:
        licence = "GPL-3.0-or-later" if "--enable-version3" in configuration else "GPL-2.0-or-later"
    elif configuration:
        licence = "LGPL-2.1-or-later"
    return [{"name": "ffmpeg", "version": version, "licence": licence}]


def vocabulary_hash() -> dict | None:
    source = Path(__file__).resolve().parent / "vocabulary.yaml"
    if not source.is_file():
        return None
    return {"name": "vocabulary.yaml",
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}


# ---------------------------------------------------------------- command


def resolve_index(root: Path, given: str | None) -> Path:
    if given:
        return Path(given).expanduser().resolve()
    return (root / INDEX_DIR_NAME).resolve()


def media_root_value(root: Path, index_dir: Path) -> str:
    """A relative media root wherever one is reasonable, so the index survives the
    library being moved, which is the point of rule 2. An index parked far away
    gets an absolute path instead - a chain of six ".." segments helps nobody."""
    relative = os.path.relpath(root, index_dir)
    if relative == ".":
        return "."
    if not relative.startswith(".."):
        return Path(relative).as_posix()
    if len(relative.split(os.sep)) <= 3:
        return Path(relative).as_posix()
    return str(root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fisean scan",
        description="Index a directory hierarchy of videos so the browser can read it.",
        epilog="Exit status is 0 when the scan ran, 1 when it could not run at all. "
               "Use --strict to also fail when a file could not be indexed.",
    )
    parser.add_argument("directory", nargs="?", default=".",
                        help="root of the hierarchy to scan (default: the current directory)")
    parser.add_argument("--index", "--out", dest="index", default=None,
                        help=f"index directory, any name and place (default: {INDEX_DIR_NAME}/ "
                             "inside the directory being scanned)")
    parser.add_argument("--force", action="store_true",
                        help="re-scan files that have not changed")
    parser.add_argument("--prune", action="store_true",
                        help="delete records whose media file is gone")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be done, write nothing")
    parser.add_argument("--stills", type=int, default=STILLS_PER_VIDEO,
                        help=f"most stills to keep per video (default: {STILLS_PER_VIDEO})")
    parser.add_argument("--still-width", type=int, default=STILL_WIDTH,
                        help=f"maximum still width in pixels (default: {STILL_WIDTH})")
    parser.add_argument("--no-stills", action="store_true", help="measure only, extract no stills")
    parser.add_argument("--sample-fps", type=float, default=None,
                        help="frames per second to sample for analysis (default: about "
                             f"{SAMPLE_TARGET} frames across the video)")
    parser.add_argument("--no-audio", action="store_true",
                        help="skip mp3 files (they are indexed by default, with no stills)")
    parser.add_argument("--jobs", type=int, default=1,
                        help="files to work on at once (default: 1, which keeps output in order)")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many files")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="seconds before one ffmpeg call is abandoned (default: 900)")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero if any file failed to be indexed")
    parser.add_argument("--quiet", action="store_true", help="only failures and the summary")
    parser.add_argument("--verbose", action="store_true", help="also show per-file timing")
    parser.add_argument("--no-colour", "--no-color", dest="no_colour", action="store_true",
                        help="plain output, no ANSI colours")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    colour = Palette(colour_wanted(sys.stdout) and not args.no_colour)
    glyphs = Glyphs(unicode_ok())
    reporter = Reporter(colour, glyphs, args.verbose, args.quiet, width=38)

    for program in ("ffprobe", "ffmpeg"):
        if shutil.which(program) is None:
            sys.stderr.write(
                f"{program} is not on PATH, and the indexer cannot work without it\n")
            return 1

    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        sys.stderr.write(f"no such directory: {root}\n")
        return 1
    index_dir = resolve_index(root, args.index)
    if index_dir == root:
        sys.stderr.write("the index directory cannot be the directory being scanned\n")
        return 1

    if not args.dry_run:
        try:
            (index_dir / "videos").mkdir(parents=True, exist_ok=True)
            (index_dir / "stills").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            sys.stderr.write(
                f"cannot create the index in {index_dir}: {exc}\n"
                "If the library is read-only, put the index elsewhere:\n"
                f"    fisean scan {args.directory} --index ~/indexes/{root.name}\n")
            return 1

    files = find_media(root, index_dir, not args.no_audio)
    if args.limit is not None:
        files = files[:args.limit]
    # Column width for the progress lines: the longest relative path, so codec,
    # size and duration line up down the page. Capped, because one deeply nested
    # file should not push everything else off the right edge of the terminal.
    if files:
        reporter.width = min(max(len(p.relative_to(root).as_posix()) for p in files), 46)
    if not files:
        reporter.header([("directory", str(root)), ("index", str(index_dir))])
        reporter.summary([("no supported files found", "yellow")])
        return 0

    records, unreadable = existing_records(index_dir)
    by_relative: dict[str, Path] = {}
    for path in files:
        by_relative[path.relative_to(root).as_posix()] = path
    current_ids = {asset_id(rel, p.stem) for rel, p in by_relative.items()}
    stale = sorted(set(records) - current_ids)

    counts: dict[str, int] = {}
    total_bytes = 0
    for path in files:
        suffix = path.suffix.lstrip(".").lower()
        counts[suffix] = counts.get(suffix, 0) + 1
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass

    if not args.quiet:
        reporter.header([
            ("directory", str(root)),
            ("index", str(index_dir)),
            ("media root", media_root_value(root, index_dir)),
            ("found", f"{len(files)} files, {human_bytes(total_bytes)}  " +
                      colour.dim(" ".join(f"{k} {v}" for k, v in sorted(counts.items())))),
            ("rules", f"one pass at {ANALYSIS_WIDTH}px, {args.stills} stills per video max, "
                      f"{args.still_width}px wide"),
        ])

    if unreadable:
        shown = ", ".join(unreadable[:3]) + (" …" if len(unreadable) > 3 else "")
        reporter.warn(f"{len(unreadable)} record(s) in the index could not be read "
                      f"({shown}); indexing them again")

    if args.dry_run:
        for index, path in enumerate(files, start=1):
            relative = path.relative_to(root).as_posix()
            name = asset_id(relative, path.stem)
            reporter.skipped(index, len(files), relative,
                             "would re-scan" if name in records else "would index")
        reporter.summary([(f"{len(files)} files", "bold"),
                          (f"{len(records)} already indexed", "dim"),
                          ("nothing written", "dim")])
        return 0

    started = time.monotonic()
    indexed = skipped = failed = 0
    stills_written = 0
    errors: list[dict] = []
    warnings: list[str] = []

    def handle(path: Path) -> tuple[str, dict | None, tuple[str, str] | None, list[dict], float]:
        relative = path.relative_to(root).as_posix()
        name = asset_id(relative, path.stem)
        record, error, problems = scan_one(path, root, index_dir, records.get(name), args)
        if record is None and error is None:
            return "skipped", None, None, [], 0.0
        seconds = float((record or {}).get("scan", {}).get("seconds") or 0.0)
        return ("indexed" if record else "failed"), record, error, problems, seconds

    def report(index: int, outcome: str, record: dict | None, error: tuple[str, str] | None,
               relative: str, problems: list[dict], elapsed: float) -> None:
        nonlocal indexed, skipped, failed, stills_written
        if outcome == "skipped":
            skipped += 1
            reporter.skipped(index, len(files), relative, "unchanged, not re-scanned")
            return
        if outcome == "failed":
            failed += 1
            stage, message = error or ("probe", "failed for no recorded reason")
            errors.append({"path": relative, "stage": stage, "message": message})
            reporter.failed(index, len(files), relative, message)
            return
        indexed += 1
        assert record is not None
        for problem in problems:
            errors.append({"path": relative, **problem})
        still_count = len(record["stills"])
        stills_written += still_count
        technical = record["technical"]
        shape = (f"{technical['width']}×{technical['height']}"
                 if technical.get("width") else "no video")
        # The still count is padded and the elapsed time right-aligned, so the
        # times form a column down the page instead of drifting with the width of
        # the text before them. Seven characters is room for "99m59s", so a scan
        # that takes 99 minutes does not shift the line, and one that takes longer
        # still fits: human_duration switches to "1h45m" past an hour.
        stills_text = f"{still_count} still" + ("" if still_count == 1 else "s")
        detail = (f"{colour.dim((technical.get('video_codec') or '--').ljust(10))} "
                  f"{shape:>9}  {human_duration(technical.get('duration_s')):>7}  "
                  f"{stills_text:<9} {colour.dim(f'{human_duration(elapsed):>7}')}")
        reporter.done(index, len(files), relative, detail)

    def write_record_or_report(relative: str, record: dict) -> None:
        """Write one record, naming the file if the write is refused.

        A refusal from write_json means this program produced a number that is
        not a number - a bug here, not a bad file. It is still better reported
        against one asset than left to end a 500-file scan at file 400, and the
        manifest says which record went missing.
        """
        problem = write_record(index_dir, record)
        if problem:
            errors.append({"path": relative, "stage": "write", "message": problem})
            reporter.warn(f"{relative}: {problem}")

    if args.jobs > 1:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(handle, files))
        for index, (path, result) in enumerate(zip(files, results), start=1):
            outcome, record, error, problems, seconds = result
            relative = path.relative_to(root).as_posix()
            report(index, outcome, record, error, relative, problems, seconds)
            if record is not None:
                write_record_or_report(relative, record)
    else:
        for index, path in enumerate(files, start=1):
            relative = path.relative_to(root).as_posix()
            reporter.working(index, len(files), relative)
            outcome, record, error, problems, seconds = handle(path)
            report(index, outcome, record, error, relative, problems, seconds)
            if record is not None:
                write_record_or_report(relative, record)

    pruned: list[str] = []
    for name in stale:
        record = records[name]
        path = index_dir / "videos" / f"{name}.json"
        if args.prune:
            path.unlink(missing_ok=True)
            for still in record.get("stills") or []:
                still_path = index_dir / still.get("path", "")
                if still_path.is_file():
                    still_path.unlink()
            still_dir = index_dir / "stills" / name
            if still_dir.is_dir():
                try:
                    still_dir.rmdir()
                except OSError:
                    pass
            pruned.append(name)
        else:
            warnings.append(record.get("source", {}).get("path", name))

    manifest = {
        "index_version": INDEX_VERSION,
        "generated": iso_z(time.time()),
        "media_root": media_root_value(root, index_dir),
        "asset_count": len(list((index_dir / "videos").glob("*.json"))),
        "generator": {"name": "scan.py", "version": SCAN_VERSION},
        "models": ffmpeg_facts(),
        # `assets` is deliberately absent. The browser then lists videos/ for
        # itself, which is also what makes an interrupted scan harmless: the
        # records already written are still found, with no manifest to fall out
        # of step.
    }
    vocabulary = vocabulary_hash()
    if vocabulary:
        manifest["vocabulary"] = vocabulary
    manifest["sampling"] = {"sample_fps": args.sample_fps, "analysis_width": ANALYSIS_WIDTH}
    if errors:
        manifest["errors"] = errors
    write_json(index_dir / "manifest.json", manifest)

    elapsed = time.monotonic() - started
    if warnings:
        shown = ", ".join(warnings[:4]) + (" …" if len(warnings) > 4 else "")
        reporter.warn(f"{len(warnings)} record(s) no longer match a file: {shown}")
        reporter.warn("re-run with --prune to delete them")
    reporter.summary([
        (f"scanned {indexed}", "green" if indexed else "dim"),
        (f"skipped {skipped}", "dim" if skipped else "dim"),
        (f"failed {failed}", "red" if failed else "dim"),
        (f"{stills_written} stills", "cyan" if stills_written else "dim"),
        (f"{human_duration(elapsed)}", "dim"),
    ])
    if not args.quiet:
        reporter.note(f"index {index_dir}")
    # A file that could not be indexed is reported and recorded, but it does not
    # make the run a failure: a library with one corrupt file would otherwise fail
    # every scan for ever. --strict is there for callers that want the opposite.
    return 1 if (args.strict and failed) else 0


if __name__ == "__main__":
    sys.exit(main())
