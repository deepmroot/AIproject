# Usage Guide

## GUI

Launch:

```bash
python ascii_entropy_app.py
```

The app includes two tabs:
- **Converter**
- **Entropy (GA)**

It also has:
- a live CLI log panel
- a results browser
- image/video previews
- quick open buttons for project output folders

## Converter examples

### Image -> text + PNG

```bash
python ascii_converter.py image \
  --input test_inputs/tree.jpg \
  --output test_outputs/tree_ascii.txt \
  --output-image test_outputs/tree_ascii.png \
  --width 120 \
  --color-sat 1.25 \
  --color-val 1.05
```

### Image -> terminal ASCII

```bash
python ascii_converter.py image \
  --input test_inputs/tree.jpg \
  --print \
  --ansi-color-print
```

### Video -> ASCII MP4 + text frames

```bash
python ascii_converter.py video \
  --input test_inputs/beach.mp4 \
  --output test_outputs/beach_ascii.mp4 \
  --text-frames-dir test_outputs/beach_ascii_frames \
  --width 120 \
  --temporal-alpha 0.82
```

## Cosmic Entropy examples

### Basic run

```bash
python cosmic_entropy.py \
  --text "show a dying star over a ruined city" \
  --frames 8 \
  --gens 12 \
  --population 80
```

### Reproducible run

```bash
python cosmic_entropy.py \
  --text "a lone tree in a void" \
  --seed 12345 \
  --no-animate
```

### Procedural subject mode only

```bash
python cosmic_entropy.py \
  --text "a sunflower in static" \
  --subject-mode procedural
```

### Image-backed subject mode

```bash
python cosmic_entropy.py \
  --text "a star collapsing over mountains" \
  --subject-mode image
```

## Audiovisual examples

### Main audiovisual path through Cosmic Entropy

```bash
python cosmic_entropy.py \
  --text "a drifting cosmic storm over mountains" \
  --audiovisual \
  --av-width 640 \
  --av-height 480
```

### Standalone prototype

```bash
python audiovisual_system.py "calm drifting space"
```

## Output locations

- `outputs/` - main Cosmic Entropy outputs
- `outputs/audiovisual/` - WAV + MP4 audiovisual outputs
- `test_outputs/` - ad hoc converter and test artifacts
