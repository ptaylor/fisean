#!/usr/bin/env python3
"""Generate the fixture index and the synthetic media it points at.

The browser half is developed against this before any real library exists, so
that the index contract in docs/index-format.md is proved implementable before
the indexer has to honour it. Nothing here is real footage: the media files are
FFmpeg test patterns and the stills are colour gradients, both generated.

Why a script rather than committed-by-hand JSON: AGENTS.md requires committed
generated artefacts to record the tooling that produced them, so the result can
be reproduced. The manifest records the generator, and GENERATED.txt records the
exact command behind every file.

    python3 fixtures/make_fixtures.py            # rebuild everything
    python3 fixtures/make_fixtures.py --check    # verify only, write nothing

Requires ffmpeg and ImageMagick on PATH. It is *not* part of the tool, and the
tool never calls it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
INDEX_DIR = ROOT / "index"
MEDIA_DIR = ROOT / "media"
VOCABULARY = REPO_ROOT / "vocabulary.yaml"

GENERATOR = {"name": "fixtures/make_fixtures.py", "version": "1"}

# The model list is synthetic: it mirrors the candidates in AGENTS.md so the
# provenance fields have something plausible to point at. A real index gets
# these from the indexer that actually ran.
MODELS = [
    {"name": "ffmpeg", "version": "8.1.2", "licence": "GPL-3.0-or-later"},
    {"name": "yolo26n", "version": "8.4.164", "licence": "AGPL-3.0"},
    {"name": "siglip2-so400m-384", "version": "webli", "licence": "Apache-2.0"},
]

# How to synthesise each kind of original. Kept deliberately in the formats the
# tool claims to support, including the ones a browser cannot decode: the point
# of the fixture is to exercise the copy-path fallback as well as the player.
MEDIA_KINDS: dict[str, list[str]] = {
    "mp4_h264": [
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15", "-t", "2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    ],
    "mov_h264": [
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15", "-t", "2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    ],
    "avi_mpeg4": [
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15", "-t", "2",
        "-c:v", "mpeg4", "-q:v", "6",
    ],
    "mpeg1": [
        "-f", "lavfi", "-i", "testsrc2=size=352x288:rate=25", "-t", "2",
        "-c:v", "mpeg1video", "-q:v", "8",
    ],
    # DV only has fixed profiles, so this one geometry is not a preference:
    # 720x576 yuv420p at 25 fps is PAL DV, and the encoder refuses anything else
    # (352x288 is a DVCPRO25 geometry ffmpeg's encoder will not accept). Kept to
    # a quarter of a second because DV is ~25 Mbit/s and a longer stand-in would
    # dwarf everything else the fixture generates.
    "dv": [
        "-f", "lavfi", "-i", "testsrc2=size=720x576:rate=25", "-t", "0.4",
        "-c:v", "dvvideo", "-pix_fmt", "yuv420p",
    ],
    "flv1": [
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15", "-t", "2",
        "-c:v", "flv", "-q:v", "8",
    ],
    "h263_3gp": [
        "-f", "lavfi", "-i", "testsrc2=size=176x144:rate=12", "-t", "2",
        "-c:v", "h263", "-pix_fmt", "yuv420p",
    ],
    "mp3": [
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:a", "libmp3lame", "-b:a", "64k",
    ],
}

# One entry per fixture asset. `_fixture` holds generator-only fields and is
# stripped before the record is written; everything else is the asset record as
# docs/index-format.md defines it.
ASSETS: list[dict] = [
    {
        "id": "3f9a1c07-dsc01234",
        "technical": {"container": "avi", "video_codec": "mpeg4", "audio_codec": "mp3",
                      "width": 640, "height": 480, "fps": 25.0, "duration_s": 42.3,
                      "has_video": True},
        # file_mtime on purpose: legacy camcorder footage rarely carries a capture
        # date, and the browser has to show that date as approximate.
        "captured": {"at": "2011-08-02T14:03:11Z", "at_source": "file_mtime",
                     "device": {"make": "Sony", "model": "HDR-CX115"}, "gps": None},
        "analysis": {"sampled_frames": 20, "shot_count": 12, "mean_luma": 0.42,
                     "sharpness_p10": 0.18, "black_ratio": 0.01, "freeze_ratio": 0.04},
        "labels": [
            {"text": "outdoors", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.91, "frames": 9, "weak": False},
            {"text": "garden", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.63, "frames": 5, "weak": False},
            {"text": "bicycle", "group": "what", "source": "detector",
             "model": "yolo26n", "score": 0.77, "frames": 4, "count": 3, "weak": False},
            {"text": "children playing", "group": "activity", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.34, "frames": 3, "weak": True},
        ],
        "summary": {"text": "Two children playing in a garden; a bicycle is propped "
                             "against the fence.",
                     "source": "caption", "model": "siglip2-so400m-384"},
        "stills": [
            {"at_s": 3.2, "score": 0.88, "faces": 2, "reason": "sharpest frame of shot 1"},
            {"at_s": 18.7, "score": 0.74, "faces": 0, "reason": "scene change, most central frame of shot 7"},
            {"at_s": 33.1, "score": 0.61, "faces": 2, "reason": "scene change, sharpest frame of shot 11"},
        ],
        "_fixture": {"media": "avi_mpeg4", "file": "2011/dsc01234.avi",
                     "palettes": [["#3a4d38", "#16210f"], ["#57704a", "#1c2a17"],
                                  ["#7d8f66", "#2b3a25"]],
                     "captions": ["garden · shot 1", "garden · shot 7", "garden · shot 11"]},
    },
    {
        "id": "a41b2e88-img0042",
        "technical": {"container": "mov", "video_codec": "h264", "audio_codec": "aac",
                      "width": 1920, "height": 1080, "fps": 29.97, "duration_s": 61.5,
                      "has_video": True},
        "captured": {"at": "2016-07-24T17:12:40Z", "at_source": "container_metadata",
                     "device": {"make": "Apple", "model": "iPhone 6"},
                     "gps": {"lat": 50.2617, "lon": -4.7956}},
        "analysis": {"sampled_frames": 31, "shot_count": 6, "mean_luma": 0.58,
                     "sharpness_p10": 0.33, "black_ratio": 0.0, "freeze_ratio": 0.02},
        "labels": [
            {"text": "beach", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.88, "frames": 12, "weak": False},
            {"text": "outdoors", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.94, "frames": 18, "weak": False},
            {"text": "people", "group": "who", "source": "detector",
             "model": "yolo26n", "score": 0.82, "frames": 14, "count": 4, "weak": False},
            {"text": "paddling at the water's edge", "group": "activity",
             "source": "zero_shot", "model": "siglip2-so400m-384",
             "score": 0.47, "frames": 5, "weak": True},
        ],
        "summary": {"text": "Family on a Cornish beach, paddling at the water's edge.",
                    "source": "caption", "model": "siglip2-so400m-384"},
        "stills": [
            {"at_s": 2.4, "score": 0.93, "faces": 4, "reason": "most central frame of shot 1"},
            {"at_s": 27.0, "score": 0.81, "faces": 2, "reason": "sharpest frame of shot 3"},
            {"at_s": 55.8, "score": 0.79, "faces": 3, "reason": "scene change, sharpest frame of shot 6"},
        ],
        "_fixture": {"media": "mov_h264", "file": "2016/img0042.mov",
                     "palettes": [["#2e6d8e", "#c9d9c0"], ["#4b8fa8", "#e6d8b8"],
                                  ["#87b6c4", "#f0e4c8"]],
                     "captions": ["beach · shot 1", "beach · shot 3", "beach · shot 6"]},
    },
    {
        "id": "7c2d5f10-mvi1234",
        "technical": {"container": "mpeg", "video_codec": "mpeg1video", "audio_codec": "mp2",
                      "width": 352, "height": 288, "fps": 25.0, "duration_s": 96.4,
                      "has_video": True},
        "captured": {"at": "2003-12-25T15:41:02Z", "at_source": "file_mtime",
                     "device": None, "gps": None},
        "analysis": {"sampled_frames": 24, "shot_count": 3, "mean_luma": 0.51,
                     "sharpness_p10": 0.12, "black_ratio": 0.03, "freeze_ratio": 0.11},
        "labels": [
            {"text": "indoors", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.86, "frames": 20, "weak": False},
            {"text": "birthday cake", "group": "what", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.41, "frames": 6, "weak": False},
            {"text": "balloons", "group": "what", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.55, "frames": 8, "weak": False},
            {"text": "children", "group": "who", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.38, "frames": 9, "weak": True},
        ],
        "summary": None,
        "stills": [
            {"at_s": 12.0, "score": 0.70, "faces": 3, "reason": "most central frame of shot 1"},
            {"at_s": 74.5, "score": 0.66, "faces": 1, "reason": "sharpest frame of shot 3"},
        ],
        "_fixture": {"media": "mpeg1", "file": "2003/mvi1234.mpeg",
                     "palettes": [["#6b4a2f", "#1a1208"], ["#8a6b45", "#241a10"]],
                     "captions": ["birthday · shot 1", "birthday · shot 3"]},
    },
    {
        "id": "0e6b3d55-clip0007",
        # A raw DV stream: no container metadata at all, so the date is unknown
        # rather than merely approximate. This is the asset that proves the
        # browser copes with a null date.
        "technical": {"container": "dv", "video_codec": "dvvideo", "audio_codec": "pcm_s16le",
                      "width": 720, "height": 576, "fps": 25.0, "duration_s": 212.0,
                      "has_video": True},
        "captured": {"at": None, "at_source": "unknown", "device": None, "gps": None},
        "analysis": {"sampled_frames": 42, "shot_count": 1, "mean_luma": 0.47,
                     "sharpness_p10": 0.29, "black_ratio": 0.24, "freeze_ratio": 0.61},
        "labels": [
            {"text": "countryside", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.52, "frames": 11, "weak": False},
            {"text": "people", "group": "who", "source": "detector",
             "model": "yolo26n", "score": 0.61, "frames": 6, "count": 2, "weak": False},
        ],
        "summary": None,
        "stills": [
            {"at_s": 88.0, "score": 0.52, "faces": 1, "reason": "sharpest frame of the single shot"},
            {"at_s": 190.2, "score": 0.48, "faces": 0, "reason": "second-highest score, same shot"},
        ],
        "_fixture": {"media": "dv", "file": "unknown/clip0007.dv",
                     "palettes": [["#5a5f52", "#171a13"], ["#4a4f45", "#12140f"]],
                     "captions": ["field · 88s", "field · 190s"]},
    },
    {
        "id": "b8f4a1c2-vid00123",
        "technical": {"container": "3gp", "video_codec": "h263", "audio_codec": "amr_nb",
                      "width": 176, "height": 144, "fps": 12.0, "duration_s": 28.7,
                      "has_video": True},
        "captured": {"at": "2007-11-03T11:05:00Z", "at_source": "filename",
                     "device": {"make": "Nokia", "model": "N95"},
                     "gps": {"lat": 53.4808, "lon": -2.2426}},
        "analysis": {"sampled_frames": 14, "shot_count": 5, "mean_luma": 0.49,
                     "sharpness_p10": 0.10, "black_ratio": 0.02, "freeze_ratio": 0.07},
        "labels": [
            {"text": "street or town", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.58, "frames": 6, "weak": False},
            {"text": "crowd", "group": "who", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.44, "frames": 4, "weak": False},
            {"text": "people", "group": "who", "source": "detector",
             "model": "yolo26n", "score": 0.66, "frames": 8, "count": 11, "weak": False},
        ],
        "summary": None,
        "stills": [
            {"at_s": 5.5, "score": 0.62, "faces": 5, "reason": "most central frame of shot 2"},
            {"at_s": 21.0, "score": 0.58, "faces": 2, "reason": "sharpest frame of shot 4"},
        ],
        "_fixture": {"media": "h263_3gp", "file": "2007/vid00123.3gp",
                     "palettes": [["#4a4a52", "#141418"], ["#5c5c63", "#1a1a1e"]],
                     "captions": ["street · shot 2", "street · shot 4"]},
    },
    {
        "id": "c1d9e0f3-webclip",
        # Partially analysed on purpose: nulls mean "not measured", and the
        # browser must not turn them into "false" for the quality facets.
        "technical": {"container": "flv", "video_codec": "flv1", "audio_codec": "mp3",
                      "width": 320, "height": 240, "fps": 15.0, "duration_s": 34.2,
                      "has_video": True},
        "captured": {"at": "2006-02-11T20:30:00Z", "at_source": "container_metadata",
                     "device": None, "gps": None},
        "analysis": {"sampled_frames": 12, "shot_count": 4, "mean_luma": None,
                     "sharpness_p10": None, "black_ratio": None, "freeze_ratio": None},
        "labels": [
            {"text": "indoors", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.49, "frames": 5, "weak": False},
            {"text": "dancing or performing", "group": "activity", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.36, "frames": 2, "weak": True},
        ],
        "summary": None,
        "stills": [
            {"at_s": 9.0, "score": 0.44, "faces": 1, "reason": "most central frame of shot 2"},
        ],
        "_fixture": {"media": "flv1", "file": "2006/webclip.flv",
                     "palettes": [["#3b2f47", "#120e1a"]],
                     "captions": ["indoor · shot 2"]},
    },
    {
        "id": "2a7c4b96-img9981",
        "technical": {"container": "mov", "video_codec": "h264", "audio_codec": "aac",
                      "width": 1920, "height": 1080, "fps": 29.97, "duration_s": 45.9,
                      "has_video": True},
        "captured": {"at": "2018-12-31T23:58:12Z", "at_source": "container_metadata",
                     "device": {"make": "Apple", "model": "iPhone X"},
                     "gps": {"lat": 55.9533, "lon": -3.1883}},
        "analysis": {"sampled_frames": 23, "shot_count": 8, "mean_luma": 0.12,
                     "sharpness_p10": 0.21, "black_ratio": 0.31, "freeze_ratio": 0.05},
        "labels": [
            {"text": "night or darkness", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.93, "frames": 21, "weak": False},
            {"text": "fireworks", "group": "what", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.61, "frames": 9, "weak": False},
            {"text": "crowd", "group": "who", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.57, "frames": 7, "weak": False},
            {"text": "people", "group": "who", "source": "detector",
             "model": "yolo26n", "score": 0.58, "frames": 12, "count": 23, "weak": False},
        ],
        "summary": {"text": "Hogmanay fireworks over a crowd in the dark.",
                    "source": "caption", "model": "siglip2-so400m-384"},
        "stills": [
            {"at_s": 30.5, "score": 0.90, "faces": 0, "reason": "sharpest frame of shot 5"},
            {"at_s": 41.2, "score": 0.72, "faces": 0, "reason": "scene change, most central frame of shot 8"},
            {"at_s": 12.9, "score": 0.55, "faces": 3, "reason": "most central frame of shot 2"},
        ],
        "_fixture": {"media": "mov_h264", "file": "2018/img9981.mov",
                     "palettes": [["#1b2036", "#07080f"], ["#3a2c52", "#0b0812"],
                                  ["#141726", "#05060b"]],
                     "captions": ["night · shot 5", "night · shot 8", "night · shot 2"]},
    },
    {
        "id": "5d3e8a41-audio001",
        # The one file in the library that is not video. No stills exist to
        # extract, and every frame-derived field is null rather than zero.
        "technical": {"container": "mp3", "video_codec": None, "audio_codec": "mp3",
                      "width": None, "height": None, "fps": None, "duration_s": 187.4,
                      "has_video": False},
        "captured": {"at": "2009-06-14T09:20:44Z", "at_source": "file_mtime",
                     "device": None, "gps": None},
        "analysis": {"sampled_frames": 0, "shot_count": None, "mean_luma": None,
                     "sharpness_p10": None, "black_ratio": None, "freeze_ratio": None},
        "labels": [],
        "summary": None,
        "stills": [],
        "_fixture": {"media": "mp3", "file": "2009/audio001.mp3",
                     "palettes": [], "captions": []},
    },
    {
        "id": "9f1b6c73-img2210",
        "technical": {"container": "mp4", "video_codec": "h264", "audio_codec": "aac",
                      "width": 3840, "height": 2160, "fps": 59.94, "duration_s": 73.8,
                      "has_video": True},
        "captured": {"at": "2021-02-14T10:03:00Z", "at_source": "container_metadata",
                     "device": {"make": "Apple", "model": "iPhone 11"},
                     "gps": {"lat": 46.0207, "lon": 7.7491}},
        "analysis": {"sampled_frames": 37, "shot_count": 9, "mean_luma": 0.71,
                     "sharpness_p10": 0.41, "black_ratio": 0.0, "freeze_ratio": 0.03},
        "labels": [
            {"text": "mountains", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.90, "frames": 25, "weak": False},
            {"text": "snow", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.87, "frames": 22, "weak": False},
            {"text": "a sledge or snowman", "group": "what", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.44, "frames": 6, "weak": False},
            {"text": "children", "group": "who", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.35, "frames": 8, "weak": True},
        ],
        "summary": {"text": "Children sledging in deep snow below the mountains.",
                    "source": "caption", "model": "siglip2-so400m-384"},
        "stills": [
            {"at_s": 8.1, "score": 0.96, "faces": 0, "reason": "sharpest frame of shot 1"},
            {"at_s": 44.6, "score": 0.85, "faces": 2, "reason": "scene change, sharpest frame of shot 6"},
            {"at_s": 68.0, "score": 0.80, "faces": 3, "reason": "scene change, most central frame of shot 9"},
        ],
        "_fixture": {"media": "mp4_h264", "file": "2021/img2210.mp4",
                     "palettes": [["#cfe1ef", "#8fa9bd"], ["#e8f0f6", "#a8bccd"],
                                  ["#a9c3d6", "#6f8ba1"]],
                     "captions": ["snow · shot 1", "snow · shot 6", "snow · shot 9"]},
    },
    {
        "id": "4b2e7d18-dsc00999",
        "technical": {"container": "avi", "video_codec": "mpeg4", "audio_codec": None,
                      "width": 1280, "height": 720, "fps": 30.0, "duration_s": 19.4,
                      "has_video": True},
        "captured": {"at": "2013-09-21T19:48:30Z", "at_source": "file_mtime",
                     "device": {"make": "Samsung", "model": "WB150"}, "gps": None},
        "analysis": {"sampled_frames": 10, "shot_count": 4, "mean_luma": 0.21,
                     "sharpness_p10": 0.13, "black_ratio": 0.06, "freeze_ratio": 0.02},
        "labels": [
            {"text": "city skyline", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.68, "frames": 5, "weak": False},
            {"text": "a car", "group": "what", "source": "detector",
             "model": "yolo26n", "score": 0.71, "frames": 3, "count": 6, "weak": False},
        ],
        "summary": None,
        "stills": [
            {"at_s": 4.0, "score": 0.58, "faces": 0, "reason": "most central frame of shot 2"},
        ],
        "_fixture": {"media": "avi_mpeg4", "file": "2013/dsc00999.avi",
                     "palettes": [["#2a2f3d", "#0b0d13"]],
                     "captions": ["city · shot 2"]},
    },
    {
        "id": "6c8a3f29-img5512",
        "technical": {"container": "mov", "video_codec": "h264", "audio_codec": "aac",
                      "width": 1920, "height": 1080, "fps": 29.97, "duration_s": 12.6,
                      "has_video": True},
        "captured": {"at": "2019-05-05T14:22:10Z", "at_source": "container_metadata",
                     "device": {"make": "Apple", "model": "iPhone 8"},
                     "gps": None},
        # One shot and little movement: the "still" quality facet, without which
        # a tripod shot of a glass case looks like any other clip.
        "analysis": {"sampled_frames": 8, "shot_count": 1, "mean_luma": 0.39,
                     "sharpness_p10": 0.35, "black_ratio": 0.0, "freeze_ratio": 0.72},
        "labels": [
            {"text": "in a museum or gallery", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.66, "frames": 6, "weak": False},
            {"text": "indoors", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.81, "frames": 7, "weak": False},
        ],
        "summary": None,
        "stills": [
            {"at_s": 6.3, "score": 0.64, "faces": 0, "reason": "sharpest frame of the single shot"},
            {"at_s": 10.9, "score": 0.60, "faces": 0, "reason": "second-highest score, same shot"},
        ],
        "_fixture": {"media": "mov_h264", "file": "2019/img5512.mov",
                     "palettes": [["#4d4436", "#191510"], ["#5a5041", "#1e1a13"]],
                     "captions": ["museum · 6s", "museum · 11s"]},
    },
    {
        "id": "e0f5b7d4-img7788",
        "technical": {"container": "mp4", "video_codec": "h264", "audio_codec": "aac",
                      "width": 1920, "height": 1080, "fps": 29.97, "duration_s": 88.2,
                      "has_video": True},
        "captured": {"at": "2022-08-19T13:10:00Z", "at_source": "container_metadata",
                     "device": {"make": "Apple", "model": "iPhone 11"},
                     "gps": {"lat": 51.5316, "lon": -0.1543}},
        "analysis": {"sampled_frames": 44, "shot_count": 14, "mean_luma": 0.55,
                     "sharpness_p10": 0.27, "black_ratio": 0.01, "freeze_ratio": 0.06},
        "labels": [
            {"text": "at the zoo or farm", "group": "setting", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.62, "frames": 15, "weak": False},
            {"text": "horses or farm animals", "group": "who", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.71, "frames": 12, "weak": False},
            {"text": "a group of people", "group": "who", "source": "zero_shot",
             "model": "siglip2-so400m-384", "score": 0.53, "frames": 9, "weak": False},
            {"text": "sheep", "group": "who", "source": "detector",
             "model": "yolo26n", "score": 0.64, "frames": 4, "count": 7, "weak": False},
        ],
        "summary": {"text": "A day at the zoo: sheep in a paddock and a crowd at the fence.",
                    "source": "caption", "model": "siglip2-so400m-384"},
        "stills": [
            {"at_s": 3.9, "score": 0.86, "faces": 3, "reason": "most central frame of shot 1"},
            {"at_s": 51.2, "score": 0.83, "faces": 0, "reason": "sharpest frame of shot 9"},
            {"at_s": 80.4, "score": 0.75, "faces": 5, "reason": "scene change, sharpest frame of shot 13"},
        ],
        "_fixture": {"media": "mp4_h264", "file": "2022/img7788.mp4",
                     "palettes": [["#4f6b3c", "#1b2413"], ["#6d8a4e", "#26301a"],
                                  ["#8a9c63", "#333d22"]],
                     "captions": ["zoo · shot 1", "zoo · shot 9", "zoo · shot 13"]},
    },
]

# A file the indexer could not read. The contract requires it to be recorded
# rather than dropped silently, so the fixture contains one.
ERRORS = [
    {"path": "2004/broken/corrupt.avi", "stage": "probe",
     "message": "Invalid data found when processing input"},
]

STILL_SIZE = "480x270"
STILL_QUALITY = "72"


def run(cmd: list[str]) -> None:
    """Run a generator command, failing loudly rather than leaving a half-made fixture."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write("failed: " + " ".join(cmd) + "\n" + result.stderr + "\n")
        raise SystemExit(1)


def reproducible(program: str, args: list[str], target: Path) -> str:
    """The command as a reader can re-run it: paths relative to the repo root.

    Absolute paths would work here and break the rule in AGENTS.md against
    committing anything that names this machine or a home directory.
    """
    output = target.relative_to(REPO_ROOT).as_posix()
    quoted = " ".join(shlex.quote(arg) for arg in [*args, output])
    return f"{program} {quoted}"


def vocabulary_hash() -> str | None:
    """Hash of the label vocabulary, so a reader can tell if labels are stale."""
    if not VOCABULARY.exists():
        return None
    return hashlib.sha256(VOCABULARY.read_bytes()).hexdigest()


def build_media(record: dict, log: list[str]) -> None:
    fixture = record["_fixture"]
    target = MEDIA_DIR / fixture["file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    args = MEDIA_KINDS[fixture["media"]]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args, str(target)]
    run(cmd)
    log.append(f"{target.relative_to(REPO_ROOT)}\n    {reproducible('ffmpeg -y -hide_banner -loglevel error', args, target)}")


def build_stills(record: dict, log: list[str]) -> list[dict]:
    """Write the stills for one asset, in time order, with one marked as cover.

    Two rules from docs/index-format.md, section 5: `stills` is a timeline, so it
    is written in ascending `at_s`, and the cover is marked explicitly rather than
    being whichever entry comes first. The cover here is simply the highest score,
    which is what a real indexer is expected to do; a hand-picked cover would just
    be the same flag set by `overrides.yaml` instead.
    """
    fixture = record["_fixture"]
    # The palettes and captions are written alongside the specs, so they have to
    # be sorted with them or the still files would not match their own captions.
    ordered = sorted(
        zip(record["stills"], fixture["palettes"], fixture["captions"]),
        key=lambda row: row[0]["at_s"],
    )
    best = max(range(len(ordered)), key=lambda i: ordered[i][0]["score"]) if ordered else -1

    stills = []
    for index, (spec, palette, caption) in enumerate(ordered):
        relative = Path("stills") / record["id"] / f"{index + 1:02d}.jpg"
        target = INDEX_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "-size", STILL_SIZE,
            f"gradient:{palette[0]}-{palette[1]}",
            "-gravity", "southwest",
            "-pointsize", "17",
            "-fill", "#f0ece4",
            "-annotate", "+14+12", caption,
            "-quality", STILL_QUALITY,
        ]
        run(["magick", *args, str(target)])
        log.append(f"{target.relative_to(REPO_ROOT)}\n    {reproducible('magick', args, target)}")
        stills.append({**spec, "path": relative.as_posix(), "cover": index == best})
    return stills


def write_index(records: list[dict]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    (INDEX_DIR / "videos").mkdir(parents=True, exist_ok=True)
    for record in records:
        target = INDEX_DIR / "videos" / f"{record['id']}.json"
        target.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")

    manifest = {
        "index_version": 1,
        "generated": "2026-09-28T20:00:00Z",
        "media_root": "../media",
        "asset_count": len(records),
        "generator": GENERATOR,
        "models": MODELS,
        "vocabulary": {"name": "vocabulary.yaml", "sha256": vocabulary_hash()},
        "errors": ERRORS,
        "note": ("Synthetic fixture index. The media it points at is FFmpeg test "
                 "patterns, not footage. Regenerate with fixtures/make_fixtures.py."),
    }
    (INDEX_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="verify the tools exist and report, without writing anything")
    args = parser.parse_args()

    for tool in ("ffmpeg", "magick"):
        if shutil.which(tool) is None:
            sys.stderr.write(f"{tool} is required to build the fixtures\n")
            return 1

    if args.check:
        print(f"ffmpeg and magick found; {len(ASSETS)} assets would be generated")
        return 0

    log: list[str] = []
    records = []
    for asset in ASSETS:
        record = {"asset_version": 1,
                  **{key: value for key, value in asset.items() if key != "_fixture"}}
        build_media(asset, log)
        # A synthetic mtime, taken from the date the record claims. The one asset
        # whose date is genuinely unknown gets a null mtime too: a real indexer
        # that fell back to mtime would have written at_source "file_mtime", so
        # "unknown" with an mtime would be a contradiction.
        mtime = None if asset["captured"]["at_source"] == "unknown" else asset["captured"]["at"]
        record["source"] = {
            "path": asset["_fixture"]["file"],
            "size_bytes": (MEDIA_DIR / asset["_fixture"]["file"]).stat().st_size,
            "mtime": mtime,
            "sha256": None,
        }
        record["stills"] = build_stills(asset, log)
        records.append(record)

    write_index(records)

    (ROOT / "GENERATED.txt").write_text(
        "# Generated by fixtures/make_fixtures.py — do not edit by hand.\n"
        "# One block per output file: the path, then the command that made it,\n"
        "# as run from the repository root.\n"
        "# Proposed by GitHub Copilot (DeepSeek V4 Flash); reviewed by a human.\n\n"
        + "\n\n".join(log) + "\n",
        encoding="utf-8",
    )

    still_count = sum(len(record["stills"]) for record in records)
    print(f"wrote {len(records)} asset records, {still_count} stills, "
          f"{len(MEDIA_KINDS)} media kinds under fixtures/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
