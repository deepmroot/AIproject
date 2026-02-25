import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import time
from typing import List, Tuple


DEFAULT_RAMP = " .:-=+*#%@"
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv"}


def require_cv2():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:  # pragma: no cover - dependency guard
        msg = (
            "Missing dependency: opencv-python (and numpy).\n"
            "Install with: pip install opencv-python\n"
            f"Original import error: {exc}"
        )
        raise RuntimeError(msg) from exc
    return cv2, np


def normalize_ramp(ramp: str, invert: bool) -> str:
    ramp = ramp or DEFAULT_RAMP
    if len(ramp) < 2:
        raise ValueError("Ramp must contain at least 2 characters.")
    return ramp[::-1] if invert else ramp


@dataclass
class QualityOptions:
    clahe_clip: float = 1.4
    clahe_grid: int = 8
    gamma: float = 1.0
    sharpen: float = 0.12
    edge_weight: float = 0.08
    dither: float = 0.03
    temporal_alpha: float = 0.0


def quality_from_args(args: argparse.Namespace, temporal_alpha: float) -> QualityOptions:
    return QualityOptions(
        clahe_clip=max(0.0, float(args.clahe_clip)),
        clahe_grid=max(2, int(args.clahe_grid)),
        gamma=max(0.1, float(args.gamma)),
        sharpen=max(0.0, float(args.sharpen)),
        edge_weight=min(1.0, max(0.0, float(args.edge_weight))),
        dither=min(1.0, max(0.0, float(args.dither))),
        temporal_alpha=min(0.98, max(0.0, float(temporal_alpha))),
    )


def _resize_for_ascii(gray, width: int, row_scale: float) -> Tuple[int, int]:
    h, w = gray.shape[:2]
    if w <= 0 or h <= 0:
        return 8, 4
    out_w = max(8, int(width))
    # Characters are taller than they are wide, so compress rows.
    out_h = max(4, int((h / w) * out_w * row_scale))
    return out_w, out_h


def _preprocess_gray(gray, q: QualityOptions):
    cv2, np = require_cv2()
    out = gray.copy()

    if q.clahe_clip > 0:
        grid = max(2, int(q.clahe_grid))
        clahe = cv2.createCLAHE(clipLimit=float(q.clahe_clip), tileGridSize=(grid, grid))
        out = clahe.apply(out)

    if abs(q.gamma - 1.0) > 1e-3:
        g = max(0.1, float(q.gamma))
        lut = np.array([((i / 255.0) ** (1.0 / g)) * 255.0 for i in range(256)], dtype=np.uint8)
        out = cv2.LUT(out, lut)

    if q.sharpen > 1e-3:
        amt = float(q.sharpen)
        blur = cv2.GaussianBlur(out, (0, 0), sigmaX=1.0, sigmaY=1.0)
        out = cv2.addWeighted(out, 1.0 + amt, blur, -amt, 0)
    return out


def _ordered_dither(norm, strength: float):
    _, np = require_cv2()
    if strength <= 1e-4:
        return norm
    bayer4 = np.array(
        [
            [0, 8, 2, 10],
            [12, 4, 14, 6],
            [3, 11, 1, 9],
            [15, 7, 13, 5],
        ],
        dtype=np.float32,
    ) / 16.0
    h, w = norm.shape[:2]
    tile = np.tile(bayer4, (h // 4 + 1, w // 4 + 1))[:h, :w]
    jitter = (tile - 0.5) * float(strength)
    return np.clip(norm + jitter, 0.0, 1.0)


def gray_to_ascii_indices(
    gray,
    width: int,
    row_scale: float,
    ramp: str,
    quality: QualityOptions,
    prev_idx_float=None,
):
    cv2, np = require_cv2()
    h, w = gray.shape[:2]
    if w <= 0 or h <= 0:
        return np.zeros((4, 8), dtype=np.int32), np.zeros((4, 8), dtype=np.float32)

    processed = _preprocess_gray(gray, quality)
    out_w, out_h = _resize_for_ascii(processed, width=width, row_scale=row_scale)
    small = cv2.resize(processed, (out_w, out_h), interpolation=cv2.INTER_AREA)
    small_f = small.astype(np.float32)

    if quality.edge_weight > 1e-4:
        gx = cv2.Sobel(small, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(small, cv2.CV_32F, 0, 1, ksize=3)
        edge = cv2.magnitude(gx, gy)
        edge = cv2.normalize(edge, None, 0.0, 255.0, cv2.NORM_MINMAX)
        w_edge = float(min(1.0, max(0.0, quality.edge_weight)))
        small_f = (small_f * (1.0 - w_edge)) + (edge * w_edge)

    norm = np.clip(small_f / 255.0, 0.0, 1.0)
    norm = _ordered_dither(norm, strength=max(0.0, float(quality.dither)))

    n = len(ramp) - 1
    idx_float = norm * n
    alpha = float(min(0.98, max(0.0, quality.temporal_alpha)))
    if prev_idx_float is not None and prev_idx_float.shape == idx_float.shape and alpha > 0.0:
        idx_float = (prev_idx_float * alpha) + (idx_float * (1.0 - alpha))

    idx = np.clip(np.rint(idx_float), 0, n).astype(np.int32)
    return idx, idx_float


def indices_to_ascii_lines(idx, ramp: str) -> List[str]:
    lines: List[str] = []
    for row in idx:
        lines.append("".join(ramp[int(v)] for v in row))
    return lines


def grayscale_to_ascii_lines(gray, width: int, row_scale: float, ramp: str, quality: QualityOptions) -> List[str]:
    idx, _ = gray_to_ascii_indices(
        gray,
        width=width,
        row_scale=row_scale,
        ramp=ramp,
        quality=quality,
        prev_idx_float=None,
    )
    lines: List[str] = []
    for row in idx:
        lines.append("".join(ramp[int(v)] for v in row))
    return lines


def color_grid_from_bgr(frame_bgr, out_w: int, out_h: int, sat_boost: float, val_boost: float):
    cv2, np = require_cv2()
    small = cv2.resize(frame_bgr, (out_w, out_h), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * max(0.2, float(sat_boost)), 0, 255)
    hsv[:, :, 2] = np.clip(hsv[:, :, 2] * max(0.2, float(val_boost)), 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def ansi_color_ascii(lines: List[str], color_grid_bgr) -> str:
    rows = len(lines)
    cols = max((len(line) for line in lines), default=0)
    if rows == 0 or cols == 0:
        return ""
    out_lines: List[str] = []
    for r, line in enumerate(lines):
        parts = ["\x1b[48;2;0;0;0m"]
        for c, ch in enumerate(line):
            if ch == " ":
                parts.append(" ")
                continue
            if r < color_grid_bgr.shape[0] and c < color_grid_bgr.shape[1]:
                b, g, red = [int(v) for v in color_grid_bgr[r, c]]
            else:
                red, g, b = 240, 240, 240
            parts.append(f"\x1b[38;2;{red};{g};{b}m{ch}")
        parts.append("\x1b[0m")
        out_lines.append("".join(parts))
    return "\n".join(out_lines)


def _render_cli_frame(lines: List[str], color_grid_bgr, use_color: bool) -> str:
    if use_color and color_grid_bgr is not None:
        return ansi_color_ascii(lines, color_grid_bgr)
    return "\n".join(lines)


def render_ascii_lines(
    lines: List[str],
    font_scale: float = 0.6,
    line_spacing: int = 2,
    margin: int = 12,
    fg_gray: int = 240,
    bg_gray: int = 8,
    bg_color: Tuple[int, int, int] | None = None,
    char_colors=None,
):
    cv2, np = require_cv2()
    if not lines:
        lines = [" "]

    font = cv2.FONT_HERSHEY_PLAIN
    thickness = 1
    sample = "M"
    (char_w, char_h), baseline = cv2.getTextSize(sample, font, font_scale, thickness)
    char_w = max(1, char_w)
    line_h = max(1, char_h + baseline + line_spacing)
    max_cols = max(1, max(len(line) for line in lines))

    out_w = (margin * 2) + (max_cols * char_w)
    out_h = (margin * 2) + (len(lines) * line_h)
    if bg_color is None:
        bg = (int(bg_gray), int(bg_gray), int(bg_gray))
    else:
        bg = (int(bg_color[0]), int(bg_color[1]), int(bg_color[2]))
    canvas = np.full((out_h, out_w, 3), bg, dtype=np.uint8)

    color_grid = None
    if char_colors is not None:
        tmp = np.asarray(char_colors, dtype=np.uint8)
        if tmp.ndim == 3 and tmp.shape[2] == 3:
            color_grid = tmp

    if color_grid is None:
        y = margin + char_h
        fg = (int(fg_gray), int(fg_gray), int(fg_gray))
        for line in lines:
            cv2.putText(canvas, line, (margin, y), font, font_scale, fg, thickness, cv2.LINE_AA)
            y += line_h
        return canvas

    mask = np.zeros((out_h, out_w), dtype=np.uint8)
    color_canvas = np.zeros((out_h, out_w, 3), dtype=np.uint8)

    y = margin + char_h
    fallback = np.array([int(fg_gray), int(fg_gray), int(fg_gray)], dtype=np.uint8)
    for r, line in enumerate(lines):
        cv2.putText(mask, line, (margin, y), font, font_scale, 255, thickness, cv2.LINE_AA)
        top = max(0, y - char_h)
        bottom = min(out_h, top + line_h)
        if top >= bottom:
            y += line_h
            continue
        for c, ch in enumerate(line):
            if ch == " ":
                continue
            x0 = margin + (c * char_w)
            x1 = min(out_w, x0 + char_w)
            if x0 >= out_w or x0 >= x1:
                continue
            if r < color_grid.shape[0] and c < color_grid.shape[1]:
                col = color_grid[r, c]
            else:
                col = fallback
            color_canvas[top:bottom, x0:x1] = col
        y += line_h

    alpha = (mask.astype(np.float32) / 255.0)[:, :, None]
    canvas = (canvas.astype(np.float32) * (1.0 - alpha) + color_canvas.astype(np.float32) * alpha).astype(np.uint8)
    return canvas


def convert_image_to_ascii(
    input_path: str,
    width: int,
    row_scale: float,
    ramp: str,
    quality: QualityOptions,
) -> List[str]:
    cv2, _ = require_cv2()
    img = cv2.imread(input_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"Failed to read image: {input_path}")
    return grayscale_to_ascii_lines(img, width=width, row_scale=row_scale, ramp=ramp, quality=quality)


def run_image_mode(args: argparse.Namespace) -> int:
    cv2, _ = require_cv2()
    ramp = normalize_ramp(args.ramp, args.invert)
    quality = quality_from_args(args, temporal_alpha=0.0)
    in_ext = Path(args.input).suffix.lower()
    if in_ext in VIDEO_EXTS:
        raise ValueError(f"Input appears to be a video ({in_ext}). Use `video` mode instead of `image`.")
    src_color = cv2.imread(args.input, cv2.IMREAD_COLOR)
    if src_color is None:
        raise FileNotFoundError(f"Failed to read image: {args.input}")
    gray = cv2.cvtColor(src_color, cv2.COLOR_BGR2GRAY)
    idx, _ = gray_to_ascii_indices(
        gray,
        width=args.width,
        row_scale=args.row_scale,
        ramp=ramp,
        quality=quality,
    )
    lines = indices_to_ascii_lines(idx, ramp=ramp)
    art = "\n".join(lines)

    out_txt = Path(args.output) if args.output else Path(args.input).with_suffix(".ascii.txt")
    out_txt.write_text(art, encoding="utf-8")
    print(f"[OK] ASCII text saved: {out_txt}")

    if args.output_image:
        color_grid = None
        if not args.no_colorize:
            color_grid = color_grid_from_bgr(
                src_color,
                out_w=idx.shape[1],
                out_h=idx.shape[0],
                sat_boost=args.color_sat,
                val_boost=args.color_val,
            )
        canvas = render_ascii_lines(
            lines,
            font_scale=args.font_scale,
            line_spacing=args.line_spacing,
            margin=args.margin,
            fg_gray=args.fg_gray,
            bg_gray=args.bg_gray,
            bg_color=(0, 0, 0),
            char_colors=color_grid,
        )
        if not cv2.imwrite(args.output_image, canvas):
            raise RuntimeError(f"Failed to write ASCII image: {args.output_image}")
        print(f"[OK] ASCII image saved: {args.output_image}")

    if args.print:
        print()
        if args.ansi_color_print and not args.no_colorize:
            color_grid = color_grid_from_bgr(
                src_color,
                out_w=idx.shape[1],
                out_h=idx.shape[0],
                sat_boost=args.color_sat,
                val_boost=args.color_val,
            )
            print(ansi_color_ascii(lines, color_grid))
        else:
            print(art)
    return 0


def run_video_mode(args: argparse.Namespace) -> int:
    cv2, _ = require_cv2()
    ramp = normalize_ramp(args.ramp, args.invert)
    quality = quality_from_args(args, temporal_alpha=args.temporal_alpha)

    output_path = (args.output or "").strip()
    if output_path:
        output_ext = Path(output_path).suffix.lower()
        if output_ext not in VIDEO_EXTS:
            raise ValueError(
                f"Video output must be a video file extension {sorted(VIDEO_EXTS)} (got: {args.output})."
            )
    elif args.no_preview and not args.text_frames_dir:
        raise ValueError("Nothing to do: provide --output or --text-frames-dir, or keep terminal preview enabled.")

    cap = cv2.VideoCapture(args.input)
    if not cap.isOpened():
        raise FileNotFoundError(f"Failed to open video: {args.input}")

    src_fps = cap.get(cv2.CAP_PROP_FPS)
    if src_fps <= 0:
        src_fps = 24.0
    fps = float(args.fps) if args.fps and args.fps > 0 else float(src_fps)

    text_dir = None
    if args.text_frames_dir:
        text_dir = Path(args.text_frames_dir)
        text_dir.mkdir(parents=True, exist_ok=True)

    writer = None
    frame_idx = 0
    prev_idx_float = None
    codec = (args.codec or "mp4v").strip()
    if len(codec) != 4:
        raise ValueError("--codec must be 4 characters (example: mp4v, XVID).")
    preview_enabled = not args.no_preview
    preview_every = max(1, int(args.preview_every))
    preview_inplace = bool(preview_enabled and sys.stdout.isatty())
    preview_ansi_color = bool(preview_inplace and not args.no_colorize)
    preview_target_fps = float(args.preview_fps) if args.preview_fps and args.preview_fps > 0 else 0.0
    if preview_enabled and not preview_inplace and preview_every < 4:
        # Piped outputs (GUI/log files) can't do in-place redraw; reduce volume by default.
        preview_every = 4
    next_preview_time = 0.0
    first_preview = True

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if args.max_frames > 0 and frame_idx >= args.max_frames:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            idx, prev_idx_float = gray_to_ascii_indices(
                gray,
                width=args.width,
                row_scale=args.row_scale,
                ramp=ramp,
                quality=quality,
                prev_idx_float=prev_idx_float,
            )
            lines = indices_to_ascii_lines(idx, ramp=ramp)
            color_grid = None
            if not args.no_colorize:
                color_grid = color_grid_from_bgr(
                    frame,
                    out_w=idx.shape[1],
                    out_h=idx.shape[0],
                    sat_boost=args.color_sat,
                    val_boost=args.color_val,
                )

            if text_dir is not None:
                (text_dir / f"frame_{frame_idx:06d}.txt").write_text("\n".join(lines), encoding="utf-8")

            if preview_enabled and (frame_idx % preview_every == 0):
                now = time.perf_counter()
                if preview_target_fps <= 0.0 or now >= next_preview_time:
                    frame_txt = _render_cli_frame(lines, color_grid, use_color=preview_ansi_color)
                    if preview_inplace:
                        if first_preview:
                            sys.stdout.write("\x1b[2J\x1b[H")
                            first_preview = False
                        else:
                            sys.stdout.write("\x1b[H")
                        sys.stdout.write(frame_txt)
                        sys.stdout.write("\x1b[0m\n")
                    else:
                        # Non-tty fallback (for redirected output): print frame blocks sequentially.
                        sys.stdout.write(f"\n[FRAME {frame_idx + 1}]\n")
                        sys.stdout.write("\n".join(lines))
                        sys.stdout.write("\n")
                    sys.stdout.flush()
                    if preview_target_fps > 0.0:
                        next_preview_time = now + (1.0 / preview_target_fps)

            ascii_img = render_ascii_lines(
                lines,
                font_scale=args.font_scale,
                line_spacing=args.line_spacing,
                margin=args.margin,
                fg_gray=args.fg_gray,
                bg_gray=args.bg_gray,
                bg_color=(0, 0, 0),
                char_colors=color_grid,
            )

            if output_path and writer is None:
                fourcc = cv2.VideoWriter_fourcc(*codec)
                h, w = ascii_img.shape[:2]
                writer = cv2.VideoWriter(output_path, fourcc, fps, (w, h))
                if not writer.isOpened():
                    raise RuntimeError(
                        f"Failed to initialize writer for {output_path}. Try another --codec (for example XVID)."
                    )

            if writer is not None:
                writer.write(ascii_img)
            frame_idx += 1
            if args.progress_every > 0 and frame_idx % args.progress_every == 0 and not preview_inplace:
                print(f"[PROGRESS] {frame_idx} frames converted...")
    finally:
        cap.release()
        if writer is not None:
            writer.release()

    if frame_idx == 0:
        raise RuntimeError("No frames were converted (input may be empty or unreadable).")
    if output_path:
        print(f"[OK] ASCII video saved: {output_path} ({frame_idx} frames @ {fps:.2f} fps)")
    else:
        print(f"[OK] ASCII preview completed in terminal ({frame_idx} frames @ {fps:.2f} fps source).")
    if text_dir is not None:
        print(f"[OK] ASCII text frames saved: {text_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local image/video to ASCII converters (no APIs/LLMs).")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_img = sub.add_parser("image", help="Convert image to ASCII text (optionally PNG).")
    p_img.add_argument("--input", required=True, help="Input image path.")
    p_img.add_argument("--output", default=None, help="Output ASCII text path (default: <input>.ascii.txt).")
    p_img.add_argument("--output-image", default=None, help="Optional rendered ASCII PNG/JPG path.")
    p_img.add_argument("--width", type=int, default=120, help="ASCII columns.")
    p_img.add_argument("--row-scale", type=float, default=0.55, help="Row height correction factor.")
    p_img.add_argument("--ramp", type=str, default=DEFAULT_RAMP, help="Light->dark ramp chars.")
    p_img.add_argument("--invert", action="store_true", help="Invert luminance mapping.")
    p_img.add_argument("--clahe-clip", type=float, default=1.4, help="CLAHE clip limit (0 disables CLAHE).")
    p_img.add_argument("--clahe-grid", type=int, default=8, help="CLAHE tile grid size.")
    p_img.add_argument("--gamma", type=float, default=1.0, help="Gamma correction (>1 brightens dark regions).")
    p_img.add_argument("--sharpen", type=float, default=0.12, help="Unsharp mask amount.")
    p_img.add_argument("--edge-weight", type=float, default=0.08, help="Blend weight for gradient edges (0..1).")
    p_img.add_argument("--dither", type=float, default=0.03, help="Ordered dithering strength (0..1).")
    p_img.add_argument("--no-colorize", action="store_true", help="Disable colorized ASCII and use grayscale text.")
    p_img.add_argument("--color-sat", type=float, default=1.25, help="Color saturation boost when colorize is enabled.")
    p_img.add_argument("--color-val", type=float, default=1.05, help="Color brightness boost when colorize is enabled.")
    p_img.add_argument("--print", action="store_true", help="Print ASCII art to terminal.")
    p_img.add_argument("--ansi-color-print", action="store_true", help="Use ANSI truecolor for --print output.")
    p_img.add_argument("--font-scale", type=float, default=0.6, help="Font scale for rendered ASCII image.")
    p_img.add_argument("--line-spacing", type=int, default=2, help="Extra pixels between ASCII rows.")
    p_img.add_argument("--margin", type=int, default=12, help="Image margin in pixels.")
    p_img.add_argument("--fg-gray", type=int, default=240, help="Text grayscale value 0..255.")
    p_img.add_argument("--bg-gray", type=int, default=0, help="Background grayscale value 0..255.")

    p_vid = sub.add_parser("video", help="Convert video to rendered ASCII video.")
    p_vid.add_argument("--input", required=True, help="Input video path.")
    p_vid.add_argument("--output", default=None, help="Optional output video path (example: out_ascii.mp4).")
    p_vid.add_argument("--width", type=int, default=120, help="ASCII columns.")
    p_vid.add_argument("--row-scale", type=float, default=0.55, help="Row height correction factor.")
    p_vid.add_argument("--ramp", type=str, default=DEFAULT_RAMP, help="Light->dark ramp chars.")
    p_vid.add_argument("--invert", action="store_true", help="Invert luminance mapping.")
    p_vid.add_argument("--clahe-clip", type=float, default=1.4, help="CLAHE clip limit (0 disables CLAHE).")
    p_vid.add_argument("--clahe-grid", type=int, default=8, help="CLAHE tile grid size.")
    p_vid.add_argument("--gamma", type=float, default=1.0, help="Gamma correction (>1 brightens dark regions).")
    p_vid.add_argument("--sharpen", type=float, default=0.12, help="Unsharp mask amount.")
    p_vid.add_argument("--edge-weight", type=float, default=0.08, help="Blend weight for gradient edges (0..1).")
    p_vid.add_argument("--dither", type=float, default=0.03, help="Ordered dithering strength (0..1).")
    p_vid.add_argument("--no-colorize", action="store_true", help="Disable colorized ASCII and use grayscale text.")
    p_vid.add_argument("--color-sat", type=float, default=1.25, help="Color saturation boost when colorize is enabled.")
    p_vid.add_argument("--color-val", type=float, default=1.05, help="Color brightness boost when colorize is enabled.")
    p_vid.add_argument("--temporal-alpha", type=float, default=0.82, help="Temporal smoothing alpha for video (0..0.98).")
    p_vid.add_argument("--fps", type=float, default=0.0, help="Output fps (<=0 uses source fps).")
    p_vid.add_argument("--codec", type=str, default="mp4v", help="4-char codec (mp4v, XVID, MJPG...).")
    p_vid.add_argument("--max-frames", type=int, default=0, help="0 for all frames, else cap conversion length.")
    p_vid.add_argument("--text-frames-dir", default=None, help="Optional folder to also dump .txt per frame.")
    p_vid.add_argument("--progress-every", type=int, default=30, help="Progress print interval in frames.")
    p_vid.add_argument("--no-preview", action="store_true", help="Disable live ASCII playback in terminal.")
    p_vid.add_argument("--preview-every", type=int, default=1, help="Show every Nth frame in terminal preview.")
    p_vid.add_argument("--preview-fps", type=float, default=0.0, help="Limit terminal preview rate (<=0 = no limit).")
    p_vid.add_argument("--font-scale", type=float, default=0.6, help="Font scale for rendered ASCII video.")
    p_vid.add_argument("--line-spacing", type=int, default=2, help="Extra pixels between ASCII rows.")
    p_vid.add_argument("--margin", type=int, default=12, help="Frame margin in pixels.")
    p_vid.add_argument("--fg-gray", type=int, default=240, help="Text grayscale value 0..255.")
    p_vid.add_argument("--bg-gray", type=int, default=0, help="Background grayscale value 0..255.")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.mode == "image":
            return run_image_mode(args)
        if args.mode == "video":
            return run_video_mode(args)
        parser.error(f"Unknown mode: {args.mode}")
        return 2
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
