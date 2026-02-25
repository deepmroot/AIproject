import argparse
import hashlib
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


SHORT_RAMP = " .:-=+*#%@█"
DETAILED_RAMP = (
    "$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\\|()1{}[]?-_+~<>i!lI;:,\"^`'. "
)


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


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
                "dust",
                "fade",
                "gone",
                "end",
                "die",
                "nothing",
                "ruin",
                "decay",
                "time",
                "temporary",
                "lost",
                "fall",
            },
            "conflict": {
                "war",
                "fight",
                "struggle",
                "break",
                "destroy",
                "against",
                "clash",
                "chaos",
                "control",
                "force",
                "tear",
            },
            "hope": {
                "survive",
                "light",
                "rebuild",
                "new",
                "start",
                "live",
                "seed",
                "dawn",
                "future",
                "hope",
                "persist",
                "create",
            },
            "certainty": {
                "always",
                "forever",
                "must",
                "truth",
                "will",
                "absolute",
                "law",
                "inevitable",
                "fact",
                "certain",
                "bound",
            },
            "intensity": {
                "fire",
                "burn",
                "explode",
                "scream",
                "crush",
                "power",
                "all",
                "blood",
                "rage",
                "hard",
            },
            "isolation": {
                "alone",
                "void",
                "empty",
                "cold",
                "silence",
                "space",
                "dark",
                "single",
                "only",
                "apart",
                "solitude",
            },
            "self_focus": {
                "i",
                "me",
                "my",
                "self",
                "identity",
                "mind",
                "soul",
                "core",
                "center",
            },
        }
        self.negations = {"not", "no", "never", "without", "false", "illusion"}
        self.hedges = {"maybe", "perhaps", "might", "could", "possibly"}

    def analyze(self, text: str) -> Dict[str, float]:
        words = re.findall(r"\b\w+\b", text.lower())
        wc = len(words)
        scores = {k: 0.0 for k in self.lexicons}
        if wc == 0:
            return scores

        for i, word in enumerate(words):
            left = words[max(0, i - 2) : i]
            negated = any(n in left for n in self.negations)
            for category, lexicon in self.lexicons.items():
                if word in lexicon:
                    scores[category] += 0.5 if negated else 1.0

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

        # Prevent tiny speck solutions from winning.
        if m["ink"] < 0.08:
            total -= (0.08 - m["ink"]) * 1.5

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
        return {
            "symmetry": Metrics.symmetry(grid),
            "void": Metrics.void_ratio(grid),
            "ink": Metrics.ink_ratio(grid),
            "edge": Metrics.edge_density(grid),
            "fragmentation": Metrics.fragmentation(grid),
            "center": Metrics.central_focus(grid),
            "entropy": Metrics.char_entropy(grid),
        }

    def _evaluate_population(
        self,
        population: Sequence[Sequence[Primitive]],
        scores: Dict[str, float],
        pressure: float,
        phase: str,
        targets: Dict[str, float],
        structure_cache: Dict[Tuple[Tuple[str, int, int, int, int, int], ...], Tuple[List[List[float]], Dict[str, float]]],
    ) -> List[Tuple[float, Dict[str, float], List[List[float]], List[Primitive]]]:
        evaluated = []
        for genome in population:
            sig = genome_signature(genome)
            cached = structure_cache.get(sig)
            if cached is None:
                grid = self.renderer.render_density(genome)
                m_base = self._measure(grid)
                if len(structure_cache) > 5000:
                    structure_cache.clear()
                structure_cache[sig] = (grid, m_base)
            else:
                grid, m_base = cached

            m = dict(m_base)
            fit, term_scores = self.model.evaluate(m, targets, scores, pressure, phase)
            m["fitness"] = fit
            m["terms"] = term_scores
            evaluated.append((fit, m, grid, genome))  # Keep reference; clone only when selected.

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
        seed: int | None = None,
        frame_kick: float = 0.18,
    ) -> None:
        if seed is None:
            seed_hash = hashlib.md5(seed_text.encode("utf-8")).hexdigest()
            rng_seed = int(seed_hash, 16)
        else:
            rng_seed = int(seed)
            seed_hash = hashlib.md5(f"{seed_text}|{rng_seed}".encode("utf-8")).hexdigest()
        rng = random.Random(rng_seed)
        ga = GeneticEngine(self.renderer, rng)

        scores = self.analyzer.analyze(seed_text)
        print("\n--- SEMANTIC QUANTIFICATION ---")
        for k, v in scores.items():
            bar = ("#" * int(v * 20)).ljust(20)
            print(f"{k.ljust(15)} : [{bar}] {v:.2f}")
        print("-------------------------------")

        population = ga.spawn_population(population_size)
        frame_records: List[str] = []
        previous_metrics: Dict[str, float] | None = None
        best_genome: List[Primitive] | None = None
        targets = self.model.targets(scores)
        structure_cache: Dict[
            Tuple[Tuple[str, int, int, int, int, int], ...], Tuple[List[List[float]], Dict[str, float]]
        ] = {}
        animate_active = animate and sys.stdout.isatty()
        ansi_ok = os.environ.get("TERM", "").lower() not in {"", "dumb"}

        def render_block(block: str) -> None:
            if not animate_active:
                print(f"\n{block}")
                return
            if ansi_ok:
                print("\x1b[H\x1b[2J" + block, end="", flush=True)
            else:
                # Fallback for limited terminals where ANSI clear is not supported.
                rc = subprocess.run(["clear"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
                if rc != 0:
                    print("\n" * 120, end="")
                print(block, end="", flush=True)
            if frame_delay > 0:
                time.sleep(frame_delay)

        if animate_active:
            # Hide cursor during animation and restore in finally block.
            print("\x1b[?25l", end="", flush=True)

        try:
            for frame in range(frames):
                pressure = frame / max(1, frames - 1)
                phase = "order" if frame == 0 else "pressure"
                if frame >= frames - 2:
                    phase = "collapse"

                best_seen = -1.0
                stagnant = 0
                gens_run = 0

                for gen in range(generations_per_frame):
                    ranked = self._evaluate_population(
                        population, scores, pressure, phase, targets=targets, structure_cache=structure_cache
                    )
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
                    elites = [item[3] for item in ranked[:elite_n]]

                    next_pop = [[Primitive(**vars(p)) for p in g] for g in elites]
                    while len(next_pop) < population_size:
                        pa = elites[rng.randrange(len(elites))]
                        pb = elites[rng.randrange(len(elites))]
                        child = ga.crossover(pa, pb, pressure)
                        ga.mutate(child, pressure)
                        next_pop.append(child)

                    population = next_pop

                ranked = self._evaluate_population(
                    population, scores, pressure, phase, targets=targets, structure_cache=structure_cache
                )
                best_fit, best_metrics, best_grid, best_genome = ranked[0]
                ascii_frame = self.renderer.to_ascii(best_grid)

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

                # Force scene drift between frames so adjacent frames are not static clones.
                if frame < frames - 1 and frame_kick > 0:
                    kick_rate = clamp(
                        frame_kick
                        + 0.2 * pressure
                        + 0.15 * scores["impermanence"]
                        + 0.12 * scores["conflict"]
                    )
                    for genome in population:
                        if rng.random() < kick_rate:
                            ga.mutate(genome, clamp(pressure + 0.35))

            if scores["hope"] >= 0.25 and best_genome is not None:
                population = ga.seeded_population(best_genome, population_size, pressure=0.55)
                rebirth_total_gens = generations_per_frame + 10
                rebirth_best_seen = -1.0
                rebirth_stagnant = 0
                for gen in range(rebirth_total_gens):
                    ranked = self._evaluate_population(
                        population,
                        scores,
                        pressure=0.35,
                        phase="rebirth",
                        targets=targets,
                        structure_cache=structure_cache,
                    )
                    frame_best = ranked[0][0]
                    if frame_best > rebirth_best_seen + improvement_eps:
                        rebirth_best_seen = frame_best
                        rebirth_stagnant = 0
                    else:
                        rebirth_stagnant += 1
                    if early_stop and gen + 1 >= min_generations and rebirth_stagnant >= patience:
                        break

                    elites = [item[3] for item in ranked[: max(5, population_size // 7)]]
                    next_pop = [[Primitive(**vars(p)) for p in g] for g in elites]
                    while len(next_pop) < population_size:
                        pa = elites[rng.randrange(len(elites))]
                        pb = elites[rng.randrange(len(elites))]
                        child = ga.crossover(pa, pb, pressure=0.35)
                        ga.mutate(child, pressure=0.35)
                        next_pop.append(child)
                    population = next_pop

                best_fit, best_metrics, best_grid, _ = self._evaluate_population(
                    population,
                    scores,
                    pressure=0.35,
                    phase="rebirth",
                    targets=targets,
                    structure_cache=structure_cache,
                )[0]
                ascii_frame = self.renderer.to_ascii(best_grid)
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
        finally:
            if animate_active:
                print("\x1b[?25h", end="", flush=True)


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
    parser.add_argument("--frame-delay", type=float, default=0.12, help="Seconds to pause after each displayed frame.")
    parser.add_argument("--frame-kick", type=float, default=0.18, help="Extra between-frame mutation force (0..1).")
    parser.add_argument("--seed", type=int, default=None, help="Fixed RNG seed. Same seed => same output.")
    parser.add_argument("--random-seed", action="store_true", help="Use a time-based seed for non-repeating output.")
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
        seed=chosen_seed,
        frame_kick=clamp(args.frame_kick),
    )


if __name__ == "__main__":
    main()
