"""Report settings, with defaults matching the reference layout."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import VexCopyError
from .pdfwriter import SYSTEM_HELVETICA
from .schedule import parse_datetime

ASSETS = Path(__file__).resolve().parent.parent / "assets"
DEFAULT_LOGO = str(ASSETS / "logo.png")


@dataclass
class Column:
    key: str
    label: str
    weight: float


def default_columns() -> list[Column]:
    return [
        Column("match", "Match", 3),
        Column("field", "Field", 9),
        Column("time", "Time", 6),
        Column("red1", "Red 1", 3),
        Column("red2", "Red 2", 3),
        Column("blue1", "Blue 1", 3),
        Column("blue2", "Blue 2", 3),
    ]


@dataclass
class Config:
    title: str = "Qualification Match List"
    event_name: str = "Event Name"
    logo: str = DEFAULT_LOGO          # empty means no logo
    timestamp: str | datetime = "now"

    pdf_title: str | None = None      # defaults to the report title
    pdf_author: str = ""
    pdf_creator: str = "vexcopy"

    teams: object = 24
    matches: int | None = None
    rounds: int | None = None
    seed: object = 1
    min_gap: int | None = None
    csv: str = ""

    start: object = field(default_factory=lambda: f"{date.today():%Y-%m-%d} 09:00")
    cycle_minutes: float = 1
    breaks: list = field(default_factory=list)   # (datetime, minutes) pairs
    fields: list = field(default_factory=lambda: ["Main Field"])
    match_prefix: str = "Q"

    rows_per_page: int = 42
    columns: list = field(default_factory=default_columns)

    font_regular: str = f"{SYSTEM_HELVETICA}#0"
    font_bold: str = f"{SYSTEM_HELVETICA}#1"
    font_oblique: str = f"{SYSTEM_HELVETICA}#2"


def parse_breaks(items) -> list:
    """Accept {start, minutes} tables or 'START,MINUTES' text."""
    result = []
    for item in items:
        if isinstance(item, dict):
            unknown = set(item) - {"start", "minutes"}
            if unknown or not {"start", "minutes"} <= set(item):
                raise VexCopyError("Each break needs exactly a start and minutes.")
            start, minutes = item["start"], item["minutes"]
        elif isinstance(item, (tuple, list)):
            start, minutes = item
        else:
            parts = str(item).rsplit(",", 1)
            if len(parts) != 2:
                raise VexCopyError(f"Could not read break {item!r}. Use: 2026-10-31 12:00, 60")
            start, minutes = parts
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            raise VexCopyError(f"Break length {minutes!r} isn't a number of minutes.") from None
        if minutes <= 0:
            raise VexCopyError("A break must be longer than 0 minutes.")
        result.append((parse_datetime(start), minutes))
    return result


def _take(table: dict, name: str, allowed: set) -> dict:
    unknown = sorted(set(table) - allowed)
    if unknown:
        where = f" in [{name}]" if name else ""
        raise VexCopyError(
            f"Unknown setting(s){where}: {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(allowed))}."
        )
    return table


def load_config(path: str) -> Config:
    """Read a TOML config. Relative paths are relative to the config file."""
    config_path = Path(path)
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        raise VexCopyError(f"Config file not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise VexCopyError(f"Config file {path} has a syntax error: {exc}") from None

    def resolve(value: str) -> str:
        if not value:
            return value
        name, marker, index = value.partition("#")
        full = Path(name).expanduser()
        if not full.is_absolute():
            full = (config_path.resolve().parent / full).resolve()
        return f"{full}{marker}{index}"

    _take(data, "", {"title", "event_name", "logo", "timestamp", "pdf", "schedule",
                     "timing", "table", "fonts"})
    pdf = _take(data.get("pdf", {}), "pdf", {"title", "author", "creator"})
    schedule = _take(data.get("schedule", {}), "schedule",
                     {"teams", "matches", "rounds", "seed", "min_gap", "csv"})
    timing = _take(data.get("timing", {}), "timing",
                   {"start", "cycle_minutes", "fields", "match_prefix", "breaks"})
    table = _take(data.get("table", {}), "table", {"rows_per_page", "columns"})
    fonts = _take(data.get("fonts", {}), "fonts", {"regular", "bold", "oblique"})

    config = Config()
    for key in ("title", "event_name", "timestamp"):
        if key in data:
            setattr(config, key, data[key])
    if "logo" in data:
        config.logo = resolve(data["logo"])
    if "title" in pdf:
        config.pdf_title = pdf["title"]
    if "author" in pdf:
        config.pdf_author = pdf["author"]
    if "creator" in pdf:
        config.pdf_creator = pdf["creator"]
    for key in ("teams", "matches", "rounds", "seed", "min_gap"):
        if key in schedule:
            setattr(config, key, schedule[key])
    if "csv" in schedule:
        config.csv = resolve(schedule["csv"])
    for key in ("start", "cycle_minutes", "fields", "match_prefix"):
        if key in timing:
            setattr(config, key, timing[key])
    if "breaks" in timing:
        config.breaks = parse_breaks(timing["breaks"])
    if "rows_per_page" in table:
        config.rows_per_page = table["rows_per_page"]
    if "columns" in table:
        columns = []
        for item in table["columns"]:
            _take(item, "table.columns", {"key", "label", "weight"})
            if "key" not in item:
                raise VexCopyError("Each entry in [[table.columns]] needs a key.")
            columns.append(Column(item["key"], item.get("label", item["key"]),
                                  item.get("weight", 3)))
        config.columns = columns
    for key in ("regular", "bold", "oblique"):
        if key in fonts:
            setattr(config, f"font_{key}", resolve(fonts[key]))
    return config
