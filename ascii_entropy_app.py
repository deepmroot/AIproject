import argparse
import base64
import os
from pathlib import Path
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


APP_DIR = Path(__file__).resolve().parent
CONVERTER_SCRIPT = APP_DIR / "ascii_converter.py"
ENTROPY_SCRIPT = APP_DIR / "cosmic_entropy.py"
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".m4v", ".wmv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".gif"}


try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore

    TK_BASE = TkinterDnD.Tk
    DND_AVAILABLE = True
except Exception:
    TK_BASE = tk.Tk
    DND_AVAILABLE = False


def quote_cmd(args):
    out = []
    for arg in args:
        s = str(arg)
        if any(ch in s for ch in (" ", "\t", '"')):
            s = '"' + s.replace('"', '\\"') + '"'
        out.append(s)
    return " ".join(out)


def split_extra_args(text):
    txt = (text or "").strip()
    if not txt:
        return []
    try:
        return shlex.split(txt, posix=False)
    except Exception as exc:
        raise ValueError(f"Invalid extra args: {exc}") from exc


class App(TK_BASE):
    def __init__(self):
        super().__init__()
        self.title("ASCII Lab - Converter + Entropy GA")
        self.geometry("1240x860")
        self.minsize(1080, 760)

        self.proc = None
        self.reader_thread = None
        self.log_queue = queue.Queue()
        self._ansi_fg = "#DDDDDD"
        self._ansi_tag_colors = {"#DDDDDD"}
        self._ansi_tag_limit = 192
        self._is_fullscreen = False
        self._result_paths = []
        self._result_meta = {}
        self._preview_photo = None

        self._build_ui()
        self.after(60, self._poll_logs)

    def _build_ui(self):
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(root)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.fullscreen_text = tk.StringVar(value="Fullscreen (F11)")
        ttk.Button(toolbar, textvariable=self.fullscreen_text, command=self._toggle_fullscreen).pack(side="left")
        ttk.Button(toolbar, text="Clear Log", command=self._clear_log).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Stop Process", command=self._stop_process).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Open Project Folder", command=lambda: self._open_path(APP_DIR)).pack(
            side="left", padx=(6, 0)
        )
        status = "Drag-drop: enabled" if DND_AVAILABLE else "Drag-drop: disabled (install tkinterdnd2)"
        ttk.Label(toolbar, text=status).pack(side="right")

        self.main_split = ttk.Panedwindow(root, orient="vertical")
        self.main_split.grid(row=1, column=0, sticky="nsew")

        top = ttk.Frame(self.main_split)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(top)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        self.converter_tab = ttk.Frame(self.notebook, padding=8)
        self.entropy_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.converter_tab, text="Converter")
        self.notebook.add(self.entropy_tab, text="Entropy (GA)")

        self._build_converter_tab()
        self._build_entropy_tab()

        bottom = ttk.LabelFrame(self.main_split, text="Output Console + Results", padding=8)
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(0, weight=1)

        self.output_notebook = ttk.Notebook(bottom)
        self.output_notebook.grid(row=0, column=0, sticky="nsew")
        cli_tab = ttk.Frame(self.output_notebook, padding=4)
        self.results_tab = ttk.Frame(self.output_notebook, padding=4)
        self.output_notebook.add(cli_tab, text="CLI")
        self.output_notebook.add(self.results_tab, text="Results")

        self._build_cli_panel(cli_tab)
        self._build_results_panel(self.results_tab)
        self._clear_results()

        self.main_split.add(top, weight=3)
        self.main_split.add(bottom, weight=5)
        self.after(120, self._set_default_split)
        self.bind("<F11>", self._toggle_fullscreen_event)
        self.bind("<Escape>", self._exit_fullscreen_event)

    def _build_converter_tab(self):
        f = self.converter_tab
        for c in range(4):
            f.columnconfigure(c, weight=1 if c == 1 else 0)

        self.conv_mode = tk.StringVar(value="image")
        self.conv_input = tk.StringVar()
        self.conv_output = tk.StringVar(value=str(APP_DIR / "test_outputs" / "out_ascii.txt"))
        self.conv_output_image = tk.StringVar(value=str(APP_DIR / "test_outputs" / "out_ascii.png"))
        self.conv_text_frames = tk.StringVar(value=str(APP_DIR / "test_outputs" / "ascii_frames"))
        self.conv_width = tk.IntVar(value=120)
        self.conv_row_scale = tk.DoubleVar(value=0.55)
        self.conv_ramp = tk.StringVar(value=" .:-=+*#%@")
        self.conv_invert = tk.BooleanVar(value=False)
        self.conv_clahe_clip = tk.DoubleVar(value=1.4)
        self.conv_clahe_grid = tk.IntVar(value=8)
        self.conv_gamma = tk.DoubleVar(value=1.0)
        self.conv_sharpen = tk.DoubleVar(value=0.12)
        self.conv_edge_weight = tk.DoubleVar(value=0.08)
        self.conv_dither = tk.DoubleVar(value=0.03)
        self.conv_colorize = tk.BooleanVar(value=True)
        self.conv_color_sat = tk.DoubleVar(value=1.25)
        self.conv_color_val = tk.DoubleVar(value=1.05)
        self.conv_font_scale = tk.DoubleVar(value=0.6)
        self.conv_line_spacing = tk.IntVar(value=2)
        self.conv_margin = tk.IntVar(value=12)
        self.conv_fg_gray = tk.IntVar(value=240)
        self.conv_bg_gray = tk.IntVar(value=0)
        self.conv_ansi_color_print = tk.BooleanVar(value=False)
        self.conv_print = tk.BooleanVar(value=False)
        self.conv_fps = tk.DoubleVar(value=0.0)
        self.conv_codec = tk.StringVar(value="mp4v")
        self.conv_max_frames = tk.IntVar(value=0)
        self.conv_progress_every = tk.IntVar(value=30)
        self.conv_temporal_alpha = tk.DoubleVar(value=0.82)
        self.conv_live_preview = tk.BooleanVar(value=False)
        self.conv_preview_every = tk.IntVar(value=4)
        self.conv_preview_fps = tk.DoubleVar(value=12.0)
        self.conv_extra = tk.StringVar(value="")

        row = 0
        ttk.Label(f, text="Mode").grid(row=row, column=0, sticky="w", pady=4)
        mode_box = ttk.Combobox(f, textvariable=self.conv_mode, values=("image", "video"), state="readonly", width=12)
        mode_box.grid(row=row, column=1, sticky="w", pady=4)
        mode_box.bind("<<ComboboxSelected>>", lambda _: self._on_conv_mode_change())

        conv_actions = ttk.Frame(f)
        conv_actions.grid(row=row, column=2, columnspan=2, sticky="e")
        ttk.Button(conv_actions, text="Run Converter", command=self.run_converter).pack(side="left")
        ttk.Button(conv_actions, text="Copy CLI", command=self.copy_converter_cmd).pack(side="left", padx=(6, 0))
        ttk.Button(conv_actions, text="Open test_outputs", command=lambda: self._open_path(APP_DIR / "test_outputs")).pack(
            side="left", padx=(6, 0)
        )

        row += 1
        self.conv_input_entry = self._path_row(
            parent=f,
            row=row,
            label="Input File",
            var=self.conv_input,
            browse_cmd=self._browse_conv_input,
            browse_text="Browse...",
        )
        self._bind_drop(self.conv_input_entry, self.conv_input)

        row += 1
        self.conv_output_entry = self._path_row(
            parent=f,
            row=row,
            label="Output File",
            var=self.conv_output,
            browse_cmd=self._browse_conv_output,
            browse_text="Save As...",
        )

        row += 1
        self.conv_output_image_entry = self._path_row(
            parent=f,
            row=row,
            label="Output Image (image mode)",
            var=self.conv_output_image,
            browse_cmd=self._browse_conv_output_image,
            browse_text="Save As...",
        )

        row += 1
        self.conv_text_frames_entry = self._path_row(
            parent=f,
            row=row,
            label="Text Frames Dir (video mode)",
            var=self.conv_text_frames,
            browse_cmd=lambda: self._pick_dir(self.conv_text_frames),
            browse_text="Choose Dir...",
        )

        row += 1
        self._num_row(f, row, "Width", self.conv_width, "Row Scale", self.conv_row_scale)
        row += 1
        self._num_row(f, row, "CLAHE Clip", self.conv_clahe_clip, "CLAHE Grid", self.conv_clahe_grid)
        row += 1
        self._num_row(f, row, "Gamma", self.conv_gamma, "Sharpen", self.conv_sharpen)
        row += 1
        self._num_row(f, row, "Edge Weight", self.conv_edge_weight, "Dither", self.conv_dither)
        row += 1
        self._num_row(f, row, "Color Sat", self.conv_color_sat, "Color Val", self.conv_color_val)
        row += 1
        self._num_row(f, row, "Font Scale", self.conv_font_scale, "Line Spacing", self.conv_line_spacing)
        row += 1
        self._num_row(f, row, "Margin", self.conv_margin, "FG Gray", self.conv_fg_gray)
        row += 1
        self._num_row(f, row, "BG Gray", self.conv_bg_gray, "FPS (video)", self.conv_fps)
        row += 1
        self._num_row(f, row, "Codec (video)", self.conv_codec, "Temporal Alpha (video)", self.conv_temporal_alpha)
        row += 1
        self._num_row(f, row, "Max Frames (video)", self.conv_max_frames, "Progress Every (video)", self.conv_progress_every)
        row += 1
        self._num_row(f, row, "Preview Every (video)", self.conv_preview_every, "Preview FPS (video)", self.conv_preview_fps)
        row += 1
        ttk.Label(f, text="Ramp").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.conv_ramp).grid(row=row, column=1, columnspan=3, sticky="ew", pady=4)

        row += 1
        opts = ttk.Frame(f)
        opts.grid(row=row, column=0, columnspan=4, sticky="ew", pady=4)
        ttk.Checkbutton(opts, text="Invert", variable=self.conv_invert).pack(side="left")
        ttk.Checkbutton(opts, text="Colorize ASCII", variable=self.conv_colorize).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(opts, text="Print to terminal (image mode)", variable=self.conv_print).pack(
            side="left", padx=(12, 0)
        )
        ttk.Checkbutton(opts, text="ANSI Color Print (image mode)", variable=self.conv_ansi_color_print).pack(
            side="left", padx=(12, 0)
        )
        ttk.Checkbutton(opts, text="Live CLI Preview (video)", variable=self.conv_live_preview).pack(
            side="left", padx=(12, 0)
        )

        row += 1
        ttk.Label(f, text="Extra Args (optional)").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.conv_extra).grid(row=row, column=1, columnspan=3, sticky="ew", pady=4)

        self._on_conv_mode_change()

    def _build_entropy_tab(self):
        f = self.entropy_tab
        for c in range(4):
            f.columnconfigure(c, weight=1 if c == 1 else 0)

        self.ent_text = tk.StringVar(value="show a dying star over a ruined city")
        self.ent_frames = tk.IntVar(value=8)
        self.ent_gens = tk.IntVar(value=12)
        self.ent_population = tk.IntVar(value=80)
        self.ent_width = tk.IntVar(value=120)
        self.ent_height = tk.IntVar(value=45)
        self.ent_ascii_mode = tk.StringVar(value="short")
        self.ent_contrast = tk.IntVar(value=0)
        self.ent_workers = tk.IntVar(value=1)
        self.ent_save_dir = tk.StringVar(value=str(APP_DIR / "outputs"))
        self.ent_frame_delay = tk.DoubleVar(value=0.12)
        self.ent_frame_kick = tk.DoubleVar(value=0.18)
        self.ent_seed = tk.StringVar(value="")
        self.ent_subject_mode = tk.StringVar(value="procedural")
        self.ent_no_early_stop = tk.BooleanVar(value=False)
        self.ent_no_animate = tk.BooleanVar(value=True)
        self.ent_quiet = tk.BooleanVar(value=False)
        self.ent_timing = tk.BooleanVar(value=True)
        self.ent_random_seed = tk.BooleanVar(value=False)
        self.ent_extra = tk.StringVar(value="")

        row = 0
        ent_actions = ttk.Frame(f)
        ent_actions.grid(row=row, column=2, columnspan=2, sticky="e")
        ttk.Button(ent_actions, text="Run Entropy GA", command=self.run_entropy).pack(side="left")
        ttk.Button(ent_actions, text="Copy CLI", command=self.copy_entropy_cmd).pack(side="left", padx=(6, 0))
        ttk.Button(ent_actions, text="Open outputs", command=lambda: self._open_path(APP_DIR / "outputs")).pack(
            side="left", padx=(6, 0)
        )

        row += 1
        ttk.Label(f, text="Seed Text").grid(row=row, column=0, sticky="nw", pady=4)
        self.ent_text_widget = tk.Text(f, height=4, wrap="word")
        self.ent_text_widget.grid(row=row, column=1, columnspan=3, sticky="ew", pady=4)
        self.ent_text_widget.insert("1.0", self.ent_text.get())

        row += 1
        self._num_row(f, row, "Frames", self.ent_frames, "Generations", self.ent_gens)
        row += 1
        self._num_row(f, row, "Population", self.ent_population, "Workers", self.ent_workers)
        row += 1
        self._num_row(f, row, "Width", self.ent_width, "Height", self.ent_height)
        row += 1
        self._num_row(f, row, "Frame Delay", self.ent_frame_delay, "Frame Kick", self.ent_frame_kick)
        row += 1
        self._num_row(f, row, "ASCII Mode", self.ent_ascii_mode, "Contrast", self.ent_contrast)
        row += 1
        self._num_row(f, row, "Subject Mode", self.ent_subject_mode, "Seed (optional)", self.ent_seed)

        row += 1
        self._path_row(
            parent=f,
            row=row,
            label="Save Dir",
            var=self.ent_save_dir,
            browse_cmd=lambda: self._pick_dir(self.ent_save_dir),
            browse_text="Choose Dir...",
        )

        row += 1
        opts = ttk.Frame(f)
        opts.grid(row=row, column=0, columnspan=4, sticky="ew", pady=4)
        ttk.Checkbutton(opts, text="No Early Stop", variable=self.ent_no_early_stop).pack(side="left")
        ttk.Checkbutton(opts, text="No Animate", variable=self.ent_no_animate).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(opts, text="Quiet", variable=self.ent_quiet).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(opts, text="Timing", variable=self.ent_timing).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(opts, text="Random Seed", variable=self.ent_random_seed).pack(side="left", padx=(12, 0))

        row += 1
        ttk.Label(f, text="Extra Args (optional)").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.ent_extra).grid(row=row, column=1, columnspan=3, sticky="ew", pady=4)

    def _build_cli_panel(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self.log_text = ScrolledText(parent, wrap="word", font=("Consolas", 11))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.configure(
            state="disabled",
            bg="#000000",
            fg="#DDDDDD",
            insertbackground="#DDDDDD",
            selectbackground="#2A2A2A",
            selectforeground="#FFFFFF",
        )
        self.log_text.tag_configure("fg_default", foreground="#DDDDDD")

    def _build_results_panel(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        split = ttk.Panedwindow(parent, orient="horizontal")
        split.grid(row=0, column=0, sticky="nsew")

        left = ttk.Frame(split, padding=(0, 0, 8, 0))
        right = ttk.Frame(split)
        split.add(left, weight=1)
        split.add(right, weight=3)

        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        ttk.Label(left, text="Generated Outputs").grid(row=0, column=0, sticky="w", pady=(0, 4))

        list_wrap = ttk.Frame(left)
        list_wrap.grid(row=1, column=0, sticky="nsew")
        list_wrap.columnconfigure(0, weight=1)
        list_wrap.rowconfigure(0, weight=1)
        self.result_listbox = tk.Listbox(list_wrap, exportselection=False)
        self.result_listbox.grid(row=0, column=0, sticky="nsew")
        list_scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=self.result_listbox.yview)
        list_scroll.grid(row=0, column=1, sticky="ns")
        self.result_listbox.configure(yscrollcommand=list_scroll.set)
        self.result_listbox.bind("<<ListboxSelect>>", self._on_result_select)

        left_actions = ttk.Frame(left)
        left_actions.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(left_actions, text="Open Selected", command=self._open_selected_result).pack(side="left")
        ttk.Button(left_actions, text="Open Folder", command=self._open_selected_result_folder).pack(side="left", padx=(6, 0))
        ttk.Button(left_actions, text="Clear List", command=self._clear_results).pack(side="left", padx=(6, 0))

        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        self.result_title_var = tk.StringVar(value="No output selected.")
        ttk.Label(right, textvariable=self.result_title_var).grid(row=0, column=0, sticky="w")

        self.result_summary = ScrolledText(right, wrap="word", height=8, font=("Consolas", 10))
        self.result_summary.grid(row=1, column=0, sticky="ew", pady=(6, 6))
        self.result_summary.configure(state="disabled")

        self.preview_label = tk.Label(
            right,
            text="Run a conversion and select an item to preview here.",
            bg="#000000",
            fg="#DDDDDD",
            anchor="center",
            justify="center",
        )
        self.preview_label.grid(row=2, column=0, sticky="nsew")

    def _set_default_split(self):
        try:
            total_h = max(1, self.main_split.winfo_height())
            self.main_split.sashpos(0, int(total_h * 0.42))
        except Exception:
            pass

    def _toggle_fullscreen_event(self, _event=None):
        self._toggle_fullscreen()
        return "break"

    def _exit_fullscreen_event(self, _event=None):
        if self._is_fullscreen:
            self._set_fullscreen(False)
            return "break"
        return None

    def _toggle_fullscreen(self):
        self._set_fullscreen(not self._is_fullscreen)

    def _set_fullscreen(self, enabled):
        self._is_fullscreen = bool(enabled)
        self.attributes("-fullscreen", self._is_fullscreen)
        self.fullscreen_text.set("Exit Fullscreen (Esc)" if self._is_fullscreen else "Fullscreen (F11)")

    def _capture_result_paths(self, text):
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if "ASCII image saved:" in line:
                p = line.split("ASCII image saved:", 1)[1].strip()
                self._register_result_path(p, {"kind": "ascii_image"})
            elif "ASCII text saved:" in line:
                p = line.split("ASCII text saved:", 1)[1].strip()
                self._register_result_path(p, {"kind": "ascii_text"})
            elif "ASCII video saved:" in line:
                tail = line.split("ASCII video saved:", 1)[1].strip()
                meta = {"kind": "ascii_video"}
                m = re.match(r"(.+?)\s+\((\d+)\s+frames\s+@\s+([0-9.]+)\s+fps\)", tail)
                if m:
                    path = m.group(1).strip()
                    meta["frames"] = int(m.group(2))
                    meta["fps"] = float(m.group(3))
                else:
                    path = tail
                self._register_result_path(path, meta)
            elif "ASCII text frames saved:" in line:
                p = line.split("ASCII text frames saved:", 1)[1].strip()
                self._register_result_path(p, {"kind": "ascii_text_frames"})
            elif "Frames saved:" in line:
                p = line.split("Frames saved:", 1)[1].strip()
                self._register_result_path(p, {"kind": "entropy_frames"})

    def _register_result_path(self, raw_path, meta=None):
        raw_path = str(raw_path).strip().strip("\"")
        if not raw_path:
            return

        p = Path(raw_path)
        if not p.is_absolute():
            p = (APP_DIR / p).resolve()
        path_str = str(p)
        prev_meta = self._result_meta.get(path_str, {})
        self._result_meta[path_str] = {**prev_meta, **(meta or {})}

        if path_str not in self._result_paths:
            self._result_paths.append(path_str)
            kind = self._result_meta[path_str].get("kind", "output").replace("_", " ")
            self.result_listbox.insert("end", f"{p.name} [{kind}]")

        idx = self._result_paths.index(path_str)
        self.result_listbox.selection_clear(0, "end")
        self.result_listbox.selection_set(idx)
        self.result_listbox.see(idx)
        self._update_result_view(path_str)

    def _on_result_select(self, _event=None):
        path = self._get_selected_result_path()
        if path:
            self._update_result_view(path)

    def _get_selected_result_path(self):
        sel = self.result_listbox.curselection()
        if not sel:
            return None
        idx = int(sel[0])
        if idx < 0 or idx >= len(self._result_paths):
            return None
        return self._result_paths[idx]

    def _clear_preview(self, text):
        self.preview_label.configure(image="", text=text)
        self.preview_label.image = None
        self._preview_photo = None

    def _set_summary_text(self, text):
        self.result_summary.configure(state="normal")
        self.result_summary.delete("1.0", "end")
        self.result_summary.insert("1.0", text.strip() + "\n")
        self.result_summary.configure(state="disabled")

    def _format_bytes(self, n):
        v = float(max(0, n))
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if v < 1024.0 or unit == "TB":
                return f"{v:.1f} {unit}"
            v /= 1024.0
        return f"{v:.1f} TB"

    def _update_result_view(self, path_str):
        p = Path(path_str)
        meta = self._result_meta.get(path_str, {})
        kind = meta.get("kind", "output")
        self.result_title_var.set(f"{p.name} ({kind.replace('_', ' ')})")

        if not p.exists():
            self._clear_preview("Selected path does not exist.")
            self._set_summary_text(f"Path:\n{p}\n\nStatus: Missing.")
            return

        if p.is_dir():
            entries = []
            try:
                entries = sorted(x.name for x in p.iterdir())
            except Exception:
                pass
            preview = "\n".join(entries[:30])
            more = "" if len(entries) <= 30 else f"\n... and {len(entries) - 30} more."
            self._clear_preview("Directory selected. Use Open Folder to inspect all files.")
            self._set_summary_text(
                f"Path:\n{p}\n\nType: directory\nItems: {len(entries)}\n\nSample:\n{preview}{more}"
            )
            return

        ext = p.suffix.lower()
        base = f"Path:\n{p}\n\nSize: {self._format_bytes(p.stat().st_size)}\nType: {ext or 'file'}"

        if ext in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
            info = self._preview_image_file(p)
            self._set_summary_text(base + ("\n\n" + info if info else ""))
            return

        if ext in VIDEO_EXTS:
            info = self._preview_video_file(p, meta)
            self._set_summary_text(base + ("\n\n" + info if info else ""))
            return

        if ext in {".txt", ".log", ".md"}:
            sample = []
            line_count = 0
            try:
                with p.open("r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line_count += 1
                        if len(sample) < 40:
                            sample.append(line.rstrip("\n"))
            except Exception as exc:
                self._clear_preview("Could not preview text file.")
                self._set_summary_text(base + f"\n\nRead error: {exc}")
                return
            self._clear_preview("Text preview is shown in summary.")
            snippet = "\n".join(sample)
            more = "" if line_count <= 40 else f"\n... and {line_count - 40} more lines."
            self._set_summary_text(base + f"\n\nLines: {line_count}\n\nPreview:\n{snippet}{more}")
            return

        self._clear_preview("No preview available for this file type.")
        self._set_summary_text(base)

    def _preview_image_file(self, image_path):
        try:
            import cv2  # type: ignore
        except Exception:
            self._clear_preview("Install opencv-python to enable image previews.")
            return None

        img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if img is None:
            self._clear_preview("Failed to load image preview.")
            return None
        photo = self._photo_from_bgr(img)
        if photo is not None:
            self.preview_label.configure(image=photo, text="")
            self.preview_label.image = photo
            self._preview_photo = photo
        else:
            self._clear_preview("Failed to render image preview.")
        h, w = img.shape[:2]
        return f"Resolution: {w}x{h}"

    def _preview_video_file(self, video_path, meta):
        try:
            import cv2  # type: ignore
        except Exception:
            self._clear_preview("Install opencv-python to enable video previews.")
            return None

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            self._clear_preview("Failed to open video preview.")
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        ok, frame = cap.read()
        cap.release()

        if ok and frame is not None:
            photo = self._photo_from_bgr(frame)
            if photo is not None:
                self.preview_label.configure(image=photo, text="")
                self.preview_label.image = photo
                self._preview_photo = photo
            else:
                self._clear_preview("Failed to render video frame preview.")
        else:
            self._clear_preview("No readable frame for preview.")

        if "fps" in meta:
            fps = float(meta["fps"])
        if "frames" in meta:
            frames = int(meta["frames"])
        duration = (frames / fps) if fps > 0 else 0.0
        return f"Resolution: {w}x{h}\nFrames: {frames}\nFPS: {fps:.2f}\nDuration: {duration:.2f}s"

    def _photo_from_bgr(self, img_bgr, max_w=900, max_h=520):
        try:
            import cv2  # type: ignore
        except Exception:
            return None
        h, w = img_bgr.shape[:2]
        if h <= 0 or w <= 0:
            return None
        scale = min(max_w / float(w), max_h / float(h), 1.0)
        if scale < 1.0:
            img_bgr = cv2.resize(
                img_bgr,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        ok, enc = cv2.imencode(".png", img_bgr)
        if not ok:
            return None
        b64 = base64.b64encode(enc.tobytes()).decode("ascii")
        return tk.PhotoImage(data=b64)

    def _open_selected_result(self):
        path = self._get_selected_result_path()
        if not path:
            messagebox.showinfo("Open Selected", "No result selected.")
            return
        self._open_path(path)

    def _open_selected_result_folder(self):
        path = self._get_selected_result_path()
        if not path:
            messagebox.showinfo("Open Folder", "No result selected.")
            return
        p = Path(path)
        self._open_path(p.parent if p.is_file() else p)

    def _clear_results(self):
        self._result_paths.clear()
        self._result_meta.clear()
        self.result_listbox.delete(0, "end")
        self.result_title_var.set("No output selected.")
        self._clear_preview("Run a conversion and select an item to preview here.")
        self._set_summary_text("No output summary yet.")

    def _num_row(self, parent, row, left_label, left_var, right_label, right_var):
        ttk.Label(parent, text=left_label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=left_var).grid(row=row, column=1, sticky="ew", pady=2)
        ttk.Label(parent, text=right_label).grid(row=row, column=2, sticky="w", pady=2, padx=(12, 0))
        ttk.Entry(parent, textvariable=right_var).grid(row=row, column=3, sticky="ew", pady=2)

    def _path_row(self, parent, row, label, var, browse_cmd, browse_text):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=2)
        ttk.Button(parent, text=browse_text, command=browse_cmd).grid(row=row, column=3, sticky="e", pady=2)
        return entry

    def _bind_drop(self, widget, var):
        if not DND_AVAILABLE:
            return
        widget.drop_target_register(DND_FILES)

        def _on_drop(event):
            try:
                parts = self.tk.splitlist(event.data)
                if parts:
                    var.set(parts[0])
                else:
                    var.set(str(event.data).strip("{}"))
            except Exception:
                var.set(str(event.data).strip("{}"))
            if var is self.conv_input:
                self._sync_converter_mode_for_input()

        widget.dnd_bind("<<Drop>>", _on_drop)

    def _on_conv_mode_change(self):
        mode = self.conv_mode.get()
        image_mode = mode == "image"
        video_mode = mode == "video"
        self._set_state(self.conv_output_image_entry, "normal" if image_mode else "disabled")
        self._set_state(self.conv_text_frames_entry, "normal" if video_mode else "disabled")

        # Keep output extension aligned with converter mode to avoid writer failures.
        cur = self.conv_output.get().strip()
        if mode == "video":
            if not cur:
                self.conv_output.set(str(APP_DIR / "test_outputs" / "out_ascii.mp4"))
            else:
                p = Path(cur)
                if p.suffix.lower() in {"", ".txt"}:
                    self.conv_output.set(str((p.with_suffix("") if p.suffix else p).with_suffix(".mp4")))
        else:
            if not cur:
                self.conv_output.set(str(APP_DIR / "test_outputs" / "out_ascii.txt"))
            else:
                p = Path(cur)
                if p.suffix.lower() in VIDEO_EXTS or p.suffix.lower() == "":
                    self.conv_output.set(str((p.with_suffix("") if p.suffix else p).with_suffix(".txt")))

    def _set_state(self, widget, state):
        try:
            widget.configure(state=state)
        except Exception:
            pass

    def _browse_conv_input(self):
        path = filedialog.askopenfilename(title="Select Input File")
        if path:
            self.conv_input.set(path)
            self._sync_converter_mode_for_input()

    def _browse_conv_output(self):
        mode = self.conv_mode.get()
        if mode == "image":
            path = filedialog.asksaveasfilename(
                title="Save ASCII Text",
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            )
        else:
            path = filedialog.asksaveasfilename(
                title="Save ASCII Video",
                defaultextension=".mp4",
                filetypes=[("MP4 Video", "*.mp4"), ("AVI Video", "*.avi"), ("All files", "*.*")],
            )
        if path:
            self.conv_output.set(path)

    def _sync_converter_mode_for_input(self):
        try:
            p = Path(self.conv_input.get().strip())
            ext = p.suffix.lower()
            if ext in VIDEO_EXTS and self.conv_mode.get() != "video":
                self.conv_mode.set("video")
                self._on_conv_mode_change()
            elif ext in IMAGE_EXTS and self.conv_mode.get() != "image":
                self.conv_mode.set("image")
                self._on_conv_mode_change()
        except Exception:
            pass

    def _browse_conv_output_image(self):
        path = filedialog.asksaveasfilename(
            title="Save ASCII Image",
            defaultextension=".png",
            filetypes=[("PNG Image", "*.png"), ("JPG Image", "*.jpg;*.jpeg"), ("All files", "*.*")],
        )
        if path:
            self.conv_output_image.set(path)

    def _pick_dir(self, var):
        path = filedialog.askdirectory(title="Select Directory")
        if path:
            var.set(path)

    def run_converter(self):
        try:
            cmd = self._compose_converter_cmd()
            self._start_process(cmd, cwd=APP_DIR)
        except Exception as exc:
            messagebox.showerror("Run Converter", str(exc))

    def run_entropy(self):
        try:
            cmd = self._compose_entropy_cmd()
            self._start_process(cmd, cwd=APP_DIR)
        except Exception as exc:
            messagebox.showerror("Run Entropy", str(exc))

    def copy_converter_cmd(self):
        try:
            cmd = self._compose_converter_cmd()
            text = quote_cmd(cmd)
            self.clipboard_clear()
            self.clipboard_append(text)
            self._append_log(f"[SYSTEM] Converter CLI command copied:\n{text}\n")
        except Exception as exc:
            messagebox.showerror("Copy Converter Command", str(exc))

    def copy_entropy_cmd(self):
        try:
            cmd = self._compose_entropy_cmd()
            text = quote_cmd(cmd)
            self.clipboard_clear()
            self.clipboard_append(text)
            self._append_log(f"[SYSTEM] Entropy CLI command copied:\n{text}\n")
        except Exception as exc:
            messagebox.showerror("Copy Entropy Command", str(exc))

    def _compose_converter_cmd(self):
        mode = self.conv_mode.get()
        input_path = Path(self.conv_input.get().strip())
        if not input_path.exists():
            raise ValueError("Converter input file does not exist.")
        input_ext = input_path.suffix.lower()
        if mode == "image" and input_ext in VIDEO_EXTS:
            raise ValueError("Input file is a video. Switch mode to 'video' for this file.")
        if mode == "video" and input_ext in IMAGE_EXTS:
            raise ValueError("Input file is an image. Switch mode to 'image' for this file.")

        output_text = self.conv_output.get().strip()
        output_path = None
        if mode == "video":
            if output_text:
                output_path = Path(output_text)
                if output_path.suffix.lower() in {"", ".txt"}:
                    output_path = (output_path.with_suffix("") if output_path.suffix else output_path).with_suffix(".mp4")
                    self.conv_output.set(str(output_path))
                if output_path.suffix.lower() not in VIDEO_EXTS:
                    raise ValueError(
                        "Video mode output must be a video file (.mp4, .avi, .mov, .mkv, .m4v, .wmv)."
                    )
        elif mode == "image":
            if output_text:
                output_path = Path(output_text)
                if output_path.suffix.lower() == "":
                    output_path = output_path.with_suffix(".txt")
                    self.conv_output.set(str(output_path))

        cmd = [sys.executable, "-u", str(CONVERTER_SCRIPT), mode, "--input", str(input_path)]
        if output_path is not None:
            cmd += ["--output", str(output_path)]
        cmd += ["--width", str(self.conv_width.get())]
        cmd += ["--row-scale", str(self.conv_row_scale.get())]
        cmd += ["--ramp", self.conv_ramp.get()]
        cmd += ["--clahe-clip", str(self.conv_clahe_clip.get())]
        cmd += ["--clahe-grid", str(self.conv_clahe_grid.get())]
        cmd += ["--gamma", str(self.conv_gamma.get())]
        cmd += ["--sharpen", str(self.conv_sharpen.get())]
        cmd += ["--edge-weight", str(self.conv_edge_weight.get())]
        cmd += ["--dither", str(self.conv_dither.get())]
        cmd += ["--color-sat", str(self.conv_color_sat.get())]
        cmd += ["--color-val", str(self.conv_color_val.get())]
        cmd += ["--font-scale", str(self.conv_font_scale.get())]
        cmd += ["--line-spacing", str(self.conv_line_spacing.get())]
        cmd += ["--margin", str(self.conv_margin.get())]
        cmd += ["--fg-gray", str(self.conv_fg_gray.get())]
        cmd += ["--bg-gray", str(self.conv_bg_gray.get())]
        if not self.conv_colorize.get():
            cmd.append("--no-colorize")
        if self.conv_invert.get():
            cmd.append("--invert")

        if mode == "image":
            output_image = self.conv_output_image.get().strip()
            if output_image:
                cmd += ["--output-image", output_image]
            if self.conv_print.get():
                cmd.append("--print")
            if self.conv_ansi_color_print.get():
                cmd.append("--ansi-color-print")
        else:
            cmd += ["--fps", str(self.conv_fps.get())]
            cmd += ["--codec", self.conv_codec.get().strip() or "mp4v"]
            cmd += ["--max-frames", str(self.conv_max_frames.get())]
            cmd += ["--progress-every", str(self.conv_progress_every.get())]
            cmd += ["--temporal-alpha", str(self.conv_temporal_alpha.get())]
            cmd += ["--preview-every", str(max(1, int(self.conv_preview_every.get())))]
            cmd += ["--preview-fps", str(max(0.0, float(self.conv_preview_fps.get())))]
            if not self.conv_live_preview.get():
                cmd.append("--no-preview")
            text_frames = self.conv_text_frames.get().strip()
            if text_frames:
                cmd += ["--text-frames-dir", text_frames]

        cmd += split_extra_args(self.conv_extra.get())
        return cmd

    def _compose_entropy_cmd(self):
        text = self.ent_text_widget.get("1.0", "end").strip()
        if not text:
            raise ValueError("Entropy seed text is required.")

        cmd = [sys.executable, "-u", str(ENTROPY_SCRIPT), "--text", text]
        cmd += ["--frames", str(self.ent_frames.get())]
        cmd += ["--gens", str(self.ent_gens.get())]
        cmd += ["--population", str(self.ent_population.get())]
        cmd += ["--width", str(self.ent_width.get())]
        cmd += ["--height", str(self.ent_height.get())]
        cmd += ["--ascii-mode", self.ent_ascii_mode.get()]
        cmd += ["--contrast", str(self.ent_contrast.get())]
        cmd += ["--save-dir", self.ent_save_dir.get().strip() or str(APP_DIR / "outputs")]
        cmd += ["--workers", str(self.ent_workers.get())]
        cmd += ["--frame-delay", str(self.ent_frame_delay.get())]
        cmd += ["--frame-kick", str(self.ent_frame_kick.get())]
        cmd += ["--subject-mode", self.ent_subject_mode.get()]

        seed = self.ent_seed.get().strip()
        if seed:
            cmd += ["--seed", seed]
        if self.ent_no_early_stop.get():
            cmd.append("--no-early-stop")
        if self.ent_no_animate.get():
            cmd.append("--no-animate")
        if self.ent_quiet.get():
            cmd.append("--quiet")
        if self.ent_timing.get():
            cmd.append("--timing")
        if self.ent_random_seed.get():
            cmd.append("--random-seed")

        cmd += split_extra_args(self.ent_extra.get())
        return cmd

    def _start_process(self, cmd, cwd):
        if self.proc is not None and self.proc.poll() is None:
            raise RuntimeError("Another process is running. Stop it first.")

        for p in (APP_DIR / "test_outputs", APP_DIR / "outputs"):
            p.mkdir(parents=True, exist_ok=True)

        self._append_log("\n" + "=" * 88 + "\n")
        self._append_log(f"[RUN] {quote_cmd(cmd)}\n")
        self._append_log("=" * 88 + "\n")

        self.proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        def _reader():
            try:
                assert self.proc is not None and self.proc.stdout is not None
                for line in self.proc.stdout:
                    while self.log_queue.qsize() > 3000:
                        time.sleep(0.01)
                    self.log_queue.put(("line", line))
                rc = self.proc.wait()
                self.log_queue.put(("done", rc))
            except Exception as exc:
                self.log_queue.put(("line", f"[SYSTEM] Reader error: {exc}\n"))
                self.log_queue.put(("done", -1))

        self.reader_thread = threading.Thread(target=_reader, daemon=True)
        self.reader_thread.start()

    def _poll_logs(self):
        max_items_per_tick = 500
        processed = 0
        text_parts = []
        done_payload = None
        try:
            while processed < max_items_per_tick:
                kind, payload = self.log_queue.get_nowait()
                processed += 1
                if kind == "line":
                    text_parts.append(payload)
                elif kind == "done":
                    done_payload = payload
        except queue.Empty:
            pass

        if text_parts:
            self._append_log("".join(text_parts))
        if done_payload is not None:
            self._append_log(f"\n[SYSTEM] Process exited with code {done_payload}\n")
            self.proc = None

        next_delay = 20 if not self.log_queue.empty() else 60
        self.after(next_delay, self._poll_logs)

    def _append_log(self, text):
        self._capture_result_paths(text)
        self.log_text.configure(state="normal")
        self._insert_ansi_text(text)
        self._trim_log_if_needed(max_chars=300000)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except Exception:
            pass

    def _trim_log_if_needed(self, max_chars=300000):
        try:
            count = self.log_text.count("1.0", "end-1c", "chars")
            chars = int(count[0]) if count else 0
            if chars > max_chars:
                self.log_text.delete("1.0", f"1.0+{chars - max_chars}c")
        except Exception:
            pass

    def _insert_ansi_text(self, text):
        # For very large chunks, strip ANSI and append plain text to keep UI responsive.
        if len(text) > 50000:
            plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
            self._insert_colored(plain, "#DDDDDD")
            return

        ansi_re = re.compile(r"\x1b\[(.*?)m")
        pos = 0
        for m in ansi_re.finditer(text):
            chunk = text[pos : m.start()]
            if chunk:
                self._insert_colored(chunk, self._ansi_fg)
            code = m.group(1).strip()
            if code == "0":
                self._ansi_fg = "#DDDDDD"
            elif code.startswith("38;2;"):
                parts = code.split(";")
                if len(parts) >= 5:
                    try:
                        r = max(0, min(255, int(parts[2])))
                        g = max(0, min(255, int(parts[3])))
                        b = max(0, min(255, int(parts[4])))
                        self._ansi_fg = f"#{r:02x}{g:02x}{b:02x}"
                    except Exception:
                        pass
            pos = m.end()
        tail = text[pos:]
        if tail:
            self._insert_colored(tail, self._ansi_fg)

    def _insert_colored(self, text, fg_hex):
        color = fg_hex
        if color not in self._ansi_tag_colors and len(self._ansi_tag_colors) >= self._ansi_tag_limit:
            color = "#DDDDDD"
        tag = f"fg_{color}"
        if tag not in self.log_text.tag_names():
            self.log_text.tag_configure(tag, foreground=color)
            self._ansi_tag_colors.add(color)
        self.log_text.insert("end", text, (tag,))

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._ansi_fg = "#DDDDDD"
        self._ansi_tag_colors = {"#DDDDDD"}

    def _stop_process(self):
        if self.proc is None or self.proc.poll() is not None:
            self._append_log("[SYSTEM] No running process.\n")
            return
        self._append_log("[SYSTEM] Stopping process...\n")
        try:
            self.proc.terminate()
        except Exception as exc:
            self._append_log(f"[SYSTEM] terminate failed: {exc}\n")
            try:
                self.proc.kill()
            except Exception:
                pass

    def _open_path(self, path):
        p = Path(path)
        if not p.exists():
            messagebox.showwarning("Open Path", f"Path does not exist:\n{p}")
            return
        try:
            os.startfile(str(p))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Open Path", f"Failed to open path:\n{p}\n\n{exc}")


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.parse_known_args()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
