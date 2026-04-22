# AIproject

A local Python playground for procedural generation with three main workflows:

1. **ASCII converter** for images and videos
2. **Cosmic Entropy** for meaning-driven ASCII evolution
3. **Audiovisual generation** for synthesized audio + procedural video

The repo also includes a small Tkinter desktop app that wraps the ASCII converter and the Cosmic Entropy engine.

## What this project does

### 1) ASCII conversion
`ascii_converter.py` converts:
- images -> ASCII text, optionally rendered back to PNG/JPG
- videos -> ASCII video, optional text frame dumps, optional terminal preview

### 2) Cosmic Entropy engine
`cosmic_entropy.py` turns a text prompt into evolving ASCII frames using:
- rule-based semantic analysis
- a genetic algorithm over visual primitives
- scene intent parsing for subjects like stars, trees, sunflowers, terrain, and geometric forms
- optional subject-image generation with API-backed fallback caching

### 3) Audiovisual mode
`cosmic_entropy.py --audiovisual` adds a second output path that generates:
- synthesized WAV audio
- procedural MP4 video

There is also a standalone prototype in `audiovisual_system.py`.

### 4) Desktop UI
`ascii_entropy_app.py` provides a Tkinter GUI for:
- running the converter
- running Cosmic Entropy
- viewing logs
- browsing generated outputs
- previewing image/video results

## Repository layout

- `ascii_converter.py` - CLI image/video to ASCII converter
- `cosmic_entropy.py` - main meaning-driven ASCII evolution engine
- `ascii_entropy_app.py` - Tkinter GUI for converter + entropy workflows
- `audiovisual_system.py` - standalone audiovisual prototype
- `AV_ENGINE_DOCS.md` - notes on the audiovisual expansion
- `run_ascii_entropy_app.bat` - convenience launcher for Windows
- `test_inputs/` - sample local test media
- `test_outputs/` - generated test artifacts
- `outputs/` - regular generation outputs
- `.subject_cache/` - cached subject sprites for image-backed subject generation

## Requirements

This project is written in Python and uses local desktop/media libraries.

### Core dependencies
Install these for the main workflows:

```bash
pip install opencv-python numpy scipy imageio imageio-ffmpeg
```

### GUI extras
Optional, for drag-and-drop in the Tkinter app:

```bash
pip install tkinterdnd2
```

### Optional image-subject generation dependencies
Used by `cosmic_entropy.py` when `--subject-mode auto` or `image` is selected and an API key is available:

```bash
pip install pillow requests
```

### Optional API key
For image-backed subject generation in Cosmic Entropy:

- set `OPENAI_API_KEY`
- otherwise the engine falls back to procedural subject rendering

## Quick start

### Run the desktop app

```bash
python ascii_entropy_app.py
```

Or on Windows:

```bash
run_ascii_entropy_app.bat
```

### Convert an image to ASCII

```bash
python ascii_converter.py image \
  --input test_inputs/tree.jpg \
  --output test_outputs/tree_ascii.txt \
  --output-image test_outputs/tree_ascii.png
```

### Convert a video to ASCII

```bash
python ascii_converter.py video \
  --input test_inputs/beach.mp4 \
  --output test_outputs/beach_ascii.mp4 \
  --text-frames-dir test_outputs/beach_ascii_frames \
  --width 120
```

### Run Cosmic Entropy ASCII evolution

```bash
python cosmic_entropy.py \
  --text "show a dying star over a ruined city" \
  --frames 8 \
  --gens 12 \
  --population 80 \
  --width 120 \
  --height 45 \
  --no-animate \
  --timing
```

### Run audiovisual generation

```bash
python cosmic_entropy.py \
  --text "a drifting cosmic storm over mountains" \
  --audiovisual \
  --av-width 640 \
  --av-height 480 \
  --save-dir outputs
```

### Run standalone audiovisual prototype

```bash
python audiovisual_system.py "calm drifting space"
```

## CLI reference

## `ascii_converter.py`

### Image mode

```bash
python ascii_converter.py image --input INPUT [options]
```

Key options:
- `--output` - output text file
- `--output-image` - rendered PNG/JPG output
- `--width` - number of ASCII columns
- `--row-scale` - row height correction
- `--ramp` - brightness ramp characters
- `--invert` - invert luminance mapping
- `--clahe-clip`, `--clahe-grid` - local contrast controls
- `--gamma`, `--sharpen`, `--edge-weight`, `--dither` - quality tuning
- `--no-colorize` - grayscale output instead of colorized rendering
- `--color-sat`, `--color-val` - color boost controls
- `--print` - print ASCII to terminal
- `--ansi-color-print` - truecolor terminal output when printing

### Video mode

```bash
python ascii_converter.py video --input INPUT [options]
```

Key options:
- `--output` - output video path
- `--fps` - output fps, default uses source fps
- `--codec` - four-character codec, default `mp4v`
- `--max-frames` - cap conversion length
- `--text-frames-dir` - save a `.txt` file per frame
- `--progress-every` - progress interval
- `--no-preview` - disable terminal preview
- `--preview-every` - preview every Nth frame
- `--preview-fps` - limit terminal preview rate
- plus the same width/ramp/quality/color options as image mode

## `cosmic_entropy.py`

```bash
python cosmic_entropy.py --text TEXT [options]
```

Key options:
- `--frames` - number of output frames
- `--gens` - generations per frame
- `--population` - population size
- `--width`, `--height` - ASCII frame dimensions
- `--ascii-mode {short,detailed}` - character ramp preset
- `--contrast` - ASCII contrast shaping
- `--save-dir` - output directory
- `--no-early-stop` - disable adaptive early stopping
- `--min-gens`, `--patience`, `--eps` - early stop tuning
- `--no-animate` - print sequentially instead of in-place animation
- `--quiet` - disable live printing
- `--workers` - parallel evaluation workers
- `--timing` - print runtime summary
- `--frame-delay` - pause between displayed frames
- `--frame-kick` - extra between-frame mutation force
- `--seed` - fixed RNG seed
- `--random-seed` - use time-based seed

Subject-generation options:
- `--subject-mode {auto,procedural,image}`
- `--image-model`
- `--image-size`
- `--image-timeout`
- `--image-cache-dir`

Audiovisual options:
- `--audiovisual`
- `--av-width`
- `--av-height`

## Outputs

### Converter outputs
Common outputs include:
- ASCII text files
- rendered ASCII images
- rendered ASCII videos
- optional text frame folders

### Cosmic Entropy outputs
By default, the engine writes text outputs under `outputs/`.

### Audiovisual outputs
Audiovisual mode writes timestamped files under:

- `outputs/audiovisual/*.wav`
- `outputs/audiovisual/*.mp4`

## Notes on current status

This repository is currently an experimental project rather than a packaged library.

A few important notes:
- there is no dependency lockfile yet
- `test_outputs/` contains many generated artifacts
- `audiovisual_system.py` and the audiovisual block inside `cosmic_entropy.py` overlap conceptually
- `AV_ENGINE_DOCS.md` documents the intended audiovisual direction, but the README should be treated as the main entry point

## Suggested workflow

If you are new to the project:
1. start with `ascii_entropy_app.py`
2. try `ascii_converter.py` on a sample image
3. run `cosmic_entropy.py` in ASCII mode
4. then try `cosmic_entropy.py --audiovisual`

## Troubleshooting

### `opencv-python` import errors
Install:

```bash
pip install opencv-python numpy
```

### MP4 writing fails
Try a different codec in video mode:
- `mp4v`
- `XVID`
- `MJPG`

Example:

```bash
python ascii_converter.py video --input test_inputs/beach.mp4 --output test_outputs/out.avi --codec XVID
```

### No drag-and-drop in the GUI
Install:

```bash
pip install tkinterdnd2
```

### Image subject mode does not use API generation
Make sure:
- `OPENAI_API_KEY` is set
- `requests` and `Pillow` are installed
- `--subject-mode` is `auto` or `image`

## License

No license file is currently present in this repository.
