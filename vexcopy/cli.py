"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import VexCopyError, __version__
from .config import Config, load_config, parse_breaks
from .layout import render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vexcopy",
        description="Generate a VEX Tournament Manager style match list PDF. "
                    "Run with no arguments to open the window.",
    )
    parser.add_argument("config", nargs="?", help="TOML config file (see examples/config.toml)")
    parser.add_argument("-o", "--output", default="match_list.pdf", help="output PDF path")
    parser.add_argument("--gui", action="store_true", help="open the window")
    parser.add_argument("--version", action="version", version=__version__)

    text = parser.add_argument_group("report text")
    text.add_argument("--title")
    text.add_argument("--event", dest="event_name", help="event name, shown under the title")
    text.add_argument("--logo", help="logo image file")
    text.add_argument("--no-logo", action="store_true", help="leave the logo out")
    text.add_argument("--timestamp", help='footer date, like "2026-10-31 14:34", or "now"')

    schedule = parser.add_argument_group("schedule")
    schedule.add_argument("--teams", help='team count, or a list like "1234A,5678B,..."')
    schedule.add_argument("--matches", type=int, help="number of matches")
    schedule.add_argument("--rounds", type=int, help="number of rounds, instead of --matches")
    schedule.add_argument("--seed", help="random seed; the same seed gives the same schedule")
    schedule.add_argument("--min-gap", type=int, help="fewest matches between a team's appearances")
    schedule.add_argument("--csv", help="load match rows from a CSV instead of generating them")
    schedule.add_argument("--generate", action="store_true",
                          help="generate a schedule even if the config names a CSV")

    timing = parser.add_argument_group("timing")
    timing.add_argument("--start", help='first match time, like "2026-10-31 09:00"')
    timing.add_argument("--cycle", type=float, dest="cycle_minutes", help="minutes between matches")
    timing.add_argument("--field", action="append", dest="fields",
                        help="field name; repeat for several, which rotate between matches")
    timing.add_argument("--break", action="append", dest="breaks", metavar="START,MINUTES",
                        help='a break, like "2026-10-31 12:00,60"; repeatable')
    timing.add_argument("--match-prefix", help="text before each match number (default Q)")

    table = parser.add_argument_group("table")
    table.add_argument("--rows-per-page", type=int, help="table rows per page (at most 42)")

    meta = parser.add_argument_group("PDF properties")
    meta.add_argument("--pdf-title")
    meta.add_argument("--author", dest="pdf_author")
    meta.add_argument("--creator", dest="pdf_creator")

    fonts = parser.add_argument_group(
        "fonts", "Default is the system Helvetica. Other fonts won't match the reference exactly."
    )
    fonts.add_argument("--font-file", help="a .ttc with regular, bold and oblique as faces 0, 1, 2")
    fonts.add_argument("--font-regular", help="path, or path#face")
    fonts.add_argument("--font-bold", help="path, or path#face")
    fonts.add_argument("--font-oblique", help="path, or path#face")
    return parser


def apply_arguments(config: Config, args: argparse.Namespace) -> Config:
    for name in ("title", "event_name", "logo", "timestamp", "teams", "matches", "rounds", "seed",
                 "min_gap", "csv", "start", "cycle_minutes", "fields", "match_prefix",
                 "rows_per_page", "pdf_title", "pdf_author", "pdf_creator", "font_regular",
                 "font_bold", "font_oblique"):
        value = getattr(args, name, None)
        if value is not None:
            setattr(config, name, value)
    if args.matches is not None and args.rounds is None:
        config.rounds = None
    if args.rounds is not None and args.matches is None:
        config.matches = None
    if args.no_logo:
        config.logo = ""
    if args.generate:
        config.csv = ""
    if args.breaks:
        config.breaks = parse_breaks(args.breaks)
    if args.font_file:
        config.font_regular = args.font_regular or f"{args.font_file}#0"
        config.font_bold = args.font_bold or f"{args.font_file}#1"
        config.font_oblique = args.font_oblique or f"{args.font_file}#2"
    return config


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    if args.gui or not argv:
        try:
            from .gui import main as gui_main
        except ImportError as exc:
            print(f"error: the window needs tkinter ({exc}). "
                  "Install it with: brew install python-tk@3.14", file=sys.stderr)
            return 1
        return gui_main(args.config)

    try:
        config = load_config(args.config) if args.config else Config()
        apply_arguments(config, args)
        pdf, warnings, rows = render(config)
        Path(args.output).write_bytes(pdf)
    except (VexCopyError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"Wrote {args.output}: {len(rows)} matches")
    return 0
