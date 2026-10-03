"""واجهة أداة تحويل صور الكاميرا إلى صور عادية للآيفون.

نافذة واحدة: اسحب الصور أو المجلدات، اختر الصيغة والجودة، ثم "ابدأ التحويل".
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

import core

BG = "#f4f6f8"
CARD = "#ffffff"
INK = "#1c2127"
MUTED = "#6b7480"
ACCENT = "#0b6bcb"
ACCENT_DARK = "#0a5aa8"
OK = "#0f7a45"
ERR = "#b3261e"
BORDER = "#dfe4ea"

DRAG_BG = "#e8f0fb"
DRAG_BORDER = ACCENT


def _ui_font(size: int = 10, weight: str = "normal", family: str | None = None):
    return tkfont.Font(family=family or "Segoe UI", size=size, weight=weight)


class DropZone(tk.Frame):
    """Big dashed area that accepts dragged files and folders."""

    def __init__(self, master, on_paths, on_browse, **kw):
        super().__init__(master, bg=CARD, highlightthickness=2,
                         highlightbackground=BORDER, highlightcolor=BORDER, **kw)
        self.on_paths = on_paths
        self.on_browse = on_browse
        self._dnd_ready = False

        self.icon = tk.Label(self, text="\u2b07", font=_ui_font(34), bg=CARD, fg=ACCENT)
        self.icon.pack(pady=(26, 2))

        self.title = tk.Label(
            self, text="اسحب صور الكاميرا هنا أو اضغط لاختيار الملفات",
            font=_ui_font(13, "bold"), bg=CARD, fg=INK, wraplength=440, justify="center")
        self.title.pack(pady=(0, 4))

        self.hint = tk.Label(
            self, text="NEF · CR2 · CR3 · ARW · RAF · RW2 · ORF · PEF · SRW · DNG\nوملفات JPG و PNG كذلك",
            font=_ui_font(9), bg=CARD, fg=MUTED, wraplength=460, justify="center")
        self.hint.pack(pady=(0, 6))

        self.pick = tk.Button(
            self, text="اختيار ملفات", command=self.on_browse, font=_ui_font(10, "bold"),
            bg=ACCENT, fg="#ffffff", activebackground=ACCENT_DARK, activeforeground="#ffffff",
            relief="flat", bd=0, padx=22, pady=9, cursor="hand2")
        self.pick.pack(pady=(4, 26))

        for widget in (self, self.icon, self.title, self.hint):
            widget.bind("<Button-1>", lambda _e: self.on_browse())

        self._enable_drop()

    def _enable_drop(self) -> None:
        try:
            from tkinterdnd2 import DND_FILES
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
            self.dnd_bind("<<DropEnter>>", lambda _e: self._highlight(True))
            self.dnd_bind("<<DropLeave>>", lambda _e: self._highlight(False))
            self._dnd_ready = True
        except Exception:
            # No drag-and-drop support: browsing still works.
            self._dnd_ready = False

    def _highlight(self, on: bool) -> None:
        if on:
            self.configure(bg=DRAG_BG, highlightbackground=DRAG_BORDER, highlightcolor=DRAG_BORDER)
            for w in (self.icon, self.title, self.hint):
                w.configure(bg=DRAG_BG)
        else:
            self.configure(bg=CARD, highlightbackground=BORDER, highlightcolor=BORDER)
            for w in (self.icon, self.title, self.hint):
                w.configure(bg=CARD)

    def _on_drop(self, event) -> None:
        self._highlight(False)
        paths = self._parse(event.data)
        if paths:
            self.on_paths(paths)

    @staticmethod
    def _parse(data: str) -> list[str]:
        """Turn Tk's drop payload into a plain path list (handles {a b} quoting)."""
        out: list[str] = []
        chunk: list[str] = []
        in_brace = False
        for ch in str(data):
            if ch == "{":
                in_brace = True
            elif ch == "}":
                in_brace = False
            elif ch == " " and not in_brace:
                if chunk:
                    out.append("".join(chunk))
                    chunk = []
            else:
                chunk.append(ch)
        if chunk:
            out.append("".join(chunk))
        return [p for p in (t.strip().strip('"') for t in out) if p]


class ConverterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.files: list[str] = []
        self.output_dir = ""
        self.worker: threading.Thread | None = None
        self.cancel = threading.Event()
        # Worker threads never touch Tk: they post events here and the main
        # thread drains them on a timer.
        self.events: queue.Queue = queue.Queue()
        self._drain_job = None

        root.title("محوّل صور الكاميرا إلى صور عادية")
        root.configure(bg=BG)
        self._fit_window(1060, 780)

        self._build_style()
        self._build_header()
        self._build_body()
        self._build_footer()
        self._build_options()

        self._report_environment()
        self._refresh_summary()

    # -- layout ------------------------------------------------------------
    def _fit_window(self, want_w: int, want_h: int) -> None:
        """Size the window to the screen, never taller than the usable area."""
        try:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        except Exception:
            self.root.geometry(f"{want_w}x{want_h}")
            return
        margin = 80
        w = min(want_w, max(760, sw - margin))
        h = min(want_h, max(560, sh - margin))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(min(w, 780), min(h, 560))

    def _build_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TCombobox", padding=4)
        style.configure("Horizontal.TProgressbar", troughcolor="#e3e8ee",
                        background=ACCENT, thickness=14)

    def _build_header(self) -> None:
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=22, pady=(16, 6))
        tk.Label(bar, text="محوّل صور الكاميرا", font=_ui_font(19, "bold"), bg=BG, fg=INK).pack(anchor="e")
        tk.Label(bar, text="حوّل صور RAW من أي كاميرا إلى HEIC أو JPG جاهزة للآيفون، "
                          "مع الحفاظ على بيانات التصوير: التاريخ، الكاميرا، العدسة، الموقع.",
                 font=_ui_font(10), bg=BG, fg=MUTED, justify="right",
                 wraplength=900, anchor="e").pack(anchor="e", fill="x", pady=(3, 0))

    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=6)
        body.columnconfigure(0, weight=5, uniform="cols")
        body.columnconfigure(1, weight=3, uniform="cols")
        body.rowconfigure(0, weight=1)

        # left: drop zone + list
        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        self.drop = DropZone(left, on_paths=self.add_paths, on_browse=self.browse_files)
        self.drop.grid(row=0, column=0, sticky="ew")

        listcard = tk.Frame(left, bg=CARD, highlightthickness=1,
                            highlightbackground=BORDER, highlightcolor=BORDER)
        listcard.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        listcard.rowconfigure(0, weight=1)
        listcard.columnconfigure(0, weight=1)

        head = tk.Frame(listcard, bg=CARD)
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        head.columnconfigure(0, weight=1)
        self.summary = tk.Label(head, text="لا توجد صور بعد", font=_ui_font(10, "bold"),
                                bg=CARD, fg=INK)
        self.summary.grid(row=0, column=0, sticky="w")
        tk.Button(head, text="مسح الكل", command=self.clear_files, font=_ui_font(9),
                  bg=CARD, fg=MUTED, activebackground=CARD, relief="flat", bd=0,
                  cursor="hand2").grid(row=0, column=1, sticky="e")

        wrap = tk.Frame(listcard, bg=CARD)
        wrap.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.listbox = tk.Listbox(wrap, font=_ui_font(9), bg="#fbfcfd", fg=INK,
                                  selectbackground="#d7e6f8", selectforeground=INK,
                                  relief="flat", bd=0, highlightthickness=1, height=8,
                                  width=30, highlightbackground=BORDER, activestyle="none")
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.configure(yscrollcommand=scroll.set)

        # right: options card
        self.right = tk.Frame(body, bg=CARD, highlightthickness=1,
                              highlightbackground=BORDER, highlightcolor=BORDER)
        self.right.grid(row=0, column=1, sticky="nsew")
        self.right.columnconfigure(0, weight=1)

    def _card_title(self, parent, text: str) -> None:
        tk.Label(parent, text=text, font=_ui_font(12, "bold"), bg=CARD, fg=INK).pack(
            anchor="e", padx=14, pady=(14, 2))

    def _build_options(self) -> None:
        p = self.right
        self._card_title(p, "الإعدادات")

        tk.Label(p, text="الصيغة", font=_ui_font(9, "bold"), bg=CARD, fg=MUTED).pack(
            anchor="e", padx=14, pady=(12, 2))
        self.format_var = tk.StringVar(value="heic")
        self.format_box = ttk.Combobox(
            p, state="readonly", justify="right", font=_ui_font(10),
            values=[core.FORMAT_LABELS_AR["heic"], core.FORMAT_LABELS_AR["jpg"]])
        self.format_box.set(core.FORMAT_LABELS_AR["heic"])
        self.format_box.pack(fill="x", padx=14)
        self.format_box.bind("<<ComboboxSelected>>", lambda _e: self._on_format_change())

        tk.Label(p, text="الجودة", font=_ui_font(9, "bold"), bg=CARD, fg=MUTED).pack(
            anchor="e", padx=14, pady=(12, 2))
        self.preset_var = tk.StringVar(value=core.DEFAULT_PRESET)
        values = [core.PRESET_LABELS_AR[k] for k in ("best", "balanced", "small")]
        self.preset_box = ttk.Combobox(p, state="readonly", justify="right",
                                       font=_ui_font(10), values=values)
        self.preset_box.set(core.PRESET_LABELS_AR[core.DEFAULT_PRESET])
        self.preset_box.pack(fill="x", padx=14)
        self.preset_box.bind("<<ComboboxSelected>>", lambda _e: self._on_preset_change())
        self.preset_note = tk.Label(p, text="", font=_ui_font(8), bg=CARD, fg=MUTED,
                                    justify="right", wraplength=290)
        self.preset_note.pack(anchor="e", padx=14, pady=(3, 0))
        self._on_preset_change()

        tk.Label(p, text="مكان الحفظ", font=_ui_font(9, "bold"), bg=CARD, fg=MUTED).pack(
            anchor="e", padx=14, pady=(14, 2))
        self.out_label = tk.Label(p, text="بجانب كل صورة، داخل مجلد Converted",
                                  font=_ui_font(9), bg=CARD, fg=INK, justify="right",
                                  wraplength=290, anchor="e")
        self.out_label.pack(anchor="e", padx=14, fill="x")
        row = tk.Frame(p, bg=CARD)
        row.pack(fill="x", padx=14, pady=(6, 0))
        for text, cmd in (("مجلد مخصّص", self.choose_output), ("الافتراضي", self.reset_output)):
            tk.Button(row, text=text, command=cmd, font=_ui_font(9), bg="#eef1f5", fg=INK,
                      activebackground="#e2e7ee", relief="flat", bd=0, padx=12, pady=6,
                      cursor="hand2").pack(side="right", padx=(6, 0))
        self.same_folder_var = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="ضع النتيجة بجانب الصورة الأصلية (بدون مجلد فرعي)",
                       variable=self.same_folder_var, font=_ui_font(9), bg=CARD, fg=INK,
                       activebackground=CARD, selectcolor=CARD, anchor="e",
                       justify="right").pack(anchor="e", padx=14, pady=(8, 0))

        self.keep_time_var = tk.BooleanVar(value=True)
        tk.Checkbutton(p, text="اجعل تاريخ الملف مثل تاريخ التصوير",
                       variable=self.keep_time_var, font=_ui_font(9), bg=CARD, fg=INK,
                       activebackground=CARD, selectcolor=CARD, anchor="e",
                       justify="right").pack(anchor="e", padx=14)

        self.open_after_var = tk.BooleanVar(value=False)
        tk.Checkbutton(p, text="افتح مجلد النتيجة بعد الانتهاء",
                       variable=self.open_after_var, font=_ui_font(9), bg=CARD, fg=INK,
                       activebackground=CARD, selectcolor=CARD, anchor="e",
                       justify="right").pack(anchor="e", padx=14)

        sep = tk.Frame(p, bg=BORDER, height=1)
        sep.pack(fill="x", padx=14, pady=(14, 0))

        self.env_note = tk.Label(p, text="", font=_ui_font(8), bg=CARD, fg=MUTED,
                                 justify="right", wraplength=290)
        self.env_note.pack(anchor="e", padx=14, pady=(10, 14), fill="x")

    def _build_footer(self) -> None:
        self.footer = tk.Frame(self.root, bg=BG)
        self.footer.pack(fill="x", padx=22, pady=(4, 18))

        self.progress = ttk.Progressbar(self.footer, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 8))

        row = tk.Frame(self.footer, bg=BG)
        row.pack(fill="x")
        # Right-to-left: primary action on the right, status on the left.
        self.status = tk.Label(row, text="جاهز", font=_ui_font(10), bg=BG, fg=MUTED, anchor="w")
        self.status.pack(side="left")

        self.start_btn = tk.Button(row, text="ابدأ التحويل", command=self.start,
                                   font=_ui_font(12, "bold"), bg=ACCENT, fg="#ffffff",
                                   activebackground=ACCENT_DARK, activeforeground="#ffffff",
                                   relief="flat", bd=0, padx=30, pady=11, cursor="hand2")
        self.start_btn.pack(side="right")

        self.cancel_btn = tk.Button(row, text="إيقاف", command=self.stop,
                                    font=_ui_font(10), bg="#eef1f5", fg=INK,
                                    activebackground="#e2e7ee", relief="flat", bd=0,
                                    padx=18, pady=11, cursor="hand2", state="disabled")
        self.cancel_btn.pack(side="right", padx=(0, 8))

    # -- state -------------------------------------------------------------
    def _report_environment(self) -> None:
        notes = []
        if not core.rawpy_available():
            notes.append("محرّك قراءة RAW غير متاح — التحويل سيعمل على JPG/PNG فقط.")
        elif not core.heif_available():
            notes.append("صيغة HEIC غير متاحة هنا، لذلك سيتم الحفظ بصيغة JPG.")
        else:
            notes.append("محرّك RAW وصيغة HEIC جاهزان.")
        try:
            from tkinterdnd2 import DND_FILES  # noqa: F401
            notes.append("السحب والإفلات مفعّل.")
        except Exception:
            notes.append("السحب والإفلات غير مفعّل — استخدم زر «اختيار ملفات».")
        self.env_note.configure(text=" ".join(notes))

    def _on_format_change(self) -> None:
        current = self.format_box.get()
        if current.startswith("HEIC") and not core.heif_available():
            messagebox.showwarning(
                "HEIC غير متاح",
                "هذا الجهاز لا يستطيع إنشاء ملفات HEIC.\nسيتم الحفظ بصيغة JPG.")
            self.format_box.set(core.FORMAT_LABELS_AR["jpg"])

    def _on_preset_change(self) -> None:
        label = self.preset_box.get()
        key = next((k for k, v in core.PRESET_LABELS_AR.items() if v == label),
                   core.DEFAULT_PRESET)
        self.preset_var.set(key)
        notes = {
            "best": "بدون تصغير — أعلى تفاصيل، حجم أكبر.",
            "balanced": "حتى 4032 بكسل — نفس عرض شاشة الآيفون تقريبًا، وأصغر بخمس مرات.",
            "small": "حتى 2560 بكسل — مناسب للإرسال وتوفير المساحة.",
        }
        self.preset_note.configure(text=notes.get(key, ""))
        self._refresh_summary()

    def current_format(self) -> str:
        return "heic" if self.format_box.get().startswith("HEIC") else "jpg"

    # -- files -------------------------------------------------------------
    def browse_files(self) -> None:
        patterns = " ".join(f"*{e}" for e in core.SUPPORTED_EXTENSIONS)
        paths = filedialog.askopenfilenames(
            title="اختر صور الكاميرا",
            filetypes=[("صور مدعومة", patterns), ("كل الملفات", "*.*")])
        if paths:
            self.add_paths(list(paths))

    def add_paths(self, paths: list[str]) -> None:
        found = core.collect_inputs(list(paths))
        added = [p for p in found if p not in self.files]
        self.files.extend(added)
        self._refresh_summary()
        skipped = len(paths) - len(found)
        if not found:
            self.status.configure(text="لم يتم العثور على صور مدعومة في ما أفلتته.", fg=ERR)
        elif skipped > 0:
            self.status.configure(text=f"تمت إضافة {len(added)} صورة.", fg=MUTED)

    def clear_files(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.files.clear()
        self._refresh_summary()
        self.status.configure(text="جاهز", fg=MUTED)

    def _refresh_summary(self) -> None:
        self.listbox.delete(0, "end")
        for path in self.files:
            self.listbox.insert("end", os.path.basename(path))
        count = len(self.files)
        if count == 0:
            self.summary.configure(text="لا توجد صور بعد")
            return
        total = 0.0
        sizes = 0
        for path in self.files:
            try:
                total += os.path.getsize(path)
                sizes += 1
            except OSError:
                pass
        self.summary.configure(
            text=f"{count} صورة (بحجم {total / 1e6:.0f} ميجابايت من الملفات الأصلية)")

    def choose_output(self) -> None:
        folder = filedialog.askdirectory(title="اختر مجلد الحفظ")
        if folder:
            self.output_dir = folder
            self.out_label.configure(text=folder)

    def reset_output(self) -> None:
        self.output_dir = ""
        self.out_label.configure(text="بجانب كل صورة، داخل مجلد Converted")

    def _dest_for(self, source: str) -> str:
        if self.output_dir:
            return self.output_dir
        base = os.path.dirname(os.path.abspath(source))
        if self.same_folder_var.get():
            return base
        return os.path.join(base, "Converted")

    # -- run ---------------------------------------------------------------
    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showinfo("لا توجد صور", "أضف صورًا أولًا.")
            return
        if not core.rawpy_available():
            missing = [p for p in self.files if core.looks_like_raw(p)]
            if missing:
                messagebox.showwarning(
                    "محرّك RAW غير متاح",
                    f"{len(missing)} ملف من صيغ RAW ولا يمكن قراءتها على هذا الجهاز.\n"
                    "الملفات العادية (JPG/PNG) ستُحوّل بشكل طبيعي.")

        self.cancel.clear()
        self.start_btn.configure(state="disabled", text="جارٍ التحويل…")
        self.cancel_btn.configure(state="normal")
        self.progress.configure(value=0)
        self.status.configure(text="جارٍ التحويل…", fg=MUTED)

        snapshot = list(self.files)
        self.worker = threading.Thread(
            target=self._run, args=(snapshot, self.current_format(),
                                    self.preset_var.get(), self.keep_time_var.get()),
            daemon=True)
        self.worker.start()
        self._schedule_drain()

    def stop(self) -> None:
        self.cancel.set()
        self.status.configure(text="سيتم الإيقاف بعد الصورة الحالية…", fg=MUTED)

    def _run(self, files: list[str], fmt: str, preset: str, keep_time: bool) -> None:
        """Worker thread: convert files and post progress events (no Tk calls)."""
        total = len(files)
        done = 0
        failed: list[str] = []
        outputs: list[str] = []
        taken: set[str] = set()

        for index, source in enumerate(files, 1):
            if self.cancel.is_set():
                break
            dest_dir = self._dest_for(source)
            try:
                result = core.convert_one(source, dest_dir, fmt, preset, taken, keep_time)
            except Exception as exc:  # noqa: BLE001 - must not kill the worker
                result = core.Result(source=source, ok=False,
                                     message=f"{type(exc).__name__}: {exc}")
            if result.ok and result.output:
                done += 1
                outputs.append(result.output)
            else:
                failed.append(f"{os.path.basename(source)} — {result.message}")
            self.events.put(("progress", index, total, os.path.basename(source),
                             result.ok, result.message))

        self.events.put(("done", done, total, failed, outputs, self.cancel.is_set()))

    # -- main-thread event pump -------------------------------------------
    def _schedule_drain(self) -> None:
        self._drain_job = self.root.after(80, self._drain)

    def _drain(self) -> None:
        """Runs on the UI thread: apply everything the worker has queued."""
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "progress":
                _, index, total, name, ok, message = event
                self.progress.configure(value=(index / total) * 100)
                if ok:
                    self.status.configure(text=f"[{index}/{total}] تم — {name}", fg=OK)
                else:
                    self.status.configure(text=f"[{index}/{total}] فشل — {name}", fg=ERR)
            elif kind == "done":
                _, done, total, failed, outputs, cancelled = event
                self._finish(done, total, failed, outputs, cancelled)
                return  # _finish stops the pump
        if self.worker is not None and self.worker.is_alive():
            self._schedule_drain()
        else:
            # Worker ended without a "done" event (crash): settle the UI.
            self.events.put(("done", 0, max(1, len(self.files)),
                             ["توقف غير متوقع"], [], False))
            self._schedule_drain()

    def _finish(self, done: int, total: int, failed: list[str],
                outputs: list[str], cancelled: bool) -> None:
        """Final UI state. Runs on the main thread."""
        if self._drain_job is not None:
            try:
                self.root.after_cancel(self._drain_job)
            except Exception:
                pass
            self._drain_job = None

        self.start_btn.configure(state="normal", text="ابدأ التحويل")
        self.cancel_btn.configure(state="disabled")
        total = max(total, 1)
        self.progress.configure(value=100 if (done + len(failed)) >= total else
                                ((done + len(failed)) / total) * 100)

        stopped = cancelled and (done + len(failed)) < total
        if stopped:
            self.status.configure(text=f"تم الإيقاف — حُوّلت {done} من {total}.", fg=MUTED)
        elif failed:
            self.status.configure(text=f"تم تحويل {done} من {total}، وفشل {len(failed)}.", fg=ERR)
        else:
            self.status.configure(text=f"تم بنجاح — {done} صورة.", fg=OK)

        if self.open_after_var.get() and outputs:
            self._open_folder(os.path.dirname(outputs[0]))

        if failed:
            preview = "\n".join(failed[:8])
            more = f"\n…و{len(failed) - 8} أخرى" if len(failed) > 8 else ""
            messagebox.showwarning("بعض الملفات لم تُحوّل", preview + more)

    @staticmethod
    def _open_folder(path: str) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606 - user asked to open it
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass


def main() -> int:
    # Command-line mode: lets the same exe run as a script or scheduled task,
    # and makes the frozen build testable end to end.
    if len(sys.argv) > 1:
        try:
            return core._cli(sys.argv[1:])
        except SystemExit:
            raise
        except Exception:
            return 3

    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()

    try:
        ConverterApp(root)
    except Exception:
        messagebox.showerror("خطأ في التشغيل", traceback.format_exc())
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
