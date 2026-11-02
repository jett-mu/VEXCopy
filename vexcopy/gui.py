"""Native Mac window for editing the report settings and generating a PDF."""

from __future__ import annotations

import ctypes
import ctypes.util
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import VexCopyError
from .config import Config, load_config, parse_breaks
from .layout import render
from .schedule import parse_datetime

PAD = 6
LABEL_WIDTH = 118     # keeps fields aligned from one section to the next
BUTTON_WIDTH = 92
ENTRY_CHARS = 36


class App:
    def __init__(self, root: tk.Tk, config: Config | None = None):
        self.root = root
        self.base = config or Config()
        self.vars: dict[str, tk.StringVar] = {}
        root.title("VEX Match List")
        root.resizable(False, False)

        frame = ttk.Frame(root, padding=14)
        frame.grid(sticky="nsew")
        left, right = ttk.Frame(frame), ttk.Frame(frame)
        left.grid(row=0, column=0, sticky="n", padx=(0, 12))
        right.grid(row=0, column=1, sticky="n")

        report = self._section(left, "Report", 0, has_buttons=True)
        self._entry(report, "title", "Title", 0)
        self._entry(report, "event_name", "Event name", 1)
        self._entry(report, "logo", "Logo file", 2, browse=self._browse_logo,
                    hint="Leave empty for no logo")
        self._entry(report, "timestamp", "Footer date", 3,
                    hint='"now", or a date like 2026-10-31 14:34')

        self._entry(report, "rows_per_page", "Rows per page", 4, hint="42 is the most that fits")

        matches = self._section(left, "Matches", 1, has_buttons=True)
        self.vars["source"] = tk.StringVar(value="generate")
        source = ttk.Frame(matches)
        source.grid(row=0, column=1, columnspan=2, sticky="w", pady=(0, PAD))
        ttk.Radiobutton(source, text="Generate a schedule", value="generate",
                        variable=self.vars["source"], command=self._sync_source).pack(side="left")
        ttk.Radiobutton(source, text="Load from CSV", value="csv",
                        variable=self.vars["source"], command=self._sync_source).pack(
            side="left", padx=(14, 0))
        self.team_entry = self._entry(matches, "teams", "Teams", 1,
                                      hint="A count, or numbers like 1234A, 5678B, ...")
        self.match_entry = self._entry(matches, "matches", "Matches", 2)
        self.seed_entry = self._entry(matches, "seed", "Random seed", 3,
                                      hint="The same seed gives the same schedule")
        self.gap_entry = self._entry(matches, "min_gap", "Minimum gap", 4,
                                     hint="Matches between a team's appearances. Empty = automatic")
        self.csv_entry = self._entry(matches, "csv", "CSV file", 5, browse=self._browse_csv)
        self.csv_button = matches.grid_slaves(row=5, column=2)[0]

        timing = self._section(right, "Timing", 0)
        self._entry(timing, "start", "First match", 0, hint="Like 2026-10-31 09:00")
        self._entry(timing, "cycle_minutes", "Minutes per match", 1)
        self._entry(timing, "fields", "Fields", 2, hint="Separate several with commas")
        self._entry(timing, "match_prefix", "Match prefix", 3)
        ttk.Label(timing, text="Breaks").grid(row=4, column=0, sticky="ne", padx=(0, PAD), pady=3)
        self.breaks_text = tk.Text(timing, width=ENTRY_CHARS, height=4, font="TkDefaultFont",
                                   highlightthickness=1, highlightbackground="#c4c4c4",
                                   relief="flat", padx=4, pady=3)
        self.breaks_text.grid(row=4, column=1, sticky="ew", pady=3)
        ttk.Label(timing, text="One per line: 2026-10-31 12:00, 60", foreground="#777777").grid(
            row=5, column=1, sticky="w")

        document = self._section(right, "PDF properties", 1)
        self._entry(document, "pdf_title", "Document title", 0, hint="Empty = same as Title")
        self._entry(document, "pdf_author", "Author", 1)
        self._entry(document, "pdf_creator", "Creator", 2)

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.status = ttk.Label(buttons, text="", foreground="#777777")
        self.status.pack(side="left")
        ttk.Button(buttons, text="Generate PDF…", command=self.on_generate,
                   default="active").pack(side="right")
        ttk.Button(buttons, text="Load Config…", command=self.on_load).pack(
            side="right", padx=PAD)

        self.load_values(self.base)

    # ---- construction ----------------------------------------------------

    def _section(self, parent, title: str, row: int, has_buttons: bool = False) -> ttk.LabelFrame:
        section = ttk.LabelFrame(parent, text=title, padding=10)
        section.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        section.columnconfigure(0, minsize=LABEL_WIDTH)
        section.columnconfigure(1, weight=1)
        if has_buttons:
            section.columnconfigure(2, minsize=BUTTON_WIDTH)
        return section

    def _entry(self, parent, name: str, label: str, row: int, browse=None, hint: str | None = None):
        self.vars[name] = tk.StringVar()
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=(0, PAD), pady=3)
        entry = ttk.Entry(parent, textvariable=self.vars[name], width=ENTRY_CHARS)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        if browse:
            ttk.Button(parent, text="Choose…", command=browse).grid(row=row, column=2, padx=(PAD, 0))
        if hint:
            self._tip(entry, hint)
        return entry

    def _tip(self, widget, text: str) -> None:
        """Show a hint in the status line while the field has focus."""
        widget.bind("<FocusIn>", lambda _e: self.status.configure(text=text), add="+")
        widget.bind("<FocusOut>", lambda _e: self.status.configure(text=""), add="+")

    # ---- values ----------------------------------------------------------

    def load_values(self, config: Config) -> None:
        self.base = config
        teams = config.teams
        if isinstance(teams, (list, tuple)):
            teams = ", ".join(str(t) for t in teams)
        matches = config.matches
        if matches is None:
            per_round = max(self._team_count(teams) // 4, 1)
            matches = (config.rounds if config.rounds is not None else 10) * per_round
        values = {
            "title": config.title,
            "event_name": config.event_name,
            "rows_per_page": config.rows_per_page,
            "logo": config.logo,
            "timestamp": config.timestamp,
            "teams": teams,
            "matches": matches,
            "seed": config.seed,
            "min_gap": "" if config.min_gap is None else config.min_gap,
            "csv": config.csv,
            "start": config.start,
            "cycle_minutes": f"{config.cycle_minutes:g}",
            "fields": ", ".join(config.fields),
            "match_prefix": config.match_prefix,
            "pdf_title": config.pdf_title or "",
            "pdf_author": config.pdf_author,
            "pdf_creator": config.pdf_creator,
        }
        for name, value in values.items():
            self.vars[name].set(str(value))
        self.vars["source"].set("csv" if config.csv else "generate")
        self.breaks_text.delete("1.0", "end")
        lines = [f"{start:%Y-%m-%d %H:%M}, {minutes:g}" for start, minutes in config.breaks]
        self.breaks_text.insert("1.0", "\n".join(lines))
        self._sync_source()

    @staticmethod
    def _team_count(teams) -> int:
        text = str(teams).strip()
        return int(text) if text.isdigit() else len([t for t in text.split(",") if t.strip()])

    def _sync_source(self) -> None:
        use_csv = self.vars["source"].get() == "csv"
        for widget in (self.team_entry, self.match_entry, self.seed_entry, self.gap_entry):
            widget.state(["disabled"] if use_csv else ["!disabled"])
        self.csv_entry.state(["!disabled"] if use_csv else ["disabled"])
        self.csv_button.state(["!disabled"] if use_csv else ["disabled"])

    def _number(self, name: str, label: str, kind=int):
        text = self.vars[name].get().strip()
        try:
            return kind(text)
        except ValueError:
            raise VexCopyError(f"{label} must be a number, not {text!r}.") from None

    def to_config(self) -> Config:
        """Build settings from the form, keeping config-only values like columns and fonts."""
        config = self.base
        result = Config(
            title=self.vars["title"].get(),
            event_name=self.vars["event_name"].get(),
            logo=self.vars["logo"].get().strip(),
            timestamp=self.vars["timestamp"].get().strip() or "now",
            pdf_title=self.vars["pdf_title"].get() or None,
            pdf_author=self.vars["pdf_author"].get(),
            pdf_creator=self.vars["pdf_creator"].get(),
            start=parse_datetime(self.vars["start"].get()),
            cycle_minutes=self._number("cycle_minutes", "Minutes per match", float),
            breaks=parse_breaks(
                line for line in self.breaks_text.get("1.0", "end").splitlines() if line.strip()
            ),
            fields=[f.strip() for f in self.vars["fields"].get().split(",") if f.strip()],
            match_prefix=self.vars["match_prefix"].get(),
            rows_per_page=self._number("rows_per_page", "Rows per page"),
            columns=config.columns,
            font_regular=config.font_regular,
            font_bold=config.font_bold,
            font_oblique=config.font_oblique,
        )
        if self.vars["source"].get() == "csv":
            result.csv = self.vars["csv"].get().strip()
            if not result.csv:
                raise VexCopyError("Choose a CSV file, or switch to generating a schedule.")
        else:
            result.teams = self.vars["teams"].get()
            result.matches = self._number("matches", "Matches")
            result.seed = self.vars["seed"].get().strip() or 1
            if self.vars["min_gap"].get().strip():
                result.min_gap = self._number("min_gap", "Minimum gap")
        return result

    # ---- actions ---------------------------------------------------------

    def generate_to(self, path: str) -> tuple[list[str], int]:
        pdf, warnings, rows = render(self.to_config())
        Path(path).write_bytes(pdf)
        return warnings, len(rows)

    def on_generate(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save Match List", defaultextension=".pdf",
            initialfile="match_list.pdf", filetypes=[("PDF", "*.pdf")],
        )
        if not path:
            return
        self.status.configure(text="Generating…")
        self.root.update_idletasks()
        try:
            warnings, count = self.generate_to(path)
        except (VexCopyError, OSError) as exc:
            self.status.configure(text="")
            messagebox.showerror("Couldn't generate the PDF", str(exc), parent=self.root)
            return
        self.status.configure(text=f"Saved {count} matches to {Path(path).name}")
        if warnings:
            messagebox.showwarning("PDF saved, with warnings", "\n".join(warnings),
                                   parent=self.root)
        subprocess.run(["open", path], check=False)

    def on_load(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="Load Config", filetypes=[("TOML config", "*.toml")]
        )
        if not path:
            return
        try:
            self.load_values(load_config(path))
        except VexCopyError as exc:
            messagebox.showerror("Couldn't load the config", str(exc), parent=self.root)
            return
        self.status.configure(text=f"Loaded {Path(path).name}")

    def _browse_logo(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="Choose Logo",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.tif *.tiff"), ("All files", "*")],
        )
        if path:
            self.vars["logo"].set(path)

    def _browse_csv(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="Choose CSV", filetypes=[("CSV", "*.csv")]
        )
        if path:
            self.vars["csv"].set(path)


def bring_to_front(root: tk.Tk) -> None:
    """Activate the app, since a window started from Terminal can open behind it."""
    root.lift()
    try:
        objc = ctypes.CDLL(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_msgSend.restype = ctypes.c_void_p
        objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        app = objc.objc_msgSend(objc.objc_getClass(b"NSApplication"),
                                objc.sel_registerName(b"sharedApplication"))
        activate = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)(
            ("objc_msgSend", objc))
        activate(app, objc.sel_registerName(b"activateIgnoringOtherApps:"), True)
    except (OSError, AttributeError, TypeError):
        pass  # the window still works; it may just open behind others


def main(config_path: str | None = None) -> int:
    root = tk.Tk()
    try:
        config = load_config(config_path) if config_path else Config()
    except VexCopyError as exc:
        root.withdraw()
        messagebox.showerror("Couldn't load the config", str(exc))
        return 1
    App(root, config)
    root.update_idletasks()
    bring_to_front(root)
    root.mainloop()
    return 0
