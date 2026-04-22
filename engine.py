import argparse
from concurrent.futures import Executor, ProcessPoolExecutor
import base64
import hashlib
import io
import math
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

# New imports for Audiovisual Feature
try:
    import numpy as np
    import scipy.io.wavfile as wavfile
    import scipy.signal as signal
    from scipy.fft import fft
    import imageio
    HAS_AV_LIBS = True
except ImportError:
    HAS_AV_LIBS = False


SHORT_RAMP = " .:-=+*#%@"
DETAILED_RAMP = (
    "$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\\|()1{}[]?-_+~<>i!lI;:,\"^`'. "
)


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


# ---------- ASCII MOVIE: dialogue overlay layer ----------

def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def smoothstep(t: float) -> float:
    # nice cinematic easing
    t = clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def wrap_text(s: str, maxw: int) -> List[str]:
    words = s.split()
    lines: List[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= maxw:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def typewriter(text: str, t: float) -> str:
    # t: 0..1
    n = int(len(text) * clamp(t, 0.0, 1.0))
    return text[:n]


def fade_mask(text: str, strength: float, rng: random.Random) -> str:
    # strength: 0..1 (1 = fully visible)
    out = []
    for ch in text:
        if ch == " ":
            out.append(" ")
        else:
            out.append(ch if rng.random() < strength else " ")
    return "".join(out)


@dataclass
class TextEvent:
    text: str
    start: int
    end: int
    x0: int
    y0: int
    x1: int
    y1: int
    mode: str = "type_fade"  # "type_fade" or "glitch"


def overlay_text(lines: List[List[str]], txt: str, x: int, y: int) -> None:
    h = len(lines)
    w = len(lines[0]) if h else 0
    if y < 0 or y >= h:
        return
    for i, ch in enumerate(txt):
        xx = x + i
        if 0 <= xx < w and ch != "":
            # don't overwrite with spaces (lets ASCII show through)
            if ch != " ":
                lines[y][xx] = ch


def render_dialogue_over_world(
    world_ascii: str,
    events: List[TextEvent],
    frame_idx: int,
    rng: random.Random,
) -> str:
    world_lines = world_ascii.splitlines()
    grid = [list(row) for row in world_lines]
    h = len(grid)
    w = len(grid[0]) if h else 0
    if h == 0 or w == 0:
        return world_ascii

    for ev in events:
        if frame_idx < ev.start or frame_idx > ev.end:
            continue

        dur = max(1, ev.end - ev.start)
        t = (frame_idx - ev.start) / dur
        t2 = smoothstep(t)

        x = int(lerp(ev.x0, ev.x1, t2))
        y = int(lerp(ev.y0, ev.y1, t2))

        txt = ev.text
        # keep it inside width
        if x < 0:
            txt = txt[-x:]
            x = 0
        if x >= w:
            continue
        txt = txt[: max(0, w - x)]

        if ev.mode == "type_fade":
            # type in first 40%, hold, fade out last 30%
            if t < 0.40:
                txt = typewriter(txt, t / 0.40)
                fade = 1.0
            elif t > 0.70:
                fade = clamp(1.0 - (t - 0.70) / 0.30)
            else:
                fade = 1.0
            txt = fade_mask(txt, fade, rng)

        elif ev.mode == "glitch":
            chars = list(txt)
            for i in range(len(chars)):
                if chars[i] != " " and rng.random() < 0.08:
                    chars[i] = rng.choice(list("#$%&@*+=-:/\\|"))
            txt = "".join(chars)

        overlay_text(grid, txt, x, y)

    return "\n".join("".join(row) for row in grid)


def build_events_from_seed(
    seed_text: str, total_frames: int, width: int, height: int, rng: random.Random
) -> List[TextEvent]:
    # Split into beats (short cinematic lines)
    raw = re.split(r"[.\n;]+", seed_text.strip())
    beats: List[str] = []
    for part in raw:
        part = part.strip()
        if not part:
            continue
        beats.extend(wrap_text(part, maxw=min(58, max(18, width - 12))))

    if not beats:
        beats = ["..."]

    # Spread beats across frames with overlap
    span = max(10, total_frames // max(1, len(beats)))  # how long each line lives
    t = 0
    events: List[TextEvent] = []

    for line in beats:
        start = t
        end = min(total_frames - 1, t + span + 12)

        # start near bottom, drift upward + sideways
        base_x = max(2, (width - len(line)) // 2 + rng.randint(-6, 6))
        base_y = max(2, height - 6 + rng.randint(-1, 1))

        x0 = clamp(base_x, 1, max(1, width - len(line) - 2))
        y0 = clamp(base_y, 1, height - 2)

        x1 = clamp(x0 + rng.randint(-10, 10), 1, max(1, width - len(line) - 2))
        y1 = clamp(y0 - rng.randint(3, 9), 1, height - 2)

        events.append(TextEvent(text=line, start=int(start), end=int(end), x0=int(x0), y0=int(y0), x1=int(x1), y1=int(y1)))
        t += span  # next beat

    return events


def build_cinematic_dialogue(scores: Dict[str, float], rng: random.Random) -> str:
    pools = {
        "impermanence": [
            "Everything dissolves eventually",
            "What stands will still fall",
            "Dust remembers every shape",
        ],
        "conflict": [
            "Pressure asks for a fracture",
            "Every order meets resistance",
            "Force writes in sharp edges",
        ],
        "hope": [
            "A small light survives the noise",
            "Something fragile chooses tomorrow",
            "After ruin, motion returns",
        ],
        "certainty": [
            "Conviction hardens into pattern",
            "The line insists on meaning",
            "One signal refuses to blur",
        ],
        "intensity": [
            "The field spikes, then burns bright",
            "Heat climbs through the static",
            "Edges ignite under strain",
        ],
        "isolation": [
            "Silence expands between signals",
            "A lone point drifts in the void",
            "Distance swallows the chorus",
        ],
        "self_focus": [
            "A center tries to hold",
            "Identity bends but does not vanish",
            "The core keeps speaking softly",
        ],
    }

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    strong = [k for k, v in ordered if v >= 0.18]
    chosen = strong[:3] if strong else [ordered[0][0]]

    lines: List[str] = []
    lines.append(rng.choice(["Signal online", "Scene bootstrapped", "Entropy sequence initiated"]))
    for key in chosen:
        lines.append(rng.choice(pools[key]))
    lines.append(rng.choice(["Observe the drift", "Hold the frame", "Watch what remains"]))
    return "; ".join(lines)


@dataclass
class SceneIntent:
    prompt: str
    tokens: List[str]
    mode: str = "organic"  # "radial" | "branching" | "geometric" | "organic"
    subject: str = "generic"  # "star" | "tree" | "sunflower" | "generic"
    decay: float = 0.0
    base_seed: int = 0


def parse_scene_intent(seed_text: str) -> SceneIntent:
    low = seed_text.lower()
    words = re.findall(r"[a-z']+", low)
    stop = {
        "show",
        "make",
        "render",
        "draw",
        "a",
        "an",
        "the",
        "please",
        "me",
        "to",
        "with",
        "of",
        "and",
        "in",
        "on",
        "at",
        "for",
        "this",
        "that",
        "it",
        "as",
        "be",
        "is",
        "are",
        "scene",
        "image",
        "art",
    }
    tokens = [w for w in words if w not in stop]
    if not tokens:
        tokens = ["form"]

    mode_terms = {
        "radial": {"star", "sun", "sunflower", "flower", "planet", "moon", "galaxy", "orb", "burst"},
        "branching": {"tree", "forest", "branch", "root", "vine", "plant", "bamboo", "oak", "leaf"},
        "geometric": {"city", "tower", "machine", "robot", "ship", "building", "temple", "castle", "bridge"},
        "terrain": {"terrain", "mountain", "rocky", "hills", "canyon", "desert", "cliffs", "peaks", "ground", "landscape"},
        "ocean": {"ocean", "sea", "water", "wave", "waves", "shore", "beach", "reef", "underwater", "marine", "tide", "coastal", "lake", "river"},
        "space_bg": {"space", "cosmos", "nebula", "galaxy", "stars", "asteroid", "void", "universe", "cosmic", "stellar", "orbit", "supernova"},
        "grassland": {"grass", "grassland", "meadow", "field", "plains", "savanna", "prairie", "steppe", "lawn", "pasture"},
        "forest_bg": {"forest", "jungle", "canopy", "woods", "woodland", "trees", "rainforest", "grove", "thicket", "foliage"},
        "sunset": {"sunset", "sunrise", "dusk", "dawn", "twilight", "horizon", "sky", "clouds", "golden", "evening", "morning"},
    }
    mode_scores = {"radial": 0.0, "branching": 0.0, "geometric": 0.0, "terrain": 0.0, "organic": 0.1,
                   "ocean": 0.0, "space_bg": 0.0, "grassland": 0.0, "forest_bg": 0.0, "sunset": 0.0}
    for t in tokens:
        for mode, vocab in mode_terms.items():
            if t in vocab:
                mode_scores[mode] += 1.0
        # Soft lexical hints improve generalization for unknown nouns.
        if t.endswith("flower") or t.endswith("sun") or t.endswith("star"):
            mode_scores["radial"] += 0.6
        if t.endswith("tree") or t.endswith("wood") or t.endswith("plant"):
            mode_scores["branching"] += 0.6
        if t.endswith("bot") or t.endswith("ship") or t.endswith("craft"):
            mode_scores["geometric"] += 0.6

    subject_aliases = {
        "sunflower": {"sunflower"},
        "tree": {"tree", "forest", "oak", "pine", "bamboo", "bonsai"},
        "star": {"star", "sun", "sunrise", "sunset", "moon", "planet", "nova", "supernova"},
    }
    subject_scores = {k: 0.0 for k in subject_aliases}
    for t in tokens:
        for subject, vocab in subject_aliases.items():
            if t in vocab:
                subject_scores[subject] += 1.0
        if t.endswith("flower"):
            subject_scores["sunflower"] += 0.55
        if t.endswith("tree") or t.endswith("wood"):
            subject_scores["tree"] += 0.55
        if t.endswith("star") or t.endswith("sun"):
            subject_scores["star"] += 0.55

    decay_words = {
        "dying",
        "decay",
        "decaying",
        "wither",
        "withering",
        "collapse",
        "collapsing",
        "dead",
        "death",
        "fading",
        "ruin",
        "ruined",
        "broken",
        "burning",
        "burnout",
    }
    growth_words = {"alive", "bloom", "blooming", "grow", "growing", "bright", "fresh", "new"}
    decay_hits = sum(1 for t in tokens if t in decay_words)
    growth_hits = sum(1 for t in tokens if t in growth_words)
    decay = clamp((decay_hits - 0.5 * growth_hits) / 3.0, 0.0, 1.0)

    prompt_key = " ".join(tokens)
    base_seed = int(hashlib.md5(prompt_key.encode("utf-8")).hexdigest()[:8], 16)
    mode = max(mode_scores.items(), key=lambda kv: kv[1])[0]
    best_subject, best_score = max(subject_scores.items(), key=lambda kv: kv[1])
    subject = best_subject if best_score > 0 else "generic"
    return SceneIntent(prompt=prompt_key, tokens=tokens, mode=mode, subject=subject, decay=decay, base_seed=base_seed)


def _new_canvas(w: int, h: int) -> List[List[str]]:
    return [[" " for _ in range(w)] for _ in range(h)]


def _put(canvas: List[List[str]], x: int, y: int, ch: str) -> None:
    h = len(canvas)
    w = len(canvas[0]) if h else 0
    if 0 <= x < w and 0 <= y < h and ch != " ":
        canvas[y][x] = ch


def _line(canvas: List[List[str]], x0: int, y0: int, x1: int, y1: int, ch: str) -> None:
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        _put(canvas, x, y, ch)
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def _sprite_from_canvas(canvas: List[List[str]]) -> List[str]:
    return ["".join(row) for row in canvas]


def _apply_decay(canvas: List[List[str]], amount: float, rng: random.Random) -> None:
    if amount <= 0:
        return
    for y, row in enumerate(canvas):
        for x, ch in enumerate(row):
            if ch == " ":
                continue
            if rng.random() < amount * 0.33:
                canvas[y][x] = " "
            elif rng.random() < amount * 0.27 and ch in {"@", "#", "*", "|", "/", "\\"}:
                canvas[y][x] = rng.choice([".", ":", "+", "-"])


def _radial_sprite(w: int, h: int, t: float, decay: float, rng: random.Random, token_count: int) -> List[str]:
    canvas = _new_canvas(w, h)
    cx = w // 2 + rng.randint(-1, 1)
    cy = h // 2 + rng.randint(-1, 1)
    inner = max(1, min(w, h) // 9)
    outer = max(inner + 2, min(w, h) // 2 - 1)
    outer = max(inner + 1, int(outer * (1.0 - 0.25 * decay * t)))
    rays = max(8, min(24, 8 + token_count * 2))
    for i in range(rays):
        ang = (2.0 * math.pi * i) / rays + rng.uniform(-0.04, 0.04)
        x1 = int(cx + outer * math.cos(ang))
        y1 = int(cy + outer * math.sin(ang) * 0.6)
        _line(canvas, cx, cy, x1, y1, rng.choice(["*", "+", "#"]))
    for i in range(24):
        ang = (2.0 * math.pi * i) / 24.0
        x = int(cx + inner * math.cos(ang))
        y = int(cy + inner * math.sin(ang) * 0.65)
        _put(canvas, x, y, "@")
    _put(canvas, cx, cy, "@")
    _apply_decay(canvas, decay * (0.3 + 0.7 * t), rng)
    return _sprite_from_canvas(canvas)


def _branch(
    canvas: List[List[str]],
    x: float,
    y: float,
    length: float,
    angle: float,
    depth: int,
    rng: random.Random,
) -> None:
    if depth <= 0 or length < 1.2:
        return
    x1 = x + math.cos(angle) * length
    y1 = y - math.sin(angle) * length
    ch = "|" if abs(math.cos(angle)) < 0.4 else ("/" if math.cos(angle) < 0 else "\\")
    _line(canvas, int(round(x)), int(round(y)), int(round(x1)), int(round(y1)), ch)
    forks = 2 if depth > 1 else 1
    for _ in range(forks):
        bend = rng.uniform(0.28, 0.58)
        sign = -1 if rng.random() < 0.5 else 1
        _branch(canvas, x1, y1, length * rng.uniform(0.62, 0.78), angle + (sign * bend), depth - 1, rng)
    if depth <= 2:
        for _ in range(3):
            lx = int(round(x1 + rng.uniform(-1.5, 1.5)))
            ly = int(round(y1 + rng.uniform(-1.5, 1.5)))
            _put(canvas, lx, ly, rng.choice(["*", ".", "+"]))


def _branching_sprite(w: int, h: int, t: float, decay: float, rng: random.Random, token_count: int) -> List[str]:
    canvas = _new_canvas(w, h)
    bx = w // 2 + rng.randint(-1, 1)
    by = h - 2
    trunk = max(4, int(h * (0.42 - 0.18 * decay * t)))
    _line(canvas, bx, by, bx, by - trunk, "|")
    depth = max(3, min(6, 2 + token_count // 2))
    _branch(canvas, bx, by - trunk, length=max(3.0, h * 0.22), angle=math.pi / 2, depth=depth, rng=rng)
    _apply_decay(canvas, decay * (0.25 + 0.85 * t), rng)
    return _sprite_from_canvas(canvas)


def _geometric_sprite(w: int, h: int, t: float, decay: float, rng: random.Random, token_count: int) -> List[str]:
    canvas = _new_canvas(w, h)
    cx = w // 2
    cy = h // 2
    box_w = max(6, int(w * 0.45))
    box_h = max(4, int(h * 0.35))
    x0 = max(1, cx - box_w // 2)
    y0 = max(1, cy - box_h // 2)
    x1 = min(w - 2, x0 + box_w)
    y1 = min(h - 2, y0 + box_h)
    _line(canvas, x0, y0, x1, y0, "#")
    _line(canvas, x1, y0, x1, y1, "#")
    _line(canvas, x1, y1, x0, y1, "#")
    _line(canvas, x0, y1, x0, y0, "#")
    levels = max(2, min(7, token_count + 1))
    for i in range(1, levels + 1):
        px = int(lerp(x0 + 1, x1 - 1, i / (levels + 1)))
        _line(canvas, px, y0 + 1, px, y1 - 1, rng.choice(["|", ":", "+"]))
    _line(canvas, x0, y0, x1, y1, "/")
    _line(canvas, x1, y0, x0, y1, "\\")
    _apply_decay(canvas, decay * (0.2 + 0.8 * t), rng)
    return _sprite_from_canvas(canvas)


def _organic_sprite(w: int, h: int, t: float, decay: float, rng: random.Random, token_count: int) -> List[str]:
    canvas = _new_canvas(w, h)
    blobs = max(4, min(10, 3 + token_count))
    field = [[0.0 for _ in range(w)] for _ in range(h)]
    for _ in range(blobs):
        cx = rng.uniform(w * 0.2, w * 0.8)
        cy = rng.uniform(h * 0.2, h * 0.8)
        rad = rng.uniform(max(2.0, min(w, h) * 0.12), max(3.0, min(w, h) * 0.32))
        amp = rng.uniform(0.6, 1.35)
        inv2 = 1.0 / max(0.01, 2.0 * rad * rad)
        for y in range(h):
            for x in range(w):
                dx = x - cx
                dy = (y - cy) * 1.35
                field[y][x] += amp * math.exp(-(dx * dx + dy * dy) * inv2)
    threshold = 0.42 + (0.22 * decay * t)
    for y in range(h):
        for x in range(w):
            v = field[y][x]
            if v < threshold:
                continue
            if v > threshold * 2.0:
                ch = "@"
            elif v > threshold * 1.6:
                ch = "#"
            elif v > threshold * 1.35:
                ch = "*"
            elif v > threshold * 1.15:
                ch = "+"
            else:
                ch = "."
            canvas[y][x] = ch
    _apply_decay(canvas, decay * (0.2 + 0.9 * t), rng)
    return _sprite_from_canvas(canvas)


def _star_subject_sprite(w: int, h: int, t: float, decay: float, rng: random.Random, token_count: int) -> List[str]:
    canvas = _new_canvas(w, h)
    cx = w // 2 + rng.randint(-1, 1)
    cy = h // 2 + rng.randint(-1, 1)
    core = max(2, min(w, h) // 7)
    ray_len = max(core + 3, int(min(w, h) * (0.34 - 0.12 * decay * t)))
    rays = max(10, min(20, 10 + token_count))

    for i in range(rays):
        ang = (2.0 * math.pi * i) / rays
        x1 = int(round(cx + ray_len * math.cos(ang)))
        y1 = int(round(cy + ray_len * math.sin(ang) * 0.65))
        _line(canvas, cx, cy, x1, y1, "*" if i % 2 == 0 else "+")

    for y in range(h):
        for x in range(w):
            dx = x - cx
            dy = (y - cy) * 1.4
            d = math.sqrt(dx * dx + dy * dy)
            if d <= core * 0.7:
                canvas[y][x] = "@"
            elif d <= core:
                canvas[y][x] = "#"
            elif d <= core + 1.2 and rng.random() < 0.42:
                canvas[y][x] = "+"

    _apply_decay(canvas, decay * (0.3 + 0.8 * t), rng)
    return _sprite_from_canvas(canvas)


def _tree_subject_sprite(w: int, h: int, t: float, decay: float, rng: random.Random, token_count: int) -> List[str]:
    canvas = _new_canvas(w, h)
    bx = w // 2 + rng.randint(-1, 1)
    by = h - 2
    trunk_h = max(4, int(h * (0.48 - 0.2 * decay * t)))
    top_y = max(1, by - trunk_h)
    _line(canvas, bx, by, bx, top_y, "|")
    if bx > 0:
        _line(canvas, bx - 1, by, bx - 1, top_y + 1, "|")

    crown_r = max(3, int(min(w, h) * 0.25))
    crown_cy = max(2, top_y - max(1, crown_r // 3))
    for y in range(max(0, crown_cy - crown_r), min(h, crown_cy + crown_r + 1)):
        for x in range(max(0, bx - crown_r - 2), min(w, bx + crown_r + 3)):
            dx = x - bx
            dy = (y - crown_cy) * 1.2
            if (dx * dx + dy * dy) <= (crown_r * crown_r):
                if rng.random() < 0.62:
                    canvas[y][x] = rng.choice(["*", "#", "+", "."])

    branch_depth = max(2, min(5, 2 + token_count // 3))
    _branch(canvas, bx, top_y + 1, length=max(3.0, h * 0.18), angle=math.pi / 2, depth=branch_depth, rng=rng)
    _apply_decay(canvas, decay * (0.22 + 0.85 * t), rng)
    return _sprite_from_canvas(canvas)


def _sunflower_subject_sprite(w: int, h: int, t: float, decay: float, rng: random.Random, token_count: int) -> List[str]:
    canvas = _new_canvas(w, h)
    cx = w // 2 + rng.randint(-1, 1)
    cy = max(3, int(h * 0.38))
    petal_r = max(3, int(min(w, h) * 0.24))
    center_r = max(2, petal_r // 2)
    petals = max(10, min(20, 12 + token_count))

    for i in range(petals):
        ang = (2.0 * math.pi * i) / petals
        px = int(round(cx + petal_r * math.cos(ang)))
        py = int(round(cy + petal_r * math.sin(ang) * 0.7))
        _put(canvas, px, py, "*")
        if rng.random() < 0.4:
            _put(canvas, px + (1 if px >= cx else -1), py, "+")

    for y in range(max(0, cy - center_r), min(h, cy + center_r + 1)):
        for x in range(max(0, cx - center_r - 1), min(w, cx + center_r + 2)):
            dx = x - cx
            dy = (y - cy) * 1.2
            if (dx * dx + dy * dy) <= (center_r * center_r):
                canvas[y][x] = "@" if (dx * dx + dy * dy) < (center_r * center_r * 0.45) else "#"

    stem_bottom = h - 2
    _line(canvas, cx, cy + center_r, cx, stem_bottom, "|")
    leaf_y = min(stem_bottom - 1, cy + center_r + max(2, (stem_bottom - cy) // 3))
    _line(canvas, cx, leaf_y, max(1, cx - max(2, w // 8)), min(stem_bottom, leaf_y + 2), "/")
    _line(canvas, cx, leaf_y + 1, min(w - 2, cx + max(2, w // 8)), min(stem_bottom, leaf_y + 3), "\\")

    _apply_decay(canvas, decay * (0.24 + 0.8 * t), rng)
    return _sprite_from_canvas(canvas)


def _trim_sprite(sprite: List[str]) -> List[str]:
    if not sprite:
        return sprite
    rows = [i for i, row in enumerate(sprite) if row.strip()]
    if not rows:
        return [" "]
    top, bottom = rows[0], rows[-1]
    block = sprite[top : bottom + 1]
    left = None
    right = None
    for row in block:
        for i, ch in enumerate(row):
            if ch != " ":
                left = i if left is None else min(left, i)
                right = i if right is None else max(right, i)
    if left is None or right is None:
        return [" "]
    return [row[left : right + 1] for row in block]


def _scale_sprite(sprite: List[str], out_w: int, out_h: int) -> List[str]:
    if not sprite:
        return [" "]
    in_h = len(sprite)
    in_w = max((len(row) for row in sprite), default=1)
    if in_w <= 0 or in_h <= 0 or out_w <= 0 or out_h <= 0:
        return [" "]
    padded = [row.ljust(in_w) for row in sprite]
    out: List[str] = []
    for y in range(out_h):
        sy = min(in_h - 1, int(y * in_h / out_h))
        row_chars: List[str] = []
        for x in range(out_w):
            sx = min(in_w - 1, int(x * in_w / out_w))
            row_chars.append(padded[sy][sx])
        out.append("".join(row_chars))
    return _trim_sprite(out)


def _build_image_prompt(seed_text: str) -> str:
    return (
        "Create a centered single-subject monochrome silhouette or line-art scene for: "
        f"'{seed_text}'. "
        "High contrast, plain background, no text, no watermark, clear shape boundaries."
    )


def _fetch_openai_image_bytes(prompt: str, model: str, size: str, timeout_s: float) -> bytes | None:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import requests  # type: ignore
    except Exception:
        return None

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "prompt": prompt, "size": size, "n": 1, "response_format": "b64_json"}
    endpoints = (
        "https://api.openai.com/v1/images",
        "https://api.openai.com/v1/images/generations",
    )

    for url in endpoints:
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout_s)
            if resp.status_code >= 400:
                continue
            data = resp.json()
            arr = data.get("data")
            if not arr or not isinstance(arr, list):
                continue
            first = arr[0] or {}
            b64 = first.get("b64_json")
            if b64:
                return base64.b64decode(b64)
            image_url = first.get("url")
            if image_url:
                img_resp = requests.get(image_url, timeout=timeout_s)
                if img_resp.status_code < 400:
                    return img_resp.content
        except Exception:
            continue
    return None


def _edge_ascii_from_image_bytes(image_bytes: bytes, max_w: int, max_h: int) -> List[str]:
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return []

    img = Image.open(io.BytesIO(image_bytes))
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        rgba = rgba.crop(bbox)
        alpha = alpha.crop(bbox)

    src_w, src_h = rgba.size
    if src_w <= 0 or src_h <= 0:
        return []

    # Character cells are taller than they are wide; compensate to preserve shape.
    scale = min(max_w / src_w, max_h / max(1.0, src_h * 0.55))
    out_w = max(10, min(max_w, int(src_w * scale)))
    out_h = max(6, min(max_h, int(src_h * scale * 0.55)))

    gray = rgba.convert("L").resize((out_w, out_h), Image.Resampling.LANCZOS)
    alpha_small = alpha.resize((out_w, out_h), Image.Resampling.LANCZOS)
    g = list(gray.getdata())
    a = list(alpha_small.getdata())
    mat_g = [g[i * out_w : (i + 1) * out_w] for i in range(out_h)]
    mat_a = [a[i * out_w : (i + 1) * out_w] for i in range(out_h)]

    sobel_chars = " .:-=+*#%@"
    mags = [[0.0 for _ in range(out_w)] for _ in range(out_h)]
    max_mag = 1.0
    for y in range(1, out_h - 1):
        for x in range(1, out_w - 1):
            p00 = mat_g[y - 1][x - 1]
            p01 = mat_g[y - 1][x]
            p02 = mat_g[y - 1][x + 1]
            p10 = mat_g[y][x - 1]
            p12 = mat_g[y][x + 1]
            p20 = mat_g[y + 1][x - 1]
            p21 = mat_g[y + 1][x]
            p22 = mat_g[y + 1][x + 1]
            gx = -p00 - (2 * p10) - p20 + p02 + (2 * p12) + p22
            gy = -p00 - (2 * p01) - p02 + p20 + (2 * p21) + p22
            mag = abs(gx) + abs(gy)
            mags[y][x] = mag
            max_mag = max(max_mag, mag)

    lines: List[str] = []
    for y in range(out_h):
        row_chars: List[str] = []
        for x in range(out_w):
            alpha_px = mat_a[y][x]
            if alpha_px < 20:
                row_chars.append(" ")
                continue

            edge = mags[y][x] / max_mag
            lum = mat_g[y][x] / 255.0
            if edge > 0.14:
                idx = min(len(sobel_chars) - 1, int(edge * (len(sobel_chars) - 1)))
                row_chars.append(sobel_chars[idx])
            else:
                # Fill silhouette softly so objects remain legible.
                row_chars.append("." if lum < 0.72 else " ")
        lines.append("".join(row_chars))
    return _trim_sprite(lines)


def build_model_subject_sprite(
    seed_text: str,
    intent: SceneIntent,
    max_w: int,
    max_h: int,
    model: str,
    image_size: str,
    timeout_s: float,
    cache_dir: str,
) -> Tuple[List[str] | None, str]:
    prompt = _build_image_prompt(seed_text)
    cache_root = Path(cache_dir)
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    cache_key = hashlib.md5(f"{prompt}|{model}|{image_size}|{max_w}|{max_h}".encode("utf-8")).hexdigest()[:16]
    cache_txt = cache_root / f"subject_{cache_key}.txt"
    if cache_txt.exists():
        try:
            cached = cache_txt.read_text(encoding="utf-8").splitlines()
            if cached:
                return cached, "cache"
        except Exception:
            pass

    image_bytes = _fetch_openai_image_bytes(prompt=prompt, model=model, size=image_size, timeout_s=timeout_s)
    if not image_bytes:
        return None, "unavailable"
    sprite = _edge_ascii_from_image_bytes(image_bytes=image_bytes, max_w=max_w, max_h=max_h)
    if not sprite:
        return None, "conversion_failed"

    # Blend with mode-specific generator to retain style motion from the core engine.
    if intent.mode == "branching":
        sprite = _scale_sprite(sprite, out_w=max_w, out_h=max_h)
    else:
        sprite = _scale_sprite(sprite, out_w=max_w, out_h=max(6, int(max_h * 0.82)))

    try:
        cache_txt.write_text("\n".join(sprite), encoding="utf-8")
    except Exception:
        pass
    return sprite, "api"


def overlay_sprite(lines: List[List[str]], sprite: List[str], x: int, y: int) -> None:
    h = len(lines)
    w = len(lines[0]) if h else 0
    for sy, row in enumerate(sprite):
        yy = y + sy
        if yy < 0 or yy >= h:
            continue
        for sx, ch in enumerate(row):
            xx = x + sx
            if 0 <= xx < w and ch != " ":
                lines[yy][xx] = ch


def render_subject_over_world(
    world_ascii: str,
    intent: SceneIntent,
    frame_idx: int,
    total_frames: int,
    rng: random.Random,
    model_sprite: List[str] | None = None,
) -> str:
    world_lines = world_ascii.splitlines()
    grid = [list(row) for row in world_lines]
    h = len(grid)
    w = len(grid[0]) if h else 0
    if h == 0 or w == 0:
        return world_ascii

    t = frame_idx / max(1, total_frames - 1)
    obj_w = max(16, min(w - 2, int(w * 0.38)))
    obj_h = max(8, min(h - 2, int(h * (0.42 if intent.mode == "branching" else 0.30))))
    frame_seed = intent.base_seed ^ (frame_idx * 1315423911)
    local_rng = random.Random(frame_seed)

    if model_sprite:
        sprite = _scale_sprite(model_sprite, out_w=obj_w, out_h=obj_h)
        if intent.decay > 0.01:
            decay_canvas = [list(row) for row in sprite]
            _apply_decay(decay_canvas, intent.decay * (0.2 + 0.8 * t), local_rng)
            sprite = _sprite_from_canvas(decay_canvas)
    elif intent.subject == "star":
        sprite = _star_subject_sprite(
            obj_w,
            obj_h,
            t=t,
            decay=intent.decay,
            rng=local_rng,
            token_count=len(intent.tokens),
        )
    elif intent.subject == "tree":
        sprite = _tree_subject_sprite(
            obj_w,
            obj_h,
            t=t,
            decay=intent.decay,
            rng=local_rng,
            token_count=len(intent.tokens),
        )
    elif intent.subject == "sunflower":
        sprite = _sunflower_subject_sprite(
            obj_w,
            obj_h,
            t=t,
            decay=intent.decay,
            rng=local_rng,
            token_count=len(intent.tokens),
        )
    elif intent.mode == "radial":
        sprite = _radial_sprite(obj_w, obj_h, t=t, decay=intent.decay, rng=local_rng, token_count=len(intent.tokens))
    elif intent.mode == "branching":
        sprite = _branching_sprite(obj_w, obj_h, t=t, decay=intent.decay, rng=local_rng, token_count=len(intent.tokens))
    elif intent.mode == "geometric":
        sprite = _geometric_sprite(obj_w, obj_h, t=t, decay=intent.decay, rng=local_rng, token_count=len(intent.tokens))
    else:
        sprite = _organic_sprite(obj_w, obj_h, t=t, decay=intent.decay, rng=local_rng, token_count=len(intent.tokens))

    obj_h = len(sprite)
    obj_w = max((len(row) for row in sprite), default=0)
    x = max(0, (w - obj_w) // 2)
    if intent.mode == "branching":
        y = max(0, h - obj_h - 2)
    else:
        base_y = max(0, (h // 2) - (obj_h // 2))
        if intent.decay > 0.05:
            t = frame_idx / max(1, total_frames - 1)
            y = int(lerp(base_y - 1, base_y + 3, t))
            y = max(0, min(h - obj_h, y))
        else:
            y = base_y

    overlay_sprite(grid, sprite, x, y)
    return "\n".join("".join(row) for row in grid)


def genome_signature(genome: Sequence["Primitive"]) -> Tuple[Tuple[str, int, int, int, int, int], ...]:
    """Stable, coarse signature so unchanged elites can reuse rendered metrics."""
    return tuple(
        (
            p.kind,
            int(round(p.x * 100)),
            int(round(p.y * 100)),
            int(round(p.size * 100)),
            int(round(p.rot * 10000)),
            int(round(p.intensity * 1000)),
        )
        for p in genome
    )


@dataclass
class Primitive:
    kind: str
    x: float
    y: float
    size: float
    rot: float
    intensity: float


class SemanticAnalyzer:
    """Rule-based semantic scoring (0..1)."""

    def __init__(self) -> None:
        self.lexicons = {
            "impermanence": {
                "dust", "fade", "gone", "end", "die", "dying", "death", "dead",
                "nothing", "ruin", "ruined", "ruins", "decay", "decaying",
                "time", "temporary", "lost", "fall", "falling", "fallen",
                "fleeting", "transient", "vanish", "vanishing", "dissolve",
                "drift", "drifting", "passing", "ephemeral", "mortal",
                "wither", "withering", "crumble", "crumbling", "ash", "ashes",
                "ghost", "echo", "memory", "forget", "forgotten", "shadow",
                "mist", "fog", "fading", "erode", "eroding", "slip", "slipping",
                "collapse", "collapsing", "wreck", "wreckage", "debris",
                "remnant", "remains", "afterglow", "dusk", "twilight", "sunset",
            },
            "conflict": {
                "war", "fight", "struggle", "break", "destroy", "against", "clash",
                "chaos", "control", "force", "tear", "tension", "shatter", "crack",
                "fracture", "collide", "collision", "battle", "storm", "storming",
                "rage", "raging", "violent", "violence", "crash", "turbulent",
                "turbulence", "friction", "disruption", "disrupt", "turmoil",
                "conflict", "enemy", "opposition", "resist", "resistance",
                "pressure", "threat", "danger", "crisis", "catastrophe",
            },
            "hope": {
                "survive", "light", "rebuild", "new", "start", "live", "seed",
                "dawn", "future", "hope", "persist", "create", "warm", "warmth",
                "glow", "glowing", "rise", "rising", "bloom", "blooming", "grow",
                "growing", "gentle", "soft", "tender", "peace", "peaceful", "calm",
                "serene", "serenity", "tranquil", "quiet", "still", "stillness",
                "breathe", "breathing", "float", "floating", "flow", "flowing",
                "open", "opening", "clear", "clarity", "bright", "brightness",
                "shine", "shining", "radiant", "golden", "spring", "life",
                "beauty", "beautiful", "wonder", "pure", "heal", "healing",
                "dream", "dreaming", "wish", "love", "harmony", "balance",
            },
            "certainty": {
                "always", "forever", "must", "truth", "will", "absolute", "law",
                "inevitable", "fact", "certain", "bound", "solid", "fixed", "stable",
                "steady", "constant", "eternal", "infinite", "endless", "immovable",
                "permanent", "definite", "clear", "sharp", "precise", "exact",
                "foundation", "ground", "structure", "order", "system", "pattern",
            },
            "intensity": {
                "fire", "burn", "burning", "explode", "explosion", "scream", "crush",
                "power", "powerful", "blood", "rage", "hard", "electric", "surge",
                "pulse", "pulsing", "vibrate", "vibrating", "intense", "blazing",
                "blaze", "spark", "sparking", "ignite", "igniting", "energy",
                "energetic", "wild", "fierce", "ferocious", "massive", "huge",
                "vast", "extreme", "maximum", "peak", "apex", "star", "stellar",
                "nuclear", "cosmic", "supernova", "inferno", "unstoppable",
                "overwhelming", "tremendous", "colossal", "titanic", "roar",
            },
            "isolation": {
                "alone", "void", "empty", "cold", "silence", "space", "dark",
                "single", "only", "apart", "solitude", "lonely", "loneliness",
                "distant", "distance", "far", "remote", "desolate", "desolation",
                "abandoned", "forgotten", "hollow", "barren", "bleak", "sparse",
                "minimal", "minimalist", "cosmic", "universe", "abyss",
                "depth", "deep", "night", "midnight", "black", "numb",
                "separated", "disconnected", "adrift", "nowhere", "endless",
                "city", "urban", "crowd", "anonymous", "invisible", "unseen",
            },
            "self_focus": {
                "i", "me", "my", "self", "identity", "mind", "soul", "core",
                "center", "within", "inner", "inside", "personal", "private",
                "introspect", "reflect", "reflection", "consciousness", "aware",
                "awareness", "ego", "being", "existence", "exist", "am", "feel",
                "heart", "spirit", "psyche", "thought", "thoughts", "emotion",
            },
        }
        # Extra common words people type — mapped to nearest semantic bucket
        self._extras = {
            # impermanence
            "rain": "impermanence", "rainy": "impermanence", "rain": "impermanence",
            "wind": "impermanence", "windy": "impermanence",
            "cloud": "impermanence", "cloudy": "impermanence",
            "snow": "impermanence", "winter": "impermanence",
            "autumn": "impermanence", "fall": "impermanence",
            "leaf": "impermanence", "leaves": "impermanence",
            "smoke": "impermanence", "steam": "impermanence",
            "wave": "impermanence", "waves": "impermanence",
            "river": "impermanence", "stream": "impermanence",
            "sand": "impermanence", "tide": "impermanence",
            "afternoon": "impermanence", "evening": "impermanence",
            "melancholy": "impermanence", "melancholic": "impermanence",
            "nostalgic": "impermanence", "nostalgia": "impermanence",
            "robot": "certainty", "machine": "certainty", "metal": "certainty",
            # conflict
            "thunder": "conflict", "lightning": "conflict",
            "earthquake": "conflict", "volcano": "conflict",
            "hurricane": "conflict", "tornado": "conflict",
            "fire": "conflict", "burning": "conflict",
            "war": "conflict", "battle": "conflict",
            "dark": "conflict", "darkness": "conflict",
            "angry": "conflict", "anger": "conflict",
            "fear": "conflict", "scary": "conflict", "horror": "conflict",
            "pain": "conflict", "hurt": "conflict", "wound": "conflict",
            # hope
            "sun": "hope", "sunny": "hope", "sunshine": "hope",
            "morning": "hope", "sunrise": "hope",
            "flower": "hope", "flowers": "hope", "rose": "hope",
            "bird": "hope", "birds": "hope", "singing": "hope",
            "green": "hope", "nature": "hope",
            "happy": "hope", "happiness": "hope", "joy": "hope", "joyful": "hope",
            "love": "hope", "loving": "hope", "beautiful": "hope",
            "soft": "hope", "gentle": "hope", "warm": "hope", "warmth": "hope",
            "safe": "hope", "safety": "hope", "home": "hope",
            "spring": "hope", "summer": "hope",
            "ocean": "hope", "sea": "hope", "water": "hope",
            "sky": "hope", "blue": "hope",
            # intensity
            "star": "intensity", "stars": "intensity", "galaxy": "intensity",
            "explosion": "intensity", "exploding": "intensity",
            "loud": "intensity", "noise": "intensity", "roar": "intensity",
            "fast": "intensity", "speed": "intensity", "rush": "intensity",
            "electric": "intensity", "lightning": "intensity",
            "massive": "intensity", "giant": "intensity", "huge": "intensity",
            "neon": "intensity", "bright": "intensity", "blinding": "intensity",
            "city": "intensity", "urban": "intensity", "tokyo": "intensity",
            "night": "intensity", "midnight": "intensity",
            # isolation
            "alone": "isolation", "lonely": "isolation", "loneliness": "isolation",
            "desert": "isolation", "empty": "isolation", "barren": "isolation",
            "space": "isolation", "cosmos": "isolation", "universe": "isolation",
            "cold": "isolation", "frozen": "isolation", "ice": "isolation",
            "silent": "isolation", "quiet": "isolation", "still": "isolation",
            "lost": "isolation", "wandering": "isolation", "walking": "isolation",
            "robot": "isolation", "android": "isolation",
            "waiting": "isolation", "forgotten": "isolation",
            # self_focus
            "i": "self_focus", "me": "self_focus", "my": "self_focus",
            "myself": "self_focus", "dream": "self_focus", "dreaming": "self_focus",
            "memory": "self_focus", "remember": "self_focus", "remembering": "self_focus",
            "thinking": "self_focus", "feeling": "self_focus", "mind": "self_focus",
        }
        self.negations = {"not", "no", "never", "without", "false", "illusion"}
        self.hedges = {"maybe", "perhaps", "might", "could", "possibly"}

    def _fuzzy_match(self, word: str) -> Dict[str, float]:
        """Stem/suffix match when exact word not in lexicon."""
        hits: Dict[str, float] = {}
        # Common suffixes to strip and retry
        suffixes = ["ing", "ed", "er", "est", "ly", "ness", "tion", "ion", "s", "es"]
        candidates = [word]
        for suf in suffixes:
            if word.endswith(suf) and len(word) - len(suf) >= 3:
                candidates.append(word[: -len(suf)])
        # Also try adding common suffixes to catch base→derived
        for cat, lexicon in self.lexicons.items():
            for cand in candidates[1:]:  # skip original — already checked exact
                if cand in lexicon:
                    hits[cat] = hits.get(cat, 0) + 0.6  # partial credit
                    break
        return hits

    def analyze(self, text: str) -> Dict[str, float]:
        words = re.findall(r"\b\w+\b", text.lower())
        wc = len(words)
        scores = {k: 0.0 for k in self.lexicons}
        if wc == 0:
            return scores

        for i, word in enumerate(words):
            left = words[max(0, i - 2) : i]
            negated = any(n in left for n in self.negations)
            matched = False
            for category, lexicon in self.lexicons.items():
                if word in lexicon:
                    scores[category] += 0.5 if negated else 1.0
                    matched = True
            # Extras lookup for common words not in main lexicon
            if not matched and word in self._extras:
                cat = self._extras[word]
                scores[cat] += 0.5 if negated else 0.8
                matched = True
            # Fuzzy stem fallback last resort
            if not matched:
                for category, val in self._fuzzy_match(word).items():
                    scores[category] += (val * 0.5) if negated else val

        norm = max(1, int(wc * 0.18))
        for key in scores:
            scores[key] = clamp(scores[key] / norm)

        caps_ratio = sum(1 for c in text if c.isupper()) / max(1, len(text))
        exclamations = text.count("!")
        questions = text.count("?")
        hedge_hits = sum(1 for w in words if w in self.hedges)

        scores["intensity"] = clamp(scores["intensity"] + exclamations * 0.08 + caps_ratio * 0.6)
        scores["certainty"] = clamp(scores["certainty"] - questions * 0.08 - hedge_hits * 0.06)
        if len(set(words)) < wc:
            scores["certainty"] = clamp(scores["certainty"] + 0.1)
        if wc < 5:
            scores["isolation"] = clamp(scores["isolation"] + 0.15)

        # If nothing matched at all, seed baseline so output isn't totally flat
        total = sum(scores.values())
        if total < 0.3:
            scores["impermanence"] = 0.3
            scores["hope"] = 0.25
            scores["intensity"] = 0.2

        return scores


class PrimitiveRenderer:
    """Rasterizes primitive genomes into a density grid and ASCII output."""

    def __init__(self, width: int = 120, height: int = 45, ramp: str = SHORT_RAMP, contrast: int = 0) -> None:
        self.w = width
        self.h = height
        self.ramp = ramp
        self.contrast = max(-10, min(10, contrast))

    def _plot(self, grid: List[List[float]], x: int, y: int, ink: float) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            grid[y][x] += ink

    def _plot_soft(self, grid: List[List[float]], fx: float, fy: float, ink: float, radius: float) -> None:
        r = max(1.0, radius)
        min_x = int(max(0, math.floor(fx - r)))
        max_x = int(min(self.w - 1, math.ceil(fx + r)))
        min_y = int(max(0, math.floor(fy - r)))
        max_y = int(min(self.h - 1, math.ceil(fy + r)))
        inv = 1.0 / r
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                dx = x - fx
                dy = (y - fy) * 1.6
                d = math.sqrt(dx * dx + dy * dy)
                if d <= r:
                    falloff = 1.0 - (d * inv)
                    grid[y][x] += ink * (0.35 + 0.65 * falloff)

    def _draw_line(self, grid: List[List[float]], p: Primitive) -> None:
        half = max(1.0, p.size * 0.5)
        dx = math.cos(p.rot) * half
        dy = math.sin(p.rot) * half
        x0, y0 = p.x - dx, p.y - dy
        x1, y1 = p.x + dx, p.y + dy
        thickness = max(1.0, p.size * 0.08)
        steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        for i in range(steps + 1):
            t = i / max(1, steps)
            fx = x0 + (x1 - x0) * t
            fy = y0 + (y1 - y0) * t
            self._plot_soft(grid, fx, fy, p.intensity, thickness)

    def _draw_circle(self, grid: List[List[float]], p: Primitive) -> None:
        radius = max(1.0, p.size * 0.45)
        min_x = int(max(0, p.x - radius))
        max_x = int(min(self.w - 1, p.x + radius))
        min_y = int(max(0, p.y - radius))
        max_y = int(min(self.h - 1, p.y + radius))
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                dx = (x - p.x) / radius
                dy = ((y - p.y) * 1.6) / radius
                d2 = dx * dx + dy * dy
                if d2 <= 1.0:
                    ring = abs(1.0 - d2)
                    grid[y][x] += p.intensity * (0.25 + 0.75 * ring)

    def _draw_rect(self, grid: List[List[float]], p: Primitive) -> None:
        hw = max(1.0, p.size * 0.5)
        hh = max(1.0, p.size * 0.18)
        cos_r, sin_r = math.cos(p.rot), math.sin(p.rot)
        min_x = int(max(0, p.x - hw - hh))
        max_x = int(min(self.w - 1, p.x + hw + hh))
        min_y = int(max(0, p.y - hw - hh))
        max_y = int(min(self.h - 1, p.y + hw + hh))
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                lx = (x - p.x) * cos_r + (y - p.y) * sin_r
                ly = -(x - p.x) * sin_r + (y - p.y) * cos_r
                if abs(lx) <= hw and abs(ly) <= hh:
                    edge_prox = 1.0 - max(abs(lx) / hw, abs(ly) / hh)
                    grid[y][x] += p.intensity * (0.25 + 0.75 * clamp(edge_prox))

    def _smooth(self, grid: List[List[float]], passes: int = 1) -> None:
        for _ in range(passes):
            out = [[0.0 for _ in range(self.w)] for _ in range(self.h)]
            for y in range(self.h):
                for x in range(self.w):
                    acc = 0.0
                    weight = 0.0
                    for ny in range(max(0, y - 1), min(self.h, y + 2)):
                        for nx in range(max(0, x - 1), min(self.w, x + 2)):
                            w = 1.6 if (nx == x and ny == y) else 1.0
                            acc += grid[ny][nx] * w
                            weight += w
                    out[y][x] = acc / weight
            for y in range(self.h):
                for x in range(self.w):
                    grid[y][x] = out[y][x]

    def render_density(self, genome: Sequence[Primitive]) -> List[List[float]]:
        grid = [[0.0 for _ in range(self.w)] for _ in range(self.h)]
        for p in genome:
            if p.kind == "line":
                self._draw_line(grid, p)
            elif p.kind == "circle":
                self._draw_circle(grid, p)
            else:
                self._draw_rect(grid, p)

        self._smooth(grid, passes=1)
        max_val = max(max(row) for row in grid)
        if max_val > 0:
            inv = 1.0 / max_val
            for y in range(self.h):
                for x in range(self.w):
                    grid[y][x] = clamp((grid[y][x] * inv) ** 0.82)
        return grid

    def to_ascii(self, density: List[List[float]]) -> str:
        ramp = self._effective_ramp()
        gamma = self._contrast_gamma()
        lines = []
        max_idx = len(ramp) - 1
        for row in density:
            line = "".join(ramp[int(round((clamp(v) ** gamma) * max_idx))] for v in row)
            lines.append(line)
        return "\n".join(lines)

    def _effective_ramp(self) -> str:
        ramp = self.ramp
        if len(ramp) < 12:
            return ramp
        if self.contrast > 0:
            # Increase contrast by removing low-density tail chars.
            trim = min(self.contrast, len(ramp) - 10)
            ramp = ramp[:-trim]
        elif self.contrast < 0:
            # Lower contrast by removing some dense head chars.
            trim = min(abs(self.contrast) // 2, len(ramp) - 10)
            ramp = ramp[trim:]
        return ramp

    def _contrast_gamma(self) -> float:
        # Positive contrast deepens shadows/highlights.
        return clamp(1.0 - (self.contrast * 0.04), 0.6, 1.4)


class Metrics:
    @staticmethod
    def _binary(grid: List[List[float]], threshold: float = 0.18) -> List[List[int]]:
        return [[1 if c >= threshold else 0 for c in row] for row in grid]

    @staticmethod
    def symmetry(grid: List[List[float]]) -> float:
        h = len(grid)
        w = len(grid[0]) if h else 0
        if w == 0:
            return 0.0
        diff = 0.0
        count = 0
        for y in range(h):
            for x in range(w // 2):
                diff += abs(grid[y][x] - grid[y][w - 1 - x])
                count += 1
        return clamp(1.0 - (diff / max(1, count)))

    @staticmethod
    def void_ratio(grid: List[List[float]], threshold: float = 0.12) -> float:
        total = len(grid) * len(grid[0]) if grid else 1
        empty = sum(1 for row in grid for c in row if c < threshold)
        return clamp(empty / total)

    @staticmethod
    def ink_ratio(grid: List[List[float]], threshold: float = 0.12) -> float:
        return clamp(1.0 - Metrics.void_ratio(grid, threshold=threshold))

    @staticmethod
    def edge_density(grid: List[List[float]], threshold: float = 0.15) -> float:
        h = len(grid)
        w = len(grid[0]) if h else 0
        if h == 0 or w == 0:
            return 0.0
        edges = 0
        checks = 0
        for y in range(h - 1):
            for x in range(w - 1):
                a = grid[y][x]
                b = grid[y][x + 1]
                c = grid[y + 1][x]
                if abs(a - b) > threshold:
                    edges += 1
                if abs(a - c) > threshold:
                    edges += 1
                checks += 2
        return clamp(edges / max(1, checks))

    @staticmethod
    def fragmentation(grid: List[List[float]], threshold: float = 0.18) -> float:
        b = Metrics._binary(grid, threshold)
        h = len(b)
        w = len(b[0]) if h else 0
        if h == 0 or w == 0:
            return 0.0
        seen = [[False for _ in range(w)] for _ in range(h)]
        comps = 0

        for y in range(h):
            for x in range(w):
                if b[y][x] == 0 or seen[y][x]:
                    continue
                comps += 1
                stack = [(x, y)]
                seen[y][x] = True
                while stack:
                    cx, cy = stack.pop()
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if 0 <= nx < w and 0 <= ny < h and b[ny][nx] == 1 and not seen[ny][nx]:
                            seen[ny][nx] = True
                            stack.append((nx, ny))

        filled = sum(sum(row) for row in b)
        if filled == 0:
            return 0.0
        scaled = min(25, comps) / 25.0
        return clamp(scaled)

    @staticmethod
    def central_focus(grid: List[List[float]]) -> float:
        h = len(grid)
        w = len(grid[0]) if h else 0
        if h == 0 or w == 0:
            return 0.0
        cx = (w - 1) / 2.0
        cy = (h - 1) / 2.0
        max_d = math.sqrt(cx * cx + cy * cy)
        num = 0.0
        den = 0.0
        for y in range(h):
            for x in range(w):
                val = grid[y][x]
                if val <= 0:
                    continue
                d = math.sqrt((x - cx) ** 2 + ((y - cy) * 2.0) ** 2)
                center_weight = 1.0 - clamp(d / max_d)
                num += val * center_weight
                den += val
        if den == 0:
            return 0.0
        return clamp(num / den)

    @staticmethod
    def centroid(grid: List[List[float]]) -> Tuple[float, float]:
        h = len(grid)
        w = len(grid[0]) if h else 0
        if h == 0 or w == 0:
            return 0.5, 0.5
        total = 0.0
        sum_x = 0.0
        sum_y = 0.0
        for y in range(h):
            for x in range(w):
                val = clamp(grid[y][x])
                if val <= 0.0:
                    continue
                total += val
                sum_x += x * val
                sum_y += y * val
        if total <= 1e-9:
            return 0.5, 0.5
        return clamp((sum_x / total) / max(1.0, (w - 1))), clamp((sum_y / total) / max(1.0, (h - 1)))

    @staticmethod
    def char_entropy(grid: List[List[float]]) -> float:
        bins = [0] * len(SHORT_RAMP)
        max_idx = len(SHORT_RAMP) - 1
        total = 0
        for row in grid:
            for v in row:
                idx = int(round(clamp(v) * max_idx))
                bins[idx] += 1
                total += 1
        if total == 0:
            return 0.0
        entropy = 0.0
        for c in bins:
            if c == 0:
                continue
            p = c / total
            entropy -= p * math.log(p, 2)
        return clamp(entropy / math.log(len(SHORT_RAMP), 2))


class FitnessModel:
    """Maps semantic scores to measurable structural targets and fitness."""

    def targets(self, scores: Dict[str, float]) -> Dict[str, float]:
        return {
            "symmetry": clamp(0.15 + 0.9 * scores["certainty"] - 0.55 * scores["conflict"]),
            "void": clamp(0.06 + 0.64 * scores["isolation"]),
            "ink": clamp(0.45 + 0.25 * scores["intensity"] + 0.2 * scores["hope"] - 0.35 * scores["isolation"]),
            "edge": clamp(0.15 + 0.85 * scores["intensity"]),
            "fragmentation": clamp(0.05 + 0.9 * scores["conflict"]),
            "center": clamp(0.15 + 0.8 * scores["self_focus"]),
        }

    def evaluate(
        self,
        m: Dict[str, float],
        t: Dict[str, float],
        scores: Dict[str, float],
        pressure: float,
        phase: str,
        prev_metrics: Dict[str, float] | None = None,
    ) -> Tuple[float, Dict[str, float]]:
        # Entropy pressure progressively rewards disorder.
        dynamic = {
            "symmetry": clamp(t["symmetry"] * (1.0 - 0.75 * pressure)),
            "void": clamp(t["void"] + 0.25 * pressure),
            "ink": clamp(t["ink"] - 0.35 * pressure),
            "edge": clamp(t["edge"] + 0.15 * pressure),
            "fragmentation": clamp(t["fragmentation"] + 0.4 * pressure),
            "center": t["center"],
        }

        if phase == "rebirth":
            dynamic["symmetry"] = clamp(0.35 + 0.45 * scores["hope"])
            dynamic["void"] = clamp(dynamic["void"] - 0.2)
            dynamic["ink"] = clamp(0.33 + 0.35 * scores["hope"])
            dynamic["fragmentation"] = clamp(dynamic["fragmentation"] - 0.35)
            dynamic["center"] = clamp(0.35 + 0.6 * scores["hope"] + 0.25 * scores["self_focus"])

        weights = {
            "symmetry": 0.2,
            "void": 0.08,
            "ink": 0.24,
            "edge": 0.18,
            "fragmentation": 0.18,
            "center": 0.12,
        }

        if phase == "rebirth":
            weights = {
                "symmetry": 0.18,
                "void": 0.06,
                "ink": 0.2,
                "edge": 0.15,
                "fragmentation": 0.14,
                "center": 0.27,
            }

        term_scores = {}
        total = 0.0
        for key, w in weights.items():
            delta = abs(m[key] - dynamic[key])
            term = 1.0 - (delta ** 1.35)
            term_scores[key] = clamp(term)
            total += term_scores[key] * w

        # Reward readable structures instead of noisy speck fields.
        readability = 1.0 - (
            0.88 * m["fragmentation"]
            + 0.34 * abs(m["edge"] - 0.42)
            + 0.26 * abs(m["ink"] - 0.36)
        )
        readability = clamp(readability)
        term_scores["readability"] = readability
        total += readability * (0.09 if phase != "collapse" else 0.05)

        # Prevent tiny speck solutions from winning.
        if m["ink"] < 0.08:
            total -= (0.08 - m["ink"]) * 1.5

        # Soft continuity objective between adjacent frames.
        if prev_metrics is not None:
            frag_prev = prev_metrics.get("fragmentation", m["fragmentation"])
            center_prev = prev_metrics.get("center", m["center"])
            cx_prev = prev_metrics.get("centroid_x", 0.5)
            cy_prev = prev_metrics.get("centroid_y", 0.5)
            frag_jump = max(0.0, m["fragmentation"] - frag_prev)
            center_jump = abs(m["center"] - center_prev)
            drift = math.sqrt((m.get("centroid_x", 0.5) - cx_prev) ** 2 + (m.get("centroid_y", 0.5) - cy_prev) ** 2)

            continuity = clamp(1.0 - (0.66 * frag_jump + 0.9 * center_jump + 1.15 * drift))
            term_scores["continuity"] = continuity
            total += continuity * (0.08 if phase != "collapse" else 0.04)

            jump_penalty = (0.22 + 0.28 * pressure) * frag_jump
            jump_penalty += (0.18 + 0.25 * pressure) * max(0.0, center_jump - 0.08)
            jump_penalty += (0.35 + 0.2 * pressure) * max(0.0, drift - 0.055)
            total -= jump_penalty

        # Mild bonus in rebirth for healthy diversity.
        if phase == "rebirth":
            total += 0.08 * m["entropy"]

        return clamp(total), term_scores


class GeneticEngine:
    def __init__(self, renderer: PrimitiveRenderer, rng: random.Random) -> None:
        self.renderer = renderer
        self.rng = rng
        self.kinds = ("line", "circle", "rect")

    def _random_primitive(self) -> Primitive:
        if self.rng.random() < 0.55:
            cx = self.renderer.w * 0.5
            cy = self.renderer.h * 0.5
            x = clamp(self.rng.gauss(cx, self.renderer.w * 0.18), 0, self.renderer.w - 1)
            y = clamp(self.rng.gauss(cy, self.renderer.h * 0.18), 0, self.renderer.h - 1)
        else:
            x = self.rng.uniform(0, self.renderer.w - 1)
            y = self.rng.uniform(0, self.renderer.h - 1)
        return Primitive(
            kind=self.rng.choice(self.kinds),
            x=x,
            y=y,
            size=self.rng.uniform(4, min(self.renderer.w, self.renderer.h) * 0.42),
            rot=self.rng.uniform(0, math.pi),
            intensity=self.rng.uniform(0.4, 1.0),
        )

    def _line_primitive(self, x0: float, y0: float, x1: float, y1: float, intensity: float = 0.8) -> Primitive:
        dx = x1 - x0
        dy = y1 - y0
        size = max(3.0, math.sqrt(dx * dx + dy * dy))
        rot = math.atan2(dy, dx) % math.pi
        return Primitive(
            kind="line",
            x=clamp((x0 + x1) * 0.5, 0, self.renderer.w - 1),
            y=clamp((y0 + y1) * 0.5, 0, self.renderer.h - 1),
            size=clamp(size, 2.0, min(self.renderer.w, self.renderer.h) * 0.5),
            rot=rot,
            intensity=clamp(intensity, 0.12, 1.0),
        )

    def subject_template_genome(self, intent: SceneIntent) -> List[Primitive]:
        cx = self.renderer.w * 0.5
        cy = self.renderer.h * 0.5
        min_dim = min(self.renderer.w, self.renderer.h)
        decay_scale = clamp(1.0 - (intent.decay * 0.35))
        out: List[Primitive] = []

        if intent.subject == "star":
            r = max(5.0, min_dim * 0.14 * decay_scale)
            out.append(Primitive(kind="circle", x=cx, y=cy, size=r * 1.25, rot=0.0, intensity=0.98))
            rays = 8
            for i in range(rays):
                a = (2.0 * math.pi * i) / rays
                x1 = cx + (r * 1.95) * math.cos(a)
                y1 = cy + (r * 1.32) * math.sin(a)
                out.append(self._line_primitive(cx, cy, x1, y1, intensity=0.82))
        elif intent.subject == "tree":
            trunk_base_y = self.renderer.h - 2
            trunk_top_y = self.renderer.h * (0.38 + 0.12 * intent.decay)
            out.append(self._line_primitive(cx, trunk_base_y, cx, trunk_top_y, intensity=0.88))
            out.append(
                Primitive(
                    kind="rect",
                    x=cx,
                    y=(trunk_base_y + trunk_top_y) * 0.5,
                    size=max(5.0, min_dim * 0.16),
                    rot=math.pi / 2,
                    intensity=0.72,
                )
            )
            crown_y = max(3.0, trunk_top_y - min_dim * 0.16)
            out.append(Primitive(kind="circle", x=cx, y=crown_y, size=max(7.0, min_dim * 0.28), rot=0.0, intensity=0.84))
            out.append(self._line_primitive(cx, trunk_top_y, cx - min_dim * 0.16, crown_y, intensity=0.7))
            out.append(self._line_primitive(cx, trunk_top_y + 1.0, cx + min_dim * 0.16, crown_y + 1.0, intensity=0.7))
        elif intent.subject == "sunflower":
            head_y = self.renderer.h * 0.38
            head_r = max(5.0, min_dim * 0.11)
            out.append(Primitive(kind="circle", x=cx, y=head_y, size=head_r * 0.95, rot=0.0, intensity=0.96))
            petals = 10
            for i in range(petals):
                a = (2.0 * math.pi * i) / petals
                px = cx + (head_r * 1.35) * math.cos(a)
                py = head_y + (head_r * 0.95) * math.sin(a)
                out.append(Primitive(kind="circle", x=px, y=py, size=head_r * 0.55, rot=0.0, intensity=0.72))
            out.append(self._line_primitive(cx, head_y + head_r, cx, self.renderer.h - 2, intensity=0.82))
            out.append(
                Primitive(
                    kind="rect",
                    x=cx - min_dim * 0.1,
                    y=head_y + head_r * 1.8,
                    size=max(4.0, min_dim * 0.12),
                    rot=0.35,
                    intensity=0.62,
                )
            )
            out.append(
                Primitive(
                    kind="rect",
                    x=cx + min_dim * 0.1,
                    y=head_y + head_r * 2.0,
                    size=max(4.0, min_dim * 0.12),
                    rot=math.pi - 0.35,
                    intensity=0.62,
                )
            )

        return out

    def random_genome(self, n_min: int = 18, n_max: int = 34) -> List[Primitive]:
        return [self._random_primitive() for _ in range(self.rng.randint(n_min, n_max))]

    def crossover(self, a: Sequence[Primitive], b: Sequence[Primitive], pressure: float) -> List[Primitive]:
        if not a:
            return [Primitive(**vars(p)) for p in b]
        if not b:
            return [Primitive(**vars(p)) for p in a]

        cut_a = self.rng.randint(0, len(a) - 1)
        cut_b = self.rng.randint(0, len(b) - 1)
        child = [Primitive(**vars(p)) for p in a[:cut_a]] + [Primitive(**vars(p)) for p in b[cut_b:]]

        if self.rng.random() < 0.2 + pressure * 0.35:
            self.rng.shuffle(child)
        return child[:38]

    def mutate(self, genome: List[Primitive], pressure: float) -> None:
        if not genome:
            genome.append(self._random_primitive())

        mutation_rate = 0.08 + pressure * 0.28
        for p in genome:
            if self.rng.random() < mutation_rate:
                p.x = clamp(p.x + self.rng.uniform(-8, 8), 0, self.renderer.w - 1)
            if self.rng.random() < mutation_rate:
                p.y = clamp(p.y + self.rng.uniform(-5, 5), 0, self.renderer.h - 1)
            if self.rng.random() < mutation_rate:
                p.size = clamp(p.size + self.rng.uniform(-4, 4), 2, min(self.renderer.w, self.renderer.h) * 0.5)
            if self.rng.random() < mutation_rate:
                p.rot = (p.rot + self.rng.uniform(-0.5, 0.5)) % math.pi
            if self.rng.random() < mutation_rate:
                p.intensity = clamp(p.intensity + self.rng.uniform(-0.25, 0.25), 0.05, 1.0)
            if self.rng.random() < mutation_rate * 0.5:
                p.kind = self.rng.choice(self.kinds)

        if self.rng.random() < 0.22 + pressure * 0.25 and len(genome) < 42:
            genome.append(self._random_primitive())
        if self.rng.random() < 0.16 + pressure * 0.2 and len(genome) > 6:
            del genome[self.rng.randrange(len(genome))]

    def spawn_population(self, size: int) -> List[List[Primitive]]:
        return [self.random_genome() for _ in range(size)]

    def seeded_population(self, base: Sequence[Primitive], size: int, pressure: float) -> List[List[Primitive]]:
        pop = []
        for i in range(size):
            if i == 0:
                pop.append([Primitive(**vars(p)) for p in base])
                continue
            child = [Primitive(**vars(p)) for p in base]
            self.mutate(child, max(pressure, 0.2))
            if self.rng.random() < 0.35:
                child.append(self._random_primitive())
            pop.append(child)
        return pop


class NarrativeObserver:
    def line(self, phase: str, metrics: Dict[str, float], prev: Dict[str, float] | None) -> str:
        fit = metrics["fitness"]
        ent = metrics["entropy"]
        sym = metrics["symmetry"]

        if phase == "order":
            return f"Coherence {fit:.2f}. Symmetry lock {sym:.2f}."
        if phase == "pressure":
            delta = 0.0 if prev is None else fit - prev["fitness"]
            if delta >= 0:
                return f"Entropy pressure rising. Adaptation retained ({fit:.2f})."
            return f"Symmetry loss detected. Fitness drift {delta:.2f}."
        if phase == "collapse":
            return f"Collapse corridor entered. Entropy index {ent:.2f}."
        return f"Reconfiguration initiated. Core coherence {fit:.2f}."


_WORKER_RENDERERS: Dict[Tuple[int, int, str, int], PrimitiveRenderer] = {}


def _measure_grid(grid: List[List[float]]) -> Dict[str, float]:
    cx, cy = Metrics.centroid(grid)
    return {
        "symmetry": Metrics.symmetry(grid),
        "void": Metrics.void_ratio(grid),
        "ink": Metrics.ink_ratio(grid),
        "edge": Metrics.edge_density(grid),
        "fragmentation": Metrics.fragmentation(grid),
        "center": Metrics.central_focus(grid),
        "entropy": Metrics.char_entropy(grid),
        "centroid_x": cx,
        "centroid_y": cy,
    }


def _render_and_measure_task(
    task: Tuple[int, Sequence[Primitive], int, int, str, int]
) -> Tuple[int, Tuple[Tuple[str, int, int, int, int, int], ...], Dict[str, float]]:
    idx, genome, width, height, ramp, contrast = task
    key = (width, height, ramp, contrast)
    renderer = _WORKER_RENDERERS.get(key)
    if renderer is None:
        renderer = PrimitiveRenderer(width=width, height=height, ramp=ramp, contrast=contrast)
        _WORKER_RENDERERS[key] = renderer
    grid = renderer.render_density(genome)
    return idx, genome_signature(genome), _measure_grid(grid)


class CosmicEntropyGA:
    def __init__(
        self,
        width: int = 120,
        height: int = 45,
        ascii_mode: str = "short",
        contrast: int = 0,
    ) -> None:
        self.analyzer = SemanticAnalyzer()
        ramp = SHORT_RAMP if ascii_mode == "short" else DETAILED_RAMP
        self.renderer = PrimitiveRenderer(width=width, height=height, ramp=ramp, contrast=contrast)
        self.model = FitnessModel()
        self.observer = NarrativeObserver()

    def _measure(self, grid: List[List[float]]) -> Dict[str, float]:
        return _measure_grid(grid)

    def _evaluate_population(
        self,
        population: Sequence[Sequence[Primitive]],
        scores: Dict[str, float],
        pressure: float,
        phase: str,
        targets: Dict[str, float],
        structure_cache: Dict[Tuple[Tuple[str, int, int, int, int, int], ...], Dict[str, float]],
        prev_frame_metrics: Dict[str, float] | None = None,
        executor: Executor | None = None,
    ) -> List[Tuple[float, Dict[str, float], Sequence[Primitive], Tuple[Tuple[str, int, int, int, int, int], ...]]]:
        evaluated = []
        uncached_jobs: List[Tuple[int, Sequence[Primitive], int, int, str, int]] = []

        for idx, genome in enumerate(population):
            sig = genome_signature(genome)
            m_base = structure_cache.get(sig)
            if m_base is None:
                uncached_jobs.append((idx, genome, self.renderer.w, self.renderer.h, self.renderer.ramp, self.renderer.contrast))
                continue

            m = dict(m_base)
            fit, term_scores = self.model.evaluate(
                m,
                targets,
                scores,
                pressure,
                phase,
                prev_metrics=prev_frame_metrics,
            )
            m["fitness"] = fit
            m["terms"] = term_scores
            evaluated.append((fit, m, genome, sig))

        if uncached_jobs:
            if executor is None:
                uncached_results = [_render_and_measure_task(job) for job in uncached_jobs]
            else:
                futures = [executor.submit(_render_and_measure_task, job) for job in uncached_jobs]
                uncached_results = [f.result() for f in futures]

            if len(structure_cache) + len(uncached_results) > 5000:
                structure_cache.clear()

            for idx, sig, m_base in uncached_results:
                structure_cache[sig] = m_base
                genome = population[idx]
                m = dict(m_base)
                fit, term_scores = self.model.evaluate(
                    m,
                    targets,
                    scores,
                    pressure,
                    phase,
                    prev_metrics=prev_frame_metrics,
                )
                m["fitness"] = fit
                m["terms"] = term_scores
                evaluated.append((fit, m, genome, sig))

        evaluated.sort(key=lambda item: item[0], reverse=True)
        return evaluated

    def evolve(
        self,
        seed_text: str,
        frames: int = 8,
        generations_per_frame: int = 26,
        population_size: int = 80,
        save_dir: str = "outputs",
        early_stop: bool = True,
        min_generations: int = 8,
        patience: int = 6,
        improvement_eps: float = 0.0015,
        animate: bool = True,
        frame_delay: float = 0.12,
        quiet: bool = False,
        workers: int = 1,
        timing: bool = False,
        subject_mode: str = "auto",
        image_model: str = "gpt-image-1",
        image_size: str = "512x512",
        image_timeout: float = 30.0,
        image_cache_dir: str = ".subject_cache",
        seed: int | None = None,
        frame_kick: float = 0.18,
    ) -> None:
        run_start = time.perf_counter()
        if seed is None:
            seed_hash = hashlib.md5(seed_text.encode("utf-8")).hexdigest()
            rng_seed = int(seed_hash, 16)
        else:
            rng_seed = int(seed)
            seed_hash = hashlib.md5(f"{seed_text}|{rng_seed}".encode("utf-8")).hexdigest()
        rng = random.Random(rng_seed)
        visual_rng = random.Random(rng_seed ^ 0x5A17A3D1)
        ga = GeneticEngine(self.renderer, rng)

        scores = self.analyzer.analyze(seed_text)
        scene_intent = parse_scene_intent(seed_text)
        dialogue_seed = build_cinematic_dialogue(scores, visual_rng)
        model_subject_sprite: List[str] | None = None
        if subject_mode in {"auto", "image"}:
            subj_w = max(16, min(self.renderer.w - 2, int(self.renderer.w * 0.38)))
            subj_h = max(8, min(self.renderer.h - 2, int(self.renderer.h * (0.42 if scene_intent.mode == "branching" else 0.30))))
            sprite, source = build_model_subject_sprite(
                seed_text=seed_text,
                intent=scene_intent,
                max_w=subj_w,
                max_h=subj_h,
                model=image_model,
                image_size=image_size,
                timeout_s=image_timeout,
                cache_dir=image_cache_dir,
            )
            if sprite:
                model_subject_sprite = sprite
                print(f"[SYSTEM] Subject renderer: image ({source})")
            elif subject_mode == "image":
                print("[SYSTEM] Image subject unavailable; falling back to procedural subject renderer.")
        template_genome: List[Primitive] = []
        if scene_intent.subject != "generic" and (subject_mode == "procedural" or model_subject_sprite is None):
            template_genome = ga.subject_template_genome(scene_intent)
            if template_genome:
                print(f"[SYSTEM] Procedural subject template injected: {scene_intent.subject}")
        # Build a dialogue timeline ONCE (so text animates across frames)
        dialogue_events = build_events_from_seed(
            seed_text=dialogue_seed,
            total_frames=frames,
            width=self.renderer.w,
            height=self.renderer.h,
            rng=visual_rng,
        )
        print("\n--- SEMANTIC QUANTIFICATION ---")
        for k, v in scores.items():
            bar = ("#" * int(v * 20)).ljust(20)
            print(f"{k.ljust(15)} : [{bar}] {v:.2f}")
        print("-------------------------------")

        if template_genome:
            guided = max(8, int(population_size * 0.65))
            population = ga.seeded_population(
                template_genome,
                guided,
                pressure=clamp(0.24 + 0.22 * scene_intent.decay),
            )
            if guided < population_size:
                population.extend(ga.spawn_population(population_size - guided))
            rng.shuffle(population)
        else:
            population = ga.spawn_population(population_size)
        frame_records: List[str] = []
        previous_metrics: Dict[str, float] | None = None
        best_genome: List[Primitive] | None = None
        targets = self.model.targets(scores)
        structure_cache: Dict[Tuple[Tuple[str, int, int, int, int, int], ...], Dict[str, float]] = {}
        executor: ProcessPoolExecutor | None = None
        active_workers = 1
        if workers > 1:
            try:
                executor = ProcessPoolExecutor(max_workers=workers)
                active_workers = workers
            except Exception as exc:
                print(
                    f"\n[SYSTEM] Parallel workers unavailable ({type(exc).__name__}: {exc}). "
                    "Falling back to --workers 1."
                )
                executor = None
                active_workers = 1
        display_enabled = not quiet
        animate_active = display_enabled and animate and sys.stdout.isatty()
        ansi_ok = os.name == "nt" or os.environ.get("TERM", "").lower() not in {"", "dumb"}
        frame_perf: List[Tuple[int, float, int]] = []
        rebirth_perf: Tuple[float, int] | None = None
        eval_calls = 0

        def evaluate_population_local(
            pop: Sequence[Sequence[Primitive]],
            pressure: float,
            phase: str,
            prev_anchor: Dict[str, float] | None,
        ) -> List[Tuple[float, Dict[str, float], Sequence[Primitive], Tuple[Tuple[str, int, int, int, int, int], ...]]]:
            nonlocal executor, active_workers, eval_calls
            eval_calls += 1
            try:
                return self._evaluate_population(
                    pop,
                    scores,
                    pressure,
                    phase,
                    targets=targets,
                    structure_cache=structure_cache,
                    prev_frame_metrics=prev_anchor,
                    executor=executor,
                )
            except Exception as exc:
                if executor is not None:
                    print(
                        f"\n[SYSTEM] Parallel evaluation failed ({type(exc).__name__}: {exc}). "
                        "Switching to --workers 1 for this run."
                    )
                    executor.shutdown(wait=False, cancel_futures=True)
                    executor = None
                    active_workers = 1
                    return self._evaluate_population(
                        pop,
                        scores,
                        pressure,
                        phase,
                        targets=targets,
                        structure_cache=structure_cache,
                        prev_frame_metrics=prev_anchor,
                        executor=None,
                    )
                raise

        def render_block(block: str) -> None:
            if not display_enabled:
                return
            if not animate_active:
                print(f"\n{block}")
                return
            if ansi_ok:
                # Fast path: move to home and overwrite; avoid full-screen clear each frame.
                sys.stdout.write("\x1b[H")
                sys.stdout.write(block)
                sys.stdout.write("\x1b[J")
                sys.stdout.flush()
            else:
                # Fallback for limited terminals where ANSI clear is not supported.
                if os.name == "nt":
                    # `clear` does not exist on Windows; emulate clear with vertical spacing.
                    print("\n" * 120, end="")
                else:
                    rc = subprocess.run(
                        ["clear"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    ).returncode
                    if rc != 0:
                        print("\n" * 120, end="")
                print(block, end="", flush=True)
            if frame_delay > 0:
                time.sleep(frame_delay)

        if animate_active:
            # Hide cursor during animation and restore in finally block.
            if ansi_ok:
                sys.stdout.write("\x1b[2J\x1b[H")
                sys.stdout.flush()
            print("\x1b[?25l", end="", flush=True)

        try:
            for frame in range(frames):
                frame_start = time.perf_counter()
                pressure = frame / max(1, frames - 1)
                phase = "order" if frame == 0 else "pressure"
                if frame >= frames - 2:
                    phase = "collapse"

                best_seen = -1.0
                stagnant = 0
                gens_run = 0

                for gen in range(generations_per_frame):
                    ranked = evaluate_population_local(population, pressure, phase, previous_metrics)
                    gens_run = gen + 1
                    frame_best = ranked[0][0]
                    if frame_best > best_seen + improvement_eps:
                        best_seen = frame_best
                        stagnant = 0
                    else:
                        stagnant += 1

                    if early_stop and gen + 1 >= min_generations and stagnant >= patience:
                        break

                    elite_n = max(4, population_size // 8)
                    elites = [item[2] for item in ranked[:elite_n]]

                    next_pop = [[Primitive(**vars(p)) for p in g] for g in elites]
                    while len(next_pop) < population_size:
                        pa = elites[rng.randrange(len(elites))]
                        pb = elites[rng.randrange(len(elites))]
                        child = ga.crossover(pa, pb, pressure)
                        ga.mutate(child, pressure)
                        next_pop.append(child)

                    population = next_pop

                ranked = evaluate_population_local(population, pressure, phase, previous_metrics)
                best_fit, best_metrics, best_genome, _ = ranked[0]
                best_grid = self.renderer.render_density(best_genome)
                ascii_frame = self.renderer.to_ascii(best_grid)
                # Overlay requested subject (e.g. star/tree/sunflower) into the same world frame.
                ascii_frame = render_subject_over_world(
                    world_ascii=ascii_frame,
                    intent=scene_intent,
                    frame_idx=frame,
                    total_frames=frames,
                    rng=visual_rng,
                    model_sprite=model_subject_sprite,
                )
                # Overlay moving dialogue onto the ASCII world (same frame)
                ascii_frame = render_dialogue_over_world(
                    world_ascii=ascii_frame,
                    events=dialogue_events,
                    frame_idx=frame,
                    rng=visual_rng,
                )

                frame_block = (
                    f"=== FRAME {frame + 1}/{frames} | phase={phase} | pressure={pressure:.2f} | gens={gens_run}/{generations_per_frame} ===\n"
                    f"{ascii_frame}\n"
                    "metrics: "
                    f"fit={best_fit:.3f} sym={best_metrics['symmetry']:.3f} void={best_metrics['void']:.3f} "
                    f"ink={best_metrics['ink']:.3f} edge={best_metrics['edge']:.3f} frag={best_metrics['fragmentation']:.3f} "
                    f"center={best_metrics['center']:.3f} H={best_metrics['entropy']:.3f}\n"
                    f">> NARRATIVE LOG: {self.observer.line(phase, best_metrics, previous_metrics)}\n"
                )
                render_block(frame_block)

                previous_metrics = best_metrics
                frame_records.append(
                    f"FRAME {frame + 1} phase={phase} pressure={pressure:.2f}\n"
                    f"metrics={best_metrics}\n{ascii_frame}\n"
                )
                frame_elapsed = time.perf_counter() - frame_start
                frame_perf.append((frame + 1, frame_elapsed, gens_run))
                if timing:
                    print(
                        f"[PERF] frame {frame + 1}/{frames}: {frame_elapsed:.2f}s "
                        f"({gens_run} gens, {frame_elapsed / max(1, gens_run):.3f}s/gen)"
                    )

                # Carry elite structure into the next frame, then apply controlled drift.
                if frame < frames - 1 and best_genome is not None:
                    carry_pressure = clamp(
                        0.18
                        + 0.42 * pressure
                        + 0.2 * scores["impermanence"]
                        + 0.16 * scores["conflict"]
                        - 0.3 * scores["certainty"]
                    )
                    population = ga.seeded_population(best_genome, population_size, pressure=max(0.18, carry_pressure))

                    if frame_kick > 0:
                        kick_rate = clamp(
                            (
                                frame_kick
                                + 0.16 * pressure
                                + 0.11 * scores["impermanence"]
                                + 0.08 * scores["conflict"]
                            )
                            * (1.0 - 0.72 * scores["certainty"])
                        )
                        # Keep index 0 as continuity anchor; perturb the rest.
                        for genome in population[1:]:
                            if rng.random() < kick_rate:
                                ga.mutate(genome, clamp(carry_pressure + 0.24))

            if scores["hope"] >= 0.25 and best_genome is not None:
                population = ga.seeded_population(best_genome, population_size, pressure=0.55)
                rebirth_total_gens = generations_per_frame + 10
                rebirth_best_seen = -1.0
                rebirth_stagnant = 0
                rebirth_start = time.perf_counter()
                rebirth_gens_run = 0
                for gen in range(rebirth_total_gens):
                    ranked = evaluate_population_local(population, pressure=0.35, phase="rebirth", prev_anchor=previous_metrics)
                    rebirth_gens_run = gen + 1
                    frame_best = ranked[0][0]
                    if frame_best > rebirth_best_seen + improvement_eps:
                        rebirth_best_seen = frame_best
                        rebirth_stagnant = 0
                    else:
                        rebirth_stagnant += 1
                    if early_stop and gen + 1 >= min_generations and rebirth_stagnant >= patience:
                        break

                    elites = [item[2] for item in ranked[: max(5, population_size // 7)]]
                    next_pop = [[Primitive(**vars(p)) for p in g] for g in elites]
                    while len(next_pop) < population_size:
                        pa = elites[rng.randrange(len(elites))]
                        pb = elites[rng.randrange(len(elites))]
                        child = ga.crossover(pa, pb, pressure=0.35)
                        ga.mutate(child, pressure=0.35)
                        next_pop.append(child)
                    population = next_pop
                rebirth_elapsed = time.perf_counter() - rebirth_start
                rebirth_perf = (rebirth_elapsed, rebirth_gens_run)
                if timing:
                    print(
                        f"[PERF] rebirth: {rebirth_elapsed:.2f}s "
                        f"({rebirth_gens_run} gens, {rebirth_elapsed / max(1, rebirth_gens_run):.3f}s/gen)"
                    )

                best_fit, best_metrics, best_genome, _ = evaluate_population_local(
                    population, pressure=0.35, phase="rebirth", prev_anchor=previous_metrics
                )[0]
                best_grid = self.renderer.render_density(best_genome)
                ascii_frame = self.renderer.to_ascii(best_grid)
                ascii_frame = render_subject_over_world(
                    world_ascii=ascii_frame,
                    intent=scene_intent,
                    frame_idx=frames,
                    total_frames=frames + 1,
                    rng=visual_rng,
                    model_sprite=model_subject_sprite,
                )
                ascii_frame = render_dialogue_over_world(
                    world_ascii=ascii_frame,
                    events=dialogue_events,
                    frame_idx=frames,
                    rng=visual_rng,
                )
                rebirth_block = (
                    f"=== REBIRTH PHASE ===\n"
                    f"{ascii_frame}\n"
                    "metrics: "
                    f"fit={best_fit:.3f} sym={best_metrics['symmetry']:.3f} void={best_metrics['void']:.3f} "
                    f"ink={best_metrics['ink']:.3f} edge={best_metrics['edge']:.3f} frag={best_metrics['fragmentation']:.3f} "
                    f"center={best_metrics['center']:.3f} H={best_metrics['entropy']:.3f}\n"
                    f">> NARRATIVE LOG: {self.observer.line('rebirth', best_metrics, previous_metrics)}\n"
                )
                render_block(rebirth_block)
                frame_records.append(f"REBIRTH\nmetrics={best_metrics}\n{ascii_frame}\n")
            else:
                print("\n[SYSTEM] Total collapse. No rebirth event selected.")

            out_dir = Path(save_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"cosmic_entropy_{seed_hash[:10]}.txt"
            out_file.write_text("\n\n".join(frame_records), encoding="utf-8")
            print(f"\n[SYSTEM] Frames saved: {out_file}")
            if timing:
                total_time = time.perf_counter() - run_start
                total_gens = sum(item[2] for item in frame_perf) + (rebirth_perf[1] if rebirth_perf else 0)
                print("\n--- PERFORMANCE ---")
                for f_idx, f_secs, f_gens in frame_perf:
                    print(f"frame {f_idx:02d}: {f_secs:.2f}s | {f_gens} gens | {f_secs / max(1, f_gens):.3f}s/gen")
                if rebirth_perf:
                    r_secs, r_gens = rebirth_perf
                    print(f"rebirth : {r_secs:.2f}s | {r_gens} gens | {r_secs / max(1, r_gens):.3f}s/gen")
                print(
                    f"total   : {total_time:.2f}s | eval_calls={eval_calls} | "
                    f"workers_active={active_workers} | generations={total_gens}"
                )
                print("-------------------")
        finally:
            if animate_active:
                print("\x1b[?25h", end="", flush=True)
            if executor is not None:
                executor.shutdown(wait=True)


# --- Audiovisual Feature Implementation ---

class AudiovisualGenerator:
    """Generates music via evolution and drives high-clarity Perlin-based visuals."""

    def __init__(self, semantic_params: Dict[str, float], output_dir: str = "outputs/audiovisual"):
        self.params = semantic_params
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.sample_rate = 44100
        self.fps = 30
        
        # Precompute permutation table for Perlin noise
        self.p = np.arange(256, dtype=int)
        rng = np.random.RandomState(42) # Localized random state for consistency
        rng.shuffle(self.p)
        self.p = np.stack([self.p, self.p]).flatten()

    def _midi_to_freq(self, midi_note: int) -> float:
        return 440.0 * (2.0 ** ((midi_note - 69) / 12.0))

    def _generate_melody(self, duration_beats: int = 32) -> Tuple[List[Tuple[int, float, float]], float]:
        """Co-evolutionary melody+rhythm generation with multi-objective fitness and adaptive mutation."""
        energy = max(0.3, self.params.get("intensity", 0.5))
        conflict = max(0.2, self.params.get("conflict", 0.5))
        motion = max(0.3, conflict * 0.5 + self.params.get("impermanence", 0.5) * 0.5)
        brightness = max(0.35, self.params.get("hope", 0.5))
        density = max(0.4, 1.0 - self.params.get("isolation", 0.5) * 0.6)

        target_tempo = 55 + energy * 95
        is_major = brightness > 0.45
        scale = [0, 2, 4, 7, 9] if is_major else [0, 3, 5, 7, 10]
        root = int(48 + brightness * 8)
        octave_choices = [0, 12]

        if motion > 0.6:
            dur_choices = [0.25, 0.5, 0.5, 1.0, 1.0, 2.0]
        else:
            dur_choices = [0.5, 1.0, 1.0, 2.0, 2.0, 4.0]

        # Adaptive mutation: conflict drives how wild the evolution gets
        base_mutation = 0.10 + conflict * 0.35
        mutation_strength = 1 + int(conflict * 4)

        pop_size = 40
        gens = 50

        class MelodyGenome:
            def __init__(self, notes):
                self.notes = notes
                self.fitness = 0.0

        class RhythmGenome:
            def __init__(self, pattern):
                self.pattern = pattern  # list of (duration, accent_bool)
                self.fitness = 0.0

        def init_melody_pop():
            pop = []
            for _ in range(pop_size):
                notes = []
                cur = 0.0
                while cur < duration_beats:
                    p = root + random.choice(scale) + random.choice(octave_choices)
                    p = min(p, 67)
                    d = random.choice(dur_choices)
                    v = random.uniform(0.5, 0.9) if random.random() < density else 0.0
                    if cur + d > duration_beats: d = duration_beats - cur
                    notes.append((int(p), float(d), float(v)))
                    cur += d
                pop.append(MelodyGenome(notes))
            return pop

        def init_rhythm_pop():
            pop = []
            for _ in range(pop_size):
                pattern = []
                cur = 0.0
                while cur < duration_beats:
                    d = random.choice(dur_choices)
                    if cur + d > duration_beats: d = duration_beats - cur
                    accent = random.random() < (0.3 + energy * 0.4)
                    pattern.append((float(d), accent))
                    cur += d
                pop.append(RhythmGenome(pattern))
            return pop

        def melody_fitness(mg, best_rhythm):
            score = 0.0
            active = [n for n in mg.notes if n[2] > 0]
            if not active: return 0.0

            # 1. Scale conformity
            score += sum(1 for n in active if (n[0] - root) % 12 in scale) / len(active) * 4.0

            # 2. Smoothness — penalize big leaps
            if len(active) > 1:
                leaps = [abs(active[i][0] - active[i-1][0]) for i in range(1, len(active))]
                score += sum(max(0, 1 - (l / 7.0)) for l in leaps) / len(leaps) * 2.5

            # 3. Tension/resolution — reward phrases that return to root or fifth
            resolution_notes = {root % 12, (root + 7) % 12}
            last_few = active[-3:] if len(active) >= 3 else active
            resolves = sum(1 for n in last_few if n[0] % 12 in resolution_notes)
            score += resolves * 1.5

            # 4. Motif repetition — reward short patterns that appear more than once
            if len(active) >= 6:
                intervals = [active[i][0] - active[i-1][0] for i in range(1, len(active))]
                motif_len = 3
                motifs = [tuple(intervals[j:j+motif_len]) for j in range(len(intervals) - motif_len + 1)]
                unique = set(motifs)
                repeated = sum(1 for m in unique if motifs.count(m) > 1)
                score += min(repeated, 3) * 1.5

            # 5. Pitch range penalty
            high_notes = sum(1 for n in active if n[0] > 67)
            score -= high_notes * 2.0

            # 6. Density match
            score += (len(active) / max(len(mg.notes), 1)) * density * 2.0

            # 7. Co-evolution: reward alignment with rhythm accents
            if best_rhythm:
                m_idx, r_idx = 0, 0
                m_beat, r_beat = 0.0, 0.0
                align_score = 0
                align_total = 0
                while m_idx < len(mg.notes) and r_idx < len(best_rhythm.pattern):
                    if abs(m_beat - r_beat) < 0.01:
                        if best_rhythm.pattern[r_idx][1] and mg.notes[m_idx][2] > 0.5:
                            align_score += 1
                        align_total += 1
                    if m_beat <= r_beat:
                        m_beat += mg.notes[m_idx][1]
                        m_idx += 1
                    else:
                        r_beat += best_rhythm.pattern[r_idx][0]
                        r_idx += 1
                if align_total > 0:
                    score += (align_score / align_total) * 2.0

            return score

        def rhythm_fitness(rg, best_melody):
            score = 0.0
            n_accents = sum(1 for _, a in rg.pattern if a)
            total = len(rg.pattern)
            if total == 0: return 0.0

            # Accent density matches energy
            ideal_ratio = 0.2 + energy * 0.4
            actual_ratio = n_accents / total
            score += max(0, 2.0 - abs(actual_ratio - ideal_ratio) * 5.0)

            # Rhythmic variety
            durs = set(d for d, _ in rg.pattern)
            score += min(len(durs) / 3.0, 1.0) * motion * 2.0

            # Downbeat accent reward (beat 0, 4, 8...)
            cur = 0.0
            for d, a in rg.pattern:
                if a and (cur % 4.0) < 0.01:
                    score += 0.5
                cur += d

            # Co-evolution: reward syncopation against melody
            if best_melody:
                active_beats = set()
                cur = 0.0
                for p, d, v in best_melody.notes:
                    if v > 0: active_beats.add(round(cur, 2))
                    cur += d
                r_beat = 0.0
                offbeats = 0
                for d, a in rg.pattern:
                    if a and round(r_beat, 2) not in active_beats:
                        offbeats += 1
                    r_beat += d
                score += min(offbeats, 4) * conflict * 0.8

            return score

        melody_pop = init_melody_pop()
        rhythm_pop = init_rhythm_pop()
        best_melody = None
        best_rhythm = None

        for gen in range(gens):
            # Evaluate melody fitness against best rhythm
            for ind in melody_pop:
                ind.fitness = melody_fitness(ind, best_rhythm)
            melody_pop.sort(key=lambda x: x.fitness, reverse=True)
            best_melody = melody_pop[0]

            # Evaluate rhythm fitness against best melody
            for ind in rhythm_pop:
                ind.fitness = rhythm_fitness(ind, best_melody)
            rhythm_pop.sort(key=lambda x: x.fitness, reverse=True)
            best_rhythm = rhythm_pop[0]

            if gen == gens - 1: break

            # Adaptive mutation rate decays over generations but conflict keeps it high
            gen_ratio = gen / gens
            mutation_rate = base_mutation * (1.0 - gen_ratio * 0.5)

            # Breed melody population
            m_elites = melody_pop[:6]
            next_m = m_elites[:]
            while len(next_m) < pop_size:
                p1, p2 = random.sample(m_elites, 2)
                split = random.randint(1, min(len(p1.notes), len(p2.notes)) - 1)
                child_notes = p1.notes[:split] + p2.notes[split:]
                c_dur = sum(n[1] for n in child_notes)
                if c_dur > duration_beats:
                    child_notes = child_notes[:-1]
                elif c_dur < duration_beats:
                    child_notes.append((root, duration_beats - c_dur, 0.6))
                if random.random() < mutation_rate:
                    for _ in range(mutation_strength):
                        m_idx = random.randrange(len(child_notes))
                        n = list(child_notes[m_idx])
                        n[0] = min(max(n[0] + random.choice([-3, -2, -1, 1, 2, 3]), 36), 67)
                        child_notes[m_idx] = tuple(n)
                if random.random() < mutation_rate * 0.5:
                    m_idx = random.randrange(len(child_notes))
                    n = list(child_notes[m_idx])
                    n[2] = random.uniform(0.4, 0.9) if n[2] == 0 else 0.0
                    child_notes[m_idx] = tuple(n)
                next_m.append(MelodyGenome(child_notes))
            melody_pop = next_m

            # Breed rhythm population
            r_elites = rhythm_pop[:6]
            next_r = r_elites[:]
            while len(next_r) < pop_size:
                p1, p2 = random.sample(r_elites, 2)
                split = random.randint(1, min(len(p1.pattern), len(p2.pattern)) - 1)
                child_pat = p1.pattern[:split] + p2.pattern[split:]
                c_dur = sum(d for d, _ in child_pat)
                if c_dur > duration_beats:
                    child_pat = child_pat[:-1]
                elif c_dur < duration_beats:
                    child_pat.append((duration_beats - c_dur, False))
                if random.random() < mutation_rate:
                    m_idx = random.randrange(len(child_pat))
                    d, a = child_pat[m_idx]
                    child_pat[m_idx] = (d, not a)
                next_r.append(RhythmGenome(child_pat))
            rhythm_pop = next_r

        # Apply best rhythm accents to best melody velocities
        final_notes = list(best_melody.notes)
        m_beat, r_idx, r_beat = 0.0, 0, 0.0
        for m_i in range(len(final_notes)):
            while r_idx < len(best_rhythm.pattern) - 1 and r_beat + best_rhythm.pattern[r_idx][0] <= m_beat + 0.01:
                r_beat += best_rhythm.pattern[r_idx][0]
                r_idx += 1
            if r_idx < len(best_rhythm.pattern) and best_rhythm.pattern[r_idx][1]:
                p, d, v = final_notes[m_i]
                final_notes[m_i] = (p, d, min(v * 1.3, 0.95))
            m_beat += final_notes[m_i][1]

        return final_notes, target_tempo

    def _synthesize(self, melody: List[Tuple[int, float, float]], tempo: float) -> np.ndarray:
        """Multi-layer synthesis: FM lead, pad chords, sub-bass, kick, reverb."""
        from scipy.signal import butter, sosfilt
        beat_dur = 60.0 / tempo
        total_dur = sum(n[1] for n in melody) * beat_dur
        sr = self.sample_rate
        total_s = int(total_dur * sr)

        lead_track = np.zeros(total_s)
        pad_track = np.zeros(total_s)
        bass_track = np.zeros(total_s)
        kick_track = np.zeros(total_s)

        energy = max(0.3, self.params.get("intensity", 0.5))
        brightness = max(0.35, self.params.get("hope", 0.5))
        isolation = self.params.get("isolation", 0.5)

        beta = 0.4 + energy * 1.5

        # --- Lead melody: dual-oscillator FM with vibrato ---
        cur_s = 0
        for pitch, dur_b, vel in melody:
            dur_s = dur_b * beat_dur
            num_s = int(dur_s * sr)
            if num_s < 2: cur_s += num_s; continue

            if vel > 0:
                f_c = self._midi_to_freq(pitch)
                t = np.linspace(0, dur_s, num_s, False)

                vibrato = 0.003 * np.sin(2 * np.pi * 5.2 * t) * np.clip(t / 0.3, 0, 1)
                fm_mod = beta * np.sin(2 * np.pi * f_c * 2.0 * t)
                osc1 = np.sin(2 * np.pi * f_c * (1.0 + vibrato) * t + fm_mod)
                osc2 = np.sin(2 * np.pi * f_c * 1.002 * t + fm_mod * 0.6) * 0.5

                a_s = max(int(sr * 0.05), int(num_s * 0.10))
                d_s = max(int(sr * 0.06), int(num_s * 0.15))
                r_s = max(int(sr * 0.10), int(num_s * 0.30))
                s_level = 0.7
                env = np.ones(num_s) * s_level
                env[:a_s] = np.linspace(0.0, 1.0, a_s)
                env[a_s:a_s + d_s] = np.linspace(1.0, s_level, d_s)
                env[-r_s:] = np.linspace(env[-r_s] if r_s < num_s else s_level, 0.0, r_s)

                wave = (osc1 + osc2) * env * vel * 0.35
                end_s = min(cur_s + num_s, total_s)
                lead_track[cur_s:end_s] += wave[:end_s - cur_s]

            cur_s += num_s

        # --- Pad layer: soft chord tones that sustain under melody ---
        is_major = brightness > 0.45
        root_midi = int(48 + brightness * 8)
        if is_major:
            chord_intervals = [0, 4, 7, 12]
        else:
            chord_intervals = [0, 3, 7, 12]
        pad_notes = [self._midi_to_freq(root_midi + iv - 12) for iv in chord_intervals]
        t_full = np.linspace(0, total_dur, total_s, False)
        for pf in pad_notes:
            slow_lfo = 0.002 * np.sin(2 * np.pi * 0.15 * t_full + pf)
            pad_osc = np.sin(2 * np.pi * pf * (1.0 + slow_lfo) * t_full)
            pad_track += pad_osc
        pad_env = np.ones(total_s)
        fade_in = min(int(sr * 2.0), total_s // 4)
        fade_out = min(int(sr * 1.5), total_s // 4)
        pad_env[:fade_in] = np.linspace(0, 1, fade_in)
        pad_env[-fade_out:] = np.linspace(1, 0, fade_out)
        pad_vol = 0.08 + isolation * 0.06
        pad_track = pad_track / max(len(pad_notes), 1) * pad_env * pad_vol

        # --- Sub-bass: continuous low sine following root ---
        sub_freq = self._midi_to_freq(root_midi - 24)
        sub_lfo = 1.0 + 0.01 * np.sin(2 * np.pi * 0.1 * t_full)
        sub_osc = np.sin(2 * np.pi * sub_freq * sub_lfo * t_full)
        sub_env = pad_env.copy()
        bass_track = sub_osc * sub_env * 0.15

        # --- Kick: punchy on downbeats, softer on offbeats ---
        beat_s = int(beat_dur * sr)
        kick_freq = self._midi_to_freq(36)
        for beat_idx in range(int(total_dur / beat_dur)):
            onset = beat_idx * beat_s
            kick_len = min(int(sr * 0.22), total_s - onset)
            if kick_len < 2: break
            t_k = np.linspace(0, 0.22, kick_len, False)
            freq_env = kick_freq * np.exp(-t_k * 14.0) + kick_freq * 0.4
            kick = np.sin(2 * np.pi * freq_env * t_k)
            kick_env = np.exp(-t_k * 12.0)
            accent = 0.45 if beat_idx % 4 == 0 else 0.25
            kick *= kick_env * accent * energy
            kick_track[onset:onset + kick_len] += kick

        # --- Mix all layers ---
        audio = lead_track * 0.45 + pad_track * 0.25 + bass_track * 0.15 + kick_track * 0.15

        # Low-pass: 4000 Hz for warmth
        sos = butter(4, 4000.0, btype='low', fs=sr, output='sos')
        audio = sosfilt(sos, audio)

        # Normalize
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio /= peak

        # Reverb: longer tail for atmosphere
        d1 = int(0.22 * sr)
        d2 = int(0.45 * sr)
        d3 = int(0.72 * sr)
        rev = np.zeros(len(audio) + d3)
        rev[:len(audio)] = audio
        rev[d1:d1 + len(audio)] += audio * 0.22
        rev[d2:d2 + len(audio)] += audio * 0.14
        rev[d3:d3 + len(audio)] += audio * 0.08
        audio = rev[:total_s]

        # Soft limiter
        audio = np.tanh(audio * 0.9)

        return audio

    def _perlin_3d(self, x, y, z):
        """Vectorized 3D Perlin Noise."""
        # Simple hashing for NumPy
        def fade(t): return t * t * t * (t * (t * 6 - 15) + 10)
        def lerp(t, a, b): return a + t * (b - a)
        
        x = np.asarray(x); y = np.asarray(y); z = np.asarray(z)
        if z.ndim == 0: z = np.full_like(x, float(z))
        X = x.astype(int) & 255
        Y = y.astype(int) & 255
        Z = z.astype(int) & 255
        xf = x - x.astype(int)
        yf = y - y.astype(int)
        zf = z - z.astype(int)
        u, v, w = fade(xf), fade(yf), fade(zf)
        
        p = self.p  # use precomputed table from __init__, not rebuilt every call
        
        def grad(hash, x, y, z):
            h = hash & 15
            u = np.where(h < 8, x, y)
            v = np.where(h < 4, y, np.where((h == 12) | (h == 14), x, z))
            return np.where(h & 1, -u, u) + np.where(h & 2, -v, v)

        A = p[X] + Y
        AA = p[A] + Z
        AB = p[A + 1] + Z
        B = p[X + 1] + Y
        BA = p[B] + Z
        BB = p[B + 1] + Z
        
        return lerp(w, lerp(v, lerp(u, grad(p[AA], xf, yf, zf), grad(p[BA], xf - 1, yf, zf)),
                               lerp(u, grad(p[AB], xf, yf - 1, zf), grad(p[BB], xf - 1, yf - 1, zf))),
                       lerp(v, lerp(u, grad(p[AA + 1], xf, yf, zf - 1), grad(p[BA + 1], xf - 1, yf, zf - 1)),
                               lerp(u, grad(p[AB + 1], xf, yf - 1, zf - 1), grad(p[BB + 1], xf - 1, yf - 1, zf - 1))))

    def _fbm(self, x, y, z, octaves=3):
        """Fractional Brownian Motion for detail."""
        v = 0.0
        a = 0.5
        f = 1.0
        for _ in range(octaves):
            v += a * self._perlin_3d(x * f, y * f, z * f)
            a *= 0.5
            f *= 2.0
        return v

    def _ridged_fbm(self, x, y, z, octaves=3):
        """Ridged fBm for sharp mountain-like structures."""
        v = 0.0
        a = 0.5
        f = 1.0
        for _ in range(octaves):
            n = self._perlin_3d(x * f, y * f, z * f)
            n = 1.0 - np.abs(n)
            n = n * n # Sharpen ridges
            v += a * n
            a *= 0.5
            f *= 2.0
        return v

    def generate(self, prompt: str, width: int = 640, height: int = 480, intent: SceneIntent = None, duration_sec: float = None):
        if not HAS_AV_LIBS:
            print("[ERROR] Audiovisual libraries (numpy, scipy, imageio) not found.")
            return

        print(f"[SYSTEM] Generating Audiovisual Scene: '{prompt}'")
        mode = intent.mode if intent else "organic"
        
        # 1. Music
        melody, tempo = self._generate_melody()
        audio = self._synthesize(melody, tempo)
        audio = np.tile(audio, 2)
        
        timestamp = int(time.time())
        wav_path = self.output_dir / f"av_{timestamp}.wav"
        wav_audio = np.int16(audio * 32767)
        wavfile.write(str(wav_path), self.sample_rate, wav_audio)
        
        # 2. Audio Analysis
        samples_per_frame = self.sample_rate // self.fps
        num_frames = len(audio) // samples_per_frame
        if duration_sec is not None:
            num_frames = min(num_frames, int(duration_sec * self.fps))
        
        # 3. Render Video
        mp4_path = self.output_dir / f"av_{timestamp}.mp4"
        writer = imageio.get_writer(str(mp4_path), fps=self.fps)
        
        yy, xx = np.mgrid[0:height, 0:width]
        
        brightness = self.params.get("hope", 0.5)
        sem_conflict = self.params.get("conflict", 0.5)
        sem_impermanence = self.params.get("impermanence", 0.5)
        sem_isolation = self.params.get("isolation", 0.5)
        sem_intensity = self.params.get("intensity", 0.5)
        sem_certainty = self.params.get("certainty", 0.5)
        sem_self_focus = self.params.get("self_focus", 0.5)

        # Derived visual parameters from semantic scores
        warp_strength = 0.5 + sem_conflict * 1.5          # domain warp intensity
        drift_speed = 0.012 + sem_impermanence * 0.025    # base z_time speed
        vignette_str = 0.3 + sem_isolation * 0.45          # edge darkening
        bloom_intensity = 0.3 + sem_intensity * 0.5        # glow on bright areas
        contrast_punch = 0.7 + sem_intensity * 0.25        # S-curve gamma
        fbm_octaves = 3 + int(sem_certainty * 3)           # noise detail (3-6)
        sat_boost = 1.1 + (1.0 - sem_isolation) * 0.4      # desaturate when isolated
        focal_weight = 0.0 + sem_self_focus * 0.3           # center brightness bias

        print(f"[SYSTEM] Rendering {num_frames} frames at {width}x{height} (Mode: {mode})...")
        print(f"  Semantic visual params: warp={warp_strength:.2f} drift={drift_speed:.3f} vig={vignette_str:.2f} bloom={bloom_intensity:.2f} oct={fbm_octaves}")

        # Pre-compute FFT bands for all frames so we can normalize by running max
        print("[SYSTEM] Pre-computing FFT bands...")
        all_bass = np.zeros(num_frames)
        all_mid  = np.zeros(num_frames)
        all_treb = np.zeros(num_frames)
        for _i in range(num_frames):
            _start = _i * samples_per_frame
            _seg = audio[_start:_start + samples_per_frame]
            if len(_seg) < samples_per_frame:
                _seg = np.pad(_seg, (0, samples_per_frame - len(_seg)))
            _spec = np.abs(fft(_seg * np.hanning(len(_seg))))[:samples_per_frame // 2]
            all_bass[_i] = np.sum(_spec[:samples_per_frame // 20])
            all_mid[_i]  = np.sum(_spec[samples_per_frame // 20:samples_per_frame // 5])
            all_treb[_i] = np.sum(_spec[samples_per_frame // 5:])
        max_bass = max(all_bass.max(), 1e-6)
        max_mid  = max(all_mid.max(),  1e-6)
        max_treb = max(all_treb.max(), 1e-6)
        all_bass /= max_bass; all_mid /= max_mid; all_treb /= max_treb

        # Smooth bands with EMA to eliminate per-frame jitter / shaky-camera effect
        alpha = 0.15
        for _i in range(1, num_frames):
            all_bass[_i] = alpha * all_bass[_i] + (1 - alpha) * all_bass[_i - 1]
            all_mid[_i]  = alpha * all_mid[_i]  + (1 - alpha) * all_mid[_i - 1]
            all_treb[_i] = alpha * all_treb[_i] + (1 - alpha) * all_treb[_i - 1]

        for i in range(num_frames):
            if i % 30 == 0: print(f"  Frame {i}/{num_frames}")

            nb = float(all_bass[i])
            nm = float(all_mid[i])
            nt = float(all_treb[i])
            
            z_time = i * (drift_speed + nt * 0.03)
            
            if mode == "terrain":
                # --- Cinematic Oblique Terrain Perspective ---
                scale = 160.0 - nb * 70.0
                
                # We render a larger internal area to allow for the perspective tilt
                # and looking "into" the distance.
                # Sample points: X is width, Y is 'depth' into the scene
                depth_samples = height
                
                # Initialize a dark frame (valley/mist floor)
                r = np.full((height, width), 20 + nt * 20, dtype=float)
                g = np.full((height, width), 15 + nm * 15, dtype=float)
                b = np.full((height, width), 25 + nb * 20, dtype=float)
                
                # Perspective height buffer to handle occlusion (simple painter's algorithm)
                # We iterate from back (far) to front (near)
                for sy in range(depth_samples - 1, -1, -1):
                    # Far rows move slower (z_time offset)
                    row_z = z_time + (sy / depth_samples) * 0.5
                    
                    # Coordinate scaling: rows further away (high sy) are sampled higher up in the noise
                    y_in = (sy + (i * 0.2)) / scale 
                    x_in = xx[0, :] / scale
                    
                    # Multi-scale terrain height
                    base = self._fbm(x_in * 0.8, y_in * 0.8, row_z, octaves=3)
                    ridges = self._ridged_fbm(x_in * 2.5, y_in * 2.5, row_z, octaves=4)
                    detail = self._fbm(x_in * 8.0, y_in * 8.0, row_z, octaves=2) * 0.12
                    
                    h_map = 0.5 * base + 0.4 * ridges + detail
                    h_map = (h_map + 1.0) / 2.0
                    h_map = np.power(h_map, 2.0) # Sharp contrast
                    
                    # Project onto screen:
                    # screen_y = base_y - height_offset
                    # base_y is the row index, height_offset is scaled by elevation
                    # Far rows (high sy) appear higher on screen (low row_idx)
                    h_scale = 60.0 + nb * 40.0
                    screen_y = sy - (h_map * h_scale)
                    screen_y = screen_y.astype(int)
                    
                    # Shading for this row
                    eps = 0.05
                    h_x1 = 0.5 * self._fbm((xx[0, :]+1)/scale * 0.8, y_in * 0.8, row_z) + 0.4 * self._ridged_fbm((xx[0, :]+1)/scale * 2.5, y_in * 2.5, row_z)
                    h_x0 = 0.5 * self._fbm((xx[0, :]-1)/scale * 0.8, y_in * 0.8, row_z) + 0.4 * self._ridged_fbm((xx[0, :]-1)/scale * 2.5, y_in * 2.5, row_z)
                    dx = (h_x1 - h_x0) * 3.0
                    dy_arr = np.full_like(h_map, -0.5) # Constant forward slope for oblique look
                    
                    nz = np.ones_like(h_map) * 0.5
                    norm = np.stack([-dx, -dy_arr, nz], axis=-1)
                    norm /= np.linalg.norm(norm, axis=-1, keepdims=True)
                    
                    light_dir = np.array([0.8, 0.4, 0.9])
                    light_dir /= np.linalg.norm(light_dir)
                    shade = 0.2 + 0.8 * np.clip(np.sum(norm * light_dir, axis=-1), 0, 1)
                    shade *= (0.4 + 0.6 * h_map) # Occulsion
                    
                    # Colors
                    r_base = np.where(h_map > 0.82, 255, np.where(h_map > 0.45, 120, 65))
                    g_base = np.where(h_map > 0.82, 250, np.where(h_map > 0.45, 110, 55))
                    b_base = np.where(h_map > 0.82, 255, np.where(h_map > 0.45, 100, 45))
                    
                    row_r = r_base * shade + nt * 30
                    row_g = g_base * shade + nm * 15
                    row_b = b_base * shade + nb * 20
                    
                    # Draw into buffer (handle only visible pixels)
                    for x in range(width):
                        py = screen_y[x]
                        if 0 <= py < height:
                            # Draw column from projected Y down to the sy row base
                            # creating a "solid" look for the terrain slice
                            end_y = min(height, sy + 1)
                            r[py:end_y, x] = row_r[x]
                            g[py:end_y, x] = row_g[x]
                            b[py:end_y, x] = row_b[x]
            elif mode == "ocean":
                # --- Ocean ---
                sky_h = int(height * 0.38)
                sky_t = yy[:sky_h, :] / max(sky_h, 1)

                # Sunset gradient sky: deep indigo top → warm amber/peach horizon
                r_sky = (25 + sky_t * 200 + nt * 12).clip(0, 255)
                g_sky = (40 + sky_t * 100 + nm * 8).clip(0, 255)
                b_sky = (140 - sky_t * 70 + nb * 6).clip(0, 255)

                # Cloud wisps in sky
                cloud_x = xx[:sky_h, :] / 120.0
                cloud_y = yy[:sky_h, :] / 60.0
                clouds = self._fbm(cloud_x, cloud_y, z_time * 0.03, octaves=fbm_octaves)
                clouds = np.clip((clouds + 1.0) / 2.0 - 0.35, 0, 1) ** 1.2
                cloud_lit = clouds * np.clip(sky_t * 2.0, 0, 1)
                r_sky = np.clip(r_sky + cloud_lit * 90, 0, 255)
                g_sky = np.clip(g_sky + cloud_lit * 55, 0, 255)
                b_sky = np.clip(b_sky + cloud_lit * 25, 0, 255)

                # Sun glow near horizon
                sun_cx = width * 0.55
                streak_x = np.abs(xx[:sky_h, :] - sun_cx) / width
                horizon_band = np.clip(1.0 - sky_t * 2.8, 0, 1) ** 1.5
                sun_haze = np.clip(1.0 - streak_x * 3.0, 0, 1) ** 2.0 * horizon_band
                r_sky = np.clip(r_sky + sun_haze * 130, 0, 255)
                g_sky = np.clip(g_sky + sun_haze * 70, 0, 255)
                b_sky = np.clip(b_sky + sun_haze * 20, 0, 255)

                # Water
                wx = xx[sky_h:, :] / 65.0
                wy = yy[sky_h:, :] / 48.0
                depth_t = (yy[sky_h:, :] - sky_h) / max(height - sky_h, 1)
                wave1 = self._perlin_3d(wx, wy, np.full_like(wx, z_time * 0.7))
                wave2 = self._perlin_3d(wx * 2.3 + 3.7, wy * 2.1, np.full_like(wx, z_time * 1.1 + 1.5)) * 0.4
                wave3 = self._perlin_3d(wx * 4.5, wy * 4.2, np.full_like(wx, z_time * 1.8)) * 0.15
                wave4 = self._perlin_3d(wx * 8.0 + 5.0, wy * 7.5, np.full_like(wx, z_time * 2.5)) * 0.06
                waves = (wave1 + wave2 + wave3 + wave4 + 1.0) / 2.0

                # Color: deep dark teal far → richer blue-green near
                r_w = (5   + depth_t * 25  + waves * (20 + nm * 14) + nb * 10).clip(0, 255)
                g_w = (30  + depth_t * 70  + waves * (50 + nm * 18) + nb * 6 ).clip(0, 255)
                b_w = (80  + depth_t * 60  + waves * (55 + nb * 20)          ).clip(0, 255)

                # Sunset reflection on water — warm orange/gold path from sun
                refl_x = np.abs(xx[sky_h:, :] - sun_cx) / width
                refl_width = 0.08 + depth_t * 0.15
                refl_core = np.exp(-0.5 * (refl_x / refl_width) ** 2)
                refl_fade = np.clip(1.0 - depth_t * 1.8, 0.05, 1)
                refl_shimmer = waves * 0.3 + 0.7
                refl = refl_core * refl_fade * refl_shimmer
                r_w = np.clip(r_w + refl * 180, 0, 255)
                g_w = np.clip(g_w + refl * 100, 0, 255)
                b_w = np.clip(b_w + refl * 25,  0, 255)

                # Specular sparkle on wave peaks
                sparkle = np.clip((waves - 0.72) * 8.0, 0, 1)
                sparkle *= refl_core * 0.4 + 0.1
                r_w = np.clip(r_w + sparkle * 200, 0, 255)
                g_w = np.clip(g_w + sparkle * 180, 0, 255)
                b_w = np.clip(b_w + sparkle * 140, 0, 255)

                # Foam on crests
                foam = np.clip((waves - 0.80) * 10.0, 0, 1)
                foam *= (1.0 - depth_t * 0.6)
                r_w = r_w + foam * (235 - r_w)
                g_w = g_w + foam * (242 - g_w)
                b_w = b_w + foam * (248 - b_w)

                r = np.zeros((height, width), dtype=float)
                g = np.zeros((height, width), dtype=float)
                b = np.zeros((height, width), dtype=float)
                r[:sky_h,:]=r_sky; g[:sky_h,:]=g_sky; b[:sky_h,:]=b_sky
                r[sky_h:,:]=r_w;   g[sky_h:,:]=g_w;   b[sky_h:,:]=b_w

            elif mode == "space_bg":
                # --- Space ---
                x_in = xx / 160.0
                y_in = yy / 160.0
                neb = self._fbm(x_in, y_in, z_time * 0.08, octaves=fbm_octaves)
                neb = (neb + 1.0) / 2.0
                neb2 = self._fbm(x_in * 1.8 + 7.3, y_in * 1.8, z_time * 0.05, octaves=max(3, fbm_octaves - 1))
                neb2 = (neb2 + 1.0) / 2.0
                # Multi-color nebula: blue core, purple mid, pink edge
                r = (neb * 80  + neb2 * 60  + nt * 25).clip(0, 255)
                g = (neb * 20  + neb2 * 15  + nm * 15).clip(0, 255)
                b = (neb * 160 + neb2 * 100 + nb * 40).clip(0, 255)
                # Nebula color tint zones
                r = np.clip(r + neb2 * 60, 0, 255)  # pink fringe
                # Stars: two sizes
                sx = (xx * 73856093) ^ (yy * 19349663)
                star_sm = ((sx % 701) < 2).astype(float) * ((sx % 255) / 255.0)
                star_lg = ((sx % 2003) < 2).astype(float)
                twinkle = 0.65 + 0.35 * np.sin(z_time * 2.5 + (xx * 0.4 + yy * 0.6) % (2*np.pi))
                star_sm *= twinkle; star_lg *= twinkle
                # Large stars get a tiny glow
                from scipy.ndimage import uniform_filter as _uf
                star_glow = _uf(star_lg, size=5) * 0.4
                r = np.clip(r + star_sm * 220 + star_lg * 255 + star_glow * 180, 0, 255)
                g = np.clip(g + star_sm * 220 + star_lg * 255 + star_glow * 160, 0, 255)
                b = np.clip(b + star_sm * 255 + star_lg * 255 + star_glow * 255, 0, 255)

            elif mode == "grassland":
                # --- Grassland ---
                sky_h = int(height * 0.42)
                sky_t = yy[:sky_h, :] / max(sky_h, 1)
                # Sky: deep azure top → pale horizon
                r_sky = (80  + sky_t * 90 + nt * 12).clip(0, 255)
                g_sky = (140 + sky_t * 70 + nm * 8 ).clip(0, 255)
                b_sky = (210 - sky_t * 30            ).clip(0, 255)
                # Fluffy clouds
                cl_x = xx[:sky_h, :] / 130.0
                cl_y = yy[:sky_h, :] / 60.0
                clouds = self._fbm(cl_x, cl_y, z_time * 0.03, octaves=fbm_octaves)
                cloud_mask = np.clip((clouds + 1.0) / 2.0 - 0.55, 0, 1) * 3.0
                r_sky = np.clip(r_sky + cloud_mask * (240 - r_sky) * 0.9, 0, 255)
                g_sky = np.clip(g_sky + cloud_mask * (245 - g_sky) * 0.9, 0, 255)
                b_sky = np.clip(b_sky + cloud_mask * (248 - b_sky) * 0.9, 0, 255)

                gx = xx[sky_h:, :] / 90.0
                gy = yy[sky_h:, :] / 70.0
                wind = self._perlin_3d(gx * 3.0, gy * 1.5, np.full_like(gx, z_time * 1.5))
                base_grass = self._fbm(gx * 0.6, gy * 0.6, z_time * 0.04, octaves=fbm_octaves)
                h_grass = (base_grass + wind * 0.18 + 1.0) / 2.0
                depth_t = (yy[sky_h:, :] - sky_h) / max(height - sky_h, 1)
                # Rich greens: dark foreground, bright highlight mid, yellow-green far
                r_g = (20  + h_grass * 55  + depth_t * 50 + nb * 8 ).clip(0, 255)
                g_g = (70  + h_grass * 130 + depth_t * 25 + nm * 18).clip(0, 255)
                b_g = (10  + h_grass * 25  + depth_t * 15           ).clip(0, 255)

                r = np.zeros((height,width),dtype=float); g = np.zeros_like(r); b = np.zeros_like(r)
                r[:sky_h,:]=r_sky; g[:sky_h,:]=g_sky; b[:sky_h,:]=b_sky
                r[sky_h:,:]=r_g;   g[sky_h:,:]=g_g;   b[sky_h:,:]=b_g

            elif mode == "forest_bg":
                # --- Forest ---
                sky_h = int(height * 0.12)
                sky_t = yy[:sky_h, :] / max(sky_h, 1)
                r_sky = (100 + sky_t * 30).clip(0, 255)
                g_sky = (160 + sky_t * 40).clip(0, 255)
                b_sky = (200 + sky_t * 20).clip(0, 255)

                cx2 = xx[sky_h:, :] / 60.0
                cy2 = yy[sky_h:, :] / 50.0
                canopy = self._fbm(cx2 * 0.8, cy2 * 1.6, z_time * 0.07, octaves=fbm_octaves)
                canopy = (canopy + 1.0) / 2.0
                depth_t = (yy[sky_h:, :] - sky_h) / max(height - sky_h, 1)

                # Volumetric-style light shafts from top
                shaft_nx = xx[sky_h:, :] / (38.0 + nb * 18.0)
                shaft = self._perlin_3d(shaft_nx, cy2 * 0.25, np.full_like(cx2, z_time * 0.04))
                shaft = np.clip((shaft + 1.0) / 2.0 - 0.38, 0, 1) ** 1.5
                shaft_fade = np.clip(1.0 - depth_t * 1.8, 0, 1)
                shaft *= shaft_fade

                # Dark rich forest greens, dappled light
                r_f = (5  + canopy * 35  + depth_t * 15 + shaft * 100 + nm * 8 ).clip(0, 255)
                g_f = (20 + canopy * 90  + depth_t * 25 + shaft * 130 + nt * 12).clip(0, 255)
                b_f = (5  + canopy * 20  + depth_t * 8  + shaft * 50            ).clip(0, 255)

                r = np.zeros((height,width),dtype=float); g = np.zeros_like(r); b = np.zeros_like(r)
                r[:sky_h,:]=r_sky; g[:sky_h,:]=g_sky; b[:sky_h,:]=b_sky
                r[sky_h:,:]=r_f;   g[sky_h:,:]=g_f;   b[sky_h:,:]=b_f

            elif mode == "sunset":
                # --- Sunset ---
                t_sky = yy / height
                horizon_dist = np.abs(t_sky - 0.60)
                horizon_glow = np.clip(1.0 - horizon_dist * 6.0, 0, 1)

                # Sky: deep purple/navy top → fiery orange/yellow horizon
                r = (15  + t_sky * 220 + horizon_glow * 100 + nt * 20).clip(0, 255)
                g = (5   + t_sky * 70  + horizon_glow * 50  + nm * 10).clip(0, 255)
                b = (80  + (1.0 - t_sky) * 130 - horizon_glow * 40   ).clip(0, 255)

                # Cloud wisps
                cx3 = xx / (110.0 - nm * 35.0)
                cy3 = yy / 55.0
                clouds = self._fbm(cx3, cy3, z_time * 0.04, octaves=fbm_octaves)
                clouds = (clouds + 1.0) / 2.0
                cloud_mask = np.clip(clouds - 0.50, 0, 1) * 2.5
                # Clouds lit orange near horizon, white higher up
                cloud_r = 240 * (1.0 - t_sky * 0.3)
                cloud_g = 140 * (1.0 - t_sky * 0.5)
                cloud_b = 60  * (1.0 - t_sky * 0.8)
                r = np.clip(r + cloud_mask * (cloud_r - r) * 0.75, 0, 255)
                g = np.clip(g + cloud_mask * (cloud_g - g) * 0.60, 0, 255)
                b = np.clip(b + cloud_mask * (cloud_b - b) * 0.40, 0, 255)

                # Sun disc + halo
                sun_x2, sun_y2 = width * 0.52, height * 0.60
                sun_d2 = np.sqrt((xx - sun_x2)**2 + (yy - sun_y2)**2)
                sun_r2 = width * 0.055
                sun_disc = np.clip(1.0 - (sun_d2 - sun_r2*0.3) / (sun_r2 * 0.7), 0, 1)
                halo     = np.clip(1.0 - sun_d2 / (sun_r2 * 6.0), 0, 1) ** 2
                r = np.clip(r + sun_disc * 255 + halo * 200, 0, 255)
                g = np.clip(g + sun_disc * 230 + halo * 90,  0, 255)
                b = np.clip(b + sun_disc * 80,               0, 255)

            else:
                # --- Abstract Liquid Mode ---
                scale = 85.0
                x_in, y_in = xx / scale, yy / scale
                wf = warp_strength
                q_x = self._fbm(x_in, y_in, z_time, octaves=fbm_octaves)
                q_y = self._fbm(x_in + 5.2, y_in + 1.3, z_time, octaves=fbm_octaves)
                r_x = self._fbm(x_in + wf * 2.5 * q_x + 1.7, y_in + wf * 2.5 * q_y + 9.2, z_time, octaves=fbm_octaves)
                r_y = self._fbm(x_in + wf * 2.5 * q_x + 8.3, y_in + wf * 2.5 * q_y + 2.8, z_time, octaves=fbm_octaves)
                f = self._fbm(x_in + wf * 2.5 * r_x, y_in + wf * 2.5 * r_y, z_time, octaves=fbm_octaves)

                f = (f + 1.0) / 2.0
                z_val = np.clip(-10 * (f - 0.5), -500, 500)
                f = 1.0 / (1.0 + np.exp(z_val))

                r = 30 + f * (60 + brightness * 195) + nt * 60
                g = 20 + f * (40 + (1.0 - abs(brightness - 0.5) * 2) * 120) + nm * 50
                b = 40 + f * (80 + (1.0 - brightness) * 135) + nb * 80
            
            frame = np.stack([r, g, b], axis=-1).clip(0, 255)

            # --- Post-processing pipeline ---
            f32 = frame / 255.0

            # 1. Gamma correction — intensity drives contrast punch
            gamma = contrast_punch
            f32 = np.power(np.clip(f32, 1e-6, 1.0), gamma)

            # 2. S-curve contrast
            f32 = f32 * (f32 * (3.0 - 2.0 * f32))

            # 3. Saturation — isolation desaturates, connection saturates
            lum = 0.299 * f32[..., 0] + 0.587 * f32[..., 1] + 0.114 * f32[..., 2]
            lum3 = lum[..., np.newaxis]
            f32 = np.clip(lum3 + sat_boost * (f32 - lum3), 0.0, 1.0)

            # 4. Bloom — intensity drives glow strength
            bright_mask = np.clip(f32 - (0.85 - bloom_intensity * 0.3), 0, 1) * (2.5 + bloom_intensity * 2.0)
            from scipy.ndimage import uniform_filter
            bloom_r = uniform_filter(bright_mask[..., 0], size=max(width // 24, 3))
            bloom_g = uniform_filter(bright_mask[..., 1], size=max(width // 24, 3))
            bloom_b = uniform_filter(bright_mask[..., 2], size=max(width // 24, 3))
            bloom = np.stack([bloom_r, bloom_g, bloom_b], axis=-1)
            f32 = np.clip(f32 + bloom * (0.3 + bloom_intensity * 0.5), 0.0, 1.0)

            # 5. Vignette — isolation darkens edges more
            vcx, vcy = width / 2.0, height / 2.0
            vig_y, vig_x = np.mgrid[0:height, 0:width]
            vig = 1.0 - vignette_str * np.clip(((vig_x - vcx) ** 2 + (vig_y - vcy) ** 2) / (vcx * vcy * 2.2), 0, 1)
            f32 *= vig[..., np.newaxis]

            # 6. Self-focus: brighten center region
            if focal_weight > 0.02:
                focus = np.exp(-0.5 * ((vig_x - vcx) ** 2 / (vcx * 0.6) ** 2 + (vig_y - vcy) ** 2 / (vcy * 0.6) ** 2))
                f32 = np.clip(f32 + focus[..., np.newaxis] * focal_weight, 0.0, 1.0)

            # 7. Subtle film grain
            grain = np.random.normal(0, 0.012, f32.shape)
            f32 = np.clip(f32 + grain, 0.0, 1.0)

            frame = (f32 * 255.0).astype(np.uint8)
            writer.append_data(frame)
            
        writer.close()

        # Mux audio+video into single web-compatible MP4 (H264 + AAC, faststart)
        final_path = self.output_dir / f"av_{timestamp}_final.mp4"
        try:
            import subprocess
            result = subprocess.run([
                "ffmpeg", "-y",
                "-i", str(mp4_path),
                "-i", str(wav_path),
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                "-shortest",
                str(final_path)
            ], capture_output=True, text=True)
            if result.returncode == 0:
                mp4_path.unlink(missing_ok=True)
                mp4_path = final_path
            else:
                print(f"[WARN] ffmpeg mux failed: {result.stderr[-300:]}")
        except FileNotFoundError:
            print("[WARN] ffmpeg not found — video and audio saved separately")

        print(f"[SYSTEM] Audiovisual outputs saved to {self.output_dir}")
        print(f"  Audio: {wav_path}")
        print(f"  Video: {mp4_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Meaning-driven evolution under entropy pressure.")
    parser.add_argument("--text", type=str, default=None, help="Conceptual seed text.")
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--gens", type=int, default=26, help="Generations per frame.")
    parser.add_argument("--population", type=int, default=80)
    parser.add_argument("--width", type=int, default=120)
    parser.add_argument("--height", type=int, default=45)
    parser.add_argument("--ascii-mode", choices=("short", "detailed"), default="short")
    parser.add_argument("--contrast", type=int, default=0, help="ASCII contrast shaping, -10..10.")
    parser.add_argument("--save-dir", type=str, default="outputs")
    parser.add_argument("--no-early-stop", action="store_true", help="Disable adaptive generation early stopping.")
    parser.add_argument("--min-gens", type=int, default=8, help="Minimum generations before early stop can trigger.")
    parser.add_argument("--patience", type=int, default=6, help="Plateau generations tolerated before stopping.")
    parser.add_argument("--eps", type=float, default=0.0015, help="Minimum fitness gain treated as improvement.")
    parser.add_argument("--no-animate", action="store_true", help="Print frames sequentially instead of in-place animation.")
    parser.add_argument("--quiet", action="store_true", help="Disable live frame printing for maximum speed.")
    parser.add_argument("--workers", type=int, default=1, help="Parallel worker processes for evaluation (1 = disabled).")
    parser.add_argument("--timing", action="store_true", help="Print per-frame runtime and throughput summary.")
    parser.add_argument(
        "--subject-mode",
        choices=("auto", "procedural", "image"),
        default="auto",
        help="Subject renderer mode: auto uses image API when available, then procedural fallback.",
    )
    parser.add_argument("--image-model", type=str, default="gpt-image-1", help="Image model used for subject generation.")
    parser.add_argument("--image-size", type=str, default="512x512", help="Requested image generation size.")
    parser.add_argument("--image-timeout", type=float, default=30.0, help="Timeout seconds for subject image generation.")
    parser.add_argument("--image-cache-dir", type=str, default=".subject_cache", help="Cache directory for generated subject sprites.")
    parser.add_argument("--frame-delay", type=float, default=0.12, help="Seconds to pause after each displayed frame.")
    parser.add_argument("--frame-kick", type=float, default=0.18, help="Extra between-frame mutation force (0..1).")
    parser.add_argument("--seed", type=int, default=None, help="Fixed RNG seed. Same seed => same output.")
    parser.add_argument("--random-seed", action="store_true", help="Use a time-based seed for non-repeating output.")
    
    # Audiovisual arguments
    parser.add_argument("--audiovisual", action="store_true", help="Generate a high-clarity audiovisual video scene.")
    parser.add_argument("--av-width", type=int, default=640)
    parser.add_argument("--av-duration", type=float, default=None, help="Clip length in seconds (default: full length)")
    parser.add_argument("--av-height", type=int, default=480)
    
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    text = args.text
    if not text:
        print("===============================================")
        print("  COSMIC ENTROPY: EVOLUTION UNDER PRESSURE")
        print("===============================================")
        print("Input a conceptual seed (1-2 sentences).")
        text = input("> ").strip()

    system = CosmicEntropyGA(
        width=args.width,
        height=args.height,
        ascii_mode=args.ascii_mode,
        contrast=max(-10, min(10, args.contrast)),
    )
    
    if args.audiovisual:
        scores = system.analyzer.analyze(text)
        scene_intent = parse_scene_intent(text)
        av = AudiovisualGenerator(scores, output_dir=os.path.join(args.save_dir, "audiovisual"))
        av.generate(text, width=args.av_width, height=args.av_height, intent=scene_intent, duration_sec=args.av_duration)
        return

    chosen_seed = args.seed
    if args.random_seed and chosen_seed is None:
        chosen_seed = int(time.time_ns() % (2**31 - 1))

    system.evolve(
        seed_text=text,
        frames=max(2, args.frames),
        generations_per_frame=max(4, args.gens),
        population_size=max(20, args.population),
        save_dir=args.save_dir,
        early_stop=not args.no_early_stop,
        min_generations=max(2, args.min_gens),
        patience=max(2, args.patience),
        improvement_eps=max(0.0, args.eps),
        animate=not args.no_animate,
        frame_delay=max(0.0, args.frame_delay),
        quiet=args.quiet,
        workers=max(1, args.workers),
        timing=args.timing,
        subject_mode=args.subject_mode,
        image_model=args.image_model,
        image_size=args.image_size,
        image_timeout=max(5.0, args.image_timeout),
        image_cache_dir=args.image_cache_dir,
        seed=chosen_seed,
        frame_kick=clamp(args.frame_kick),
    )


if __name__ == "__main__":
    main()



