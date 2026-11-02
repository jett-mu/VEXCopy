"""Match rows: the seeded schedule generator, CSV loading and time formatting."""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta

from . import VexCopyError

TEAM_KEYS = ("red1", "red2", "blue1", "blue2")
PARTNER_WEIGHT = 3
OPPONENT_WEIGHT = 1
MAX_ROUND_ATTEMPTS = 200
SWAPS_PER_TEAM = 40


def parse_datetime(value) -> datetime:
    """Accept a TOML datetime or text like '2026-10-31 9:00 AM'."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M",
                    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %I:%M %p"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            pass
    raise VexCopyError(
        f"Could not read the date and time {text!r}. Use a form like 2026-10-31 09:00."
    )


def format_time(moment: datetime) -> str:
    """Format like the reference's time column: 'Sat 9:00 AM'."""
    return moment.strftime("%a %-I:%M %p")


def format_timestamp(moment: datetime) -> str:
    """Format like the reference's footer: 'October 31, 2026 2:34 PM'."""
    return moment.strftime("%B %-d, %Y %-I:%M %p")


def team_list(value) -> list[str]:
    """Turn a team count, a list, or comma-separated text into team numbers."""
    if isinstance(value, bool):
        raise VexCopyError("teams must be a count or a list of team numbers.")
    if isinstance(value, int):
        teams = [str(i) for i in range(1, value + 1)]
    elif isinstance(value, str):
        items = [t.strip() for t in value.replace("\n", ",").split(",") if t.strip()]
        if len(items) == 1 and items[0].isdigit():
            teams = [str(i) for i in range(1, int(items[0]) + 1)]
        else:
            teams = items
    else:
        teams = [str(t).strip() for t in value]
    if len(teams) < 4:
        raise VexCopyError(f"A match needs 4 teams, but only {len(teams)} were given.")
    repeated = sorted({t for t in teams if teams.count(t) > 1})
    if repeated:
        raise VexCopyError(f"Duplicate team numbers: {', '.join(repeated)}.")
    return teams


def generate_pairings(teams: list[str], matches: int, seed, min_gap: int | None = None):
    """Generate balanced 2v2 matches, returned as (red1, red2, blue1, blue2).

    Like the reference, the schedule runs in rounds where each team plays once.
    If the team count isn't divisible by 4, teams take turns sitting out.
    Each round is improved by swapping teams to reduce repeat partners and
    opponents, while keeping every team at least min_gap matches apart.
    """
    if matches < 1:
        raise VexCopyError("The number of matches must be at least 1.")
    count = len(teams)
    per_round = count // 4
    if min_gap is None:
        # A gap equal to the round length would force the same groups every round.
        min_gap = min(3, max(1, per_round - 1))
    if min_gap < 1:
        raise VexCopyError("The minimum gap between matches must be at least 1.")
    if count % 4 == 0 and min_gap > per_round:
        raise VexCopyError(
            f"A minimum gap of {min_gap} is impossible with {count} teams: every team plays "
            f"once per {per_round} matches. Use a gap of {per_round} or less."
        )

    rng = random.Random(seed)
    partners = [[0] * count for _ in range(count)]
    opponents = [[0] * count for _ in range(count)]
    red_counts = [0] * count
    plays = [0] * count
    last_played = [-10 ** 9] * count
    result: list[tuple[str, str, str, str]] = []

    def match_cost(group):
        a, b, c, d = group
        return (
            PARTNER_WEIGHT * (partners[a][b] ** 2 + partners[c][d] ** 2)
            + OPPONENT_WEIGHT * (opponents[a][c] ** 2 + opponents[a][d] ** 2
                                 + opponents[b][c] ** 2 + opponents[b][d] ** 2)
        )

    while len(result) < matches:
        for _ in range(MAX_ROUND_ATTEMPTS):
            ordered = _build_round(count, per_round, plays, rng, match_cost, last_played,
                                   len(result), min_gap)
            if ordered is not None:
                break
        else:
            raise VexCopyError(
                f"Couldn't build a schedule with a minimum gap of {min_gap} for {count} teams. "
                "Try a smaller gap."
            )
        for group in ordered:
            if len(result) == matches:
                break
            a, b, c, d = group
            if red_counts[a] + red_counts[b] > red_counts[c] + red_counts[d] or (
                red_counts[a] + red_counts[b] == red_counts[c] + red_counts[d]
                and rng.random() < 0.5
            ):
                a, b, c, d = c, d, a, b
            if rng.random() < 0.5:
                a, b = b, a
            if rng.random() < 0.5:
                c, d = d, c
            for x, y in ((a, b), (c, d)):
                partners[x][y] += 1
                partners[y][x] += 1
            for x in (a, b):
                for y in (c, d):
                    opponents[x][y] += 1
                    opponents[y][x] += 1
            for team in (a, b):
                red_counts[team] += 1
            for team in group:
                plays[team] += 1
                last_played[team] = len(result)
            result.append((teams[a], teams[b], teams[c], teams[d]))
    return result


def _build_round(count, per_round, plays, rng, match_cost, last_played, base, min_gap):
    """Build one round as an ordered list of matches, or None if the gap can't be met.

    Each team has an earliest slot it may play in. Assigning teams in that order
    finds a valid round whenever one exists, and swaps only ever keep it valid.
    """
    order = list(range(count))
    rng.shuffle(order)
    # Teams who've played the least go first, so sit-outs even out over time.
    order.sort(key=lambda team: plays[team])
    playing = order[:per_round * 4]

    earliest = {team: max(0, last_played[team] + min_gap - base) for team in playing}
    rng.shuffle(playing)
    playing.sort(key=lambda team: earliest[team])
    groups = [playing[i:i + 4] for i in range(0, len(playing), 4)]
    if any(earliest[team] > slot for slot, group in enumerate(groups) for team in group):
        return None

    for _ in range(SWAPS_PER_TEAM * count):
        g1, g2 = rng.randrange(per_round), rng.randrange(per_round)
        i, j = rng.randrange(4), rng.randrange(4)
        if g1 == g2 and i == j:
            continue
        if earliest[groups[g1][i]] > g2 or earliest[groups[g2][j]] > g1:
            continue
        before = match_cost(groups[g1]) + (match_cost(groups[g2]) if g1 != g2 else 0)
        groups[g1][i], groups[g2][j] = groups[g2][j], groups[g1][i]
        after = match_cost(groups[g1]) + (match_cost(groups[g2]) if g1 != g2 else 0)
        if after > before:
            groups[g1][i], groups[g2][j] = groups[g2][j], groups[g1][i]
    return groups


def slot_times(count: int, start: datetime, cycle_minutes: float, breaks) -> list[datetime]:
    """List match start times, moving any that fall inside a break to its end."""
    if cycle_minutes <= 0:
        raise VexCopyError("The cycle time must be greater than 0 minutes.")
    windows = sorted((b_start, b_start + timedelta(minutes=minutes)) for b_start, minutes in breaks)
    times = []
    current = start
    cycle = timedelta(minutes=cycle_minutes)
    for _ in range(count):
        for begin, end in windows:
            if begin <= current < end:
                current = end
        times.append(current)
        current += cycle
    return times


def normalize_header(text: str) -> str:
    return "".join(text.lower().split()).replace("_", "")


def load_csv(path: str, columns: list) -> list[dict]:
    """Read rows from a CSV, matching headers to column keys or labels."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            headers = reader.fieldnames or []
    except FileNotFoundError:
        raise VexCopyError(f"CSV file not found: {path}") from None
    except UnicodeDecodeError:
        raise VexCopyError(f"CSV file {path} isn't UTF-8 text. Re-save it as UTF-8.") from None
    except csv.Error as exc:
        raise VexCopyError(f"Could not read CSV file {path}: {exc}") from None

    lookup = {normalize_header(h): h for h in headers if h}
    source = {}
    for column in columns:
        for name in (column.key, column.label):
            if normalize_header(name) in lookup:
                source[column.key] = lookup[normalize_header(name)]
                break

    derived = {"match", "field", "time"}
    missing = [c.label for c in columns if c.key not in source and c.key not in derived]
    if missing:
        raise VexCopyError(
            f"CSV file {path} is missing column(s): {', '.join(missing)}. "
            f"Found: {', '.join(headers) or 'no header row'}."
        )
    loaded = []
    for number, row in enumerate(rows, start=2):
        item = {}
        for key, header in source.items():
            value = row.get(header)
            if value is None:
                raise VexCopyError(f"CSV file {path}, line {number}: too few values.")
            item[key] = value.strip()
        loaded.append(item)
    if not loaded:
        raise VexCopyError(f"CSV file {path} has no match rows.")
    return loaded


def fill_rows(rows: list[dict], start, cycle_minutes, breaks, fields, prefix) -> list[dict]:
    """Add any match number, field and time values the rows don't already have."""
    if not fields:
        raise VexCopyError("At least one field name is required.")
    times = slot_times(len(rows), start, cycle_minutes, breaks)
    for index, row in enumerate(rows):
        row.setdefault("match", f"{prefix}{index + 1}")
        row.setdefault("field", fields[index % len(fields)])
        row.setdefault("time", format_time(times[index]))
    return rows


def pairing_rows(pairings) -> list[dict]:
    return [dict(zip(TEAM_KEYS, pairing)) for pairing in pairings]
