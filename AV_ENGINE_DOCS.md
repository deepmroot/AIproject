# Cosmic Entropy Engine: Audiovisual Expansion Documentation

## Project Overview
The Cosmic Entropy Engine has been expanded from a pure ASCII evolutionary generator into a high-fidelity **Computational Audiovisual Generative System**. The system now bridges semantic text analysis, evolutionary music theory, and procedural 3D graphics (Perlin-based) into a unified real-time pipeline.

---

## 1. System Architecture & Pipeline
The generation follows a linear, meaning-driven pipeline:
1.  **Semantic Parser**: Extracts emotional and environmental "intents" from user text.
2.  **Evolutionary Music Engine**: Evolves a melody genome based on those intents.
3.  **FM Synthesis & Analysis**: Generates rich audio and performs FFT to extract control signals.
4.  **Procedural Visual Engine**: Uses 3D Perlin Noise + Domain Warping or Cinematic Terrain Shading.
5.  **Audiovisual Linkage**: Maps FFT bands (Bass, Mid, Treble) to visual parameters (Scale, Warp, Elevation).

---

## 2. Technical Implementation Details

### A. Semantic Intent Analysis
- **Rule-Based Mapping**: Keywords are mapped to a 4D vector: `[Energy, Motion, Brightness, Density]`.
- **Mode Switching**: The engine detects "Modes" (Radial, Branching, Geometric, Terrain) to choose the rendering algorithm.
- **Landscape Detection**: Detects "rocky", "mountain", "terrain" to trigger the Oblique Relief Engine.

### B. Evolutionary Music Generation (from scratch)
- **Genome**: A sequence of tuples `(pitch, duration, velocity)`.
- **Fitness Function**: Evaluates melodies based on Scale Conformity (Major/Minor Pentatonic), Pitch Smoothness, and Rhythmic Complexity.
- **Genetic Operators**: Roulette selection, single-point crossover, and pitch/duration mutation.

### C. Audio Synthesis (FM Synthesis)
- **Algorithm**: Frequency Modulation (FM) Synthesis implemented from scratch using NumPy.
- **Enhancements**: 
    - **ADSR Envelopes**: Attack, Decay, Sustain, Release for percussive or pad-like clarity.
    - **Temporal Effects**: Custom delay and reverb-simulation via signal feedback loops.

### D. Advanced Visual Algorithms

#### 1. Core Noise Engine
- **Vectorized 3D Perlin Noise**: Fast, NumPy-based implementation of 3D (X, Y, Time) noise.
- **Fractional Brownian Motion (fBm)**: Summing multiple octaves of noise for multi-scale natural detail.
- **Ridged fBm**: A specialized variation (`1.0 - abs(noise)**2`) used to create sharp mountain ridges and jagged rock faces.

#### 2. Abstract Liquid Mode
- **Domain Warping**: A sophisticated technique where noise coordinates are recursively distorted by other noise fields, creating fluid, organic visuals.
- **Sigmoid Contrast**: High-contrast shaping for a punchy, "alive" visual feel.

#### 3. Cinematic Terrain Mode (New Overhaul)
- **Multi-Layer Heightmapping**: Combines a broad landmass base, sharp ridge chains, and high-frequency rock breakup.
- **Oblique Perspective Projection**: A custom 2.5D rendering pipeline that projects 3D heightfields onto a 2D screen using an angled camera.
- **Lambertian Shading**: Calculates surface normals using finite differences to simulate dramatic directional lighting.
- **Environmental Post-Processing**:
    - **Ambient Occlusion**: Approximated valley darkening to provide grounded depth.
    - **Snow Capping**: Elevation-based color shifting for realistic mountain peaks.
    - **Atmospheric Mist**: Valley floor tinting based on audio bass energy.

---

## 3. Libraries & Dependencies
The system relies on standard numerical and media libraries (no external AI frameworks):
- `numpy`: High-speed vector operations for noise and audio.
- `scipy.io.wavfile`: Exporting generated audio.
- `scipy.fft`: Analyzing audio frequency bands for real-time visual modulation.
- `imageio`: High-performance video frame encoding.
- `imageio-ffmpeg`: Plugin for MP4 container support.

---

## 4. Key Functions & Methods

| Function | Responsibility |
| :--- | :--- |
| `AudiovisualGenerator._generate_melody` | Runs the GA to create a note sequence. |
| `AudiovisualGenerator._synthesize` | Performs FM synthesis on the evolved melody. |
| `AudiovisualGenerator._perlin_3d` | Vectorized raw 3D noise calculation. |
| `AudiovisualGenerator._fbm` | Standard multi-octave detail blending. |
| `AudiovisualGenerator._ridged_fbm` | Generates sharp ridges for terrain features. |
| `AudiovisualGenerator.generate` | Master loop: manages audio analysis, perspective rendering, and video encoding. |
| `parse_scene_intent` | Analyzes text to determine if the scene is "organic" or "terrain". |

---

## 5. Summary of Edits to Original Engine
- **Class Integration**: Integrated the `AudiovisualGenerator` directly into the `cosmic_entropy.py` core.
- **CLI Overhaul**: Expanded the interface with `--audiovisual`, `--av-width`, and `--av-height`.
- **Intelligent Synchronization**: Synced the high-res video engine with the ASCII engine's `SceneIntent` system, ensuring thematic consistency between the text log and the generated video.
- **Output Management**: Automated creation of the `outputs/audiovisual/` directory with timestamped filenames.

---
**Date**: March 11, 2026
**System Status**: Fully Operational. Cinematic Topographic and Abstract outputs verified.
