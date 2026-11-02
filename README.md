# VEX Match List Generator

Generates "Qualification Match List" PDFs in the same layout and fonts as VEX Tournament Manager's printout. With the example settings it reproduces `refernce.pdf` exactly.

Requires macOS, because it uses the system's Helvetica. The font isn't included in this project.

## Setup

```sh
brew install python@3.14 python-tk@3.14 poppler
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`poppler` is only needed to run the tests.

## Using the window

```sh
.venv/bin/python vexcopy.py
```

Fill in the form and click **Generate PDF…**. The finished PDF opens in Preview. **Load Config…** fills the form from a config file.

## Using the command line

```sh
# Reproduce the reference
.venv/bin/python vexcopy.py examples/config.toml -o out.pdf

# Generate a new schedule
.venv/bin/python vexcopy.py -o regional.pdf \
  --event "Spring Regional" --teams 32 --rounds 8 --seed 42 \
  --start "2027-03-13 08:30" --cycle 7 \
  --field "Field A" --field "Field B" \
  --break "2027-03-13 12:00,60"

# Use a config file, overriding one value
.venv/bin/python vexcopy.py my_event.toml --event "Finals" -o finals.pdf
```

Run `vexcopy.py --help` for every option. `examples/config.toml` documents the config file.

## Settings

| Setting | Config file | Flag | Window |
|---|---|---|---|
| Title, event name | `title`, `event_name` | `--title`, `--event` | yes |
| Logo | `logo` (`""` for none) | `--logo`, `--no-logo` | yes |
| Footer date | `timestamp` | `--timestamp` | yes |
| Teams | `schedule.teams` | `--teams` | yes |
| Match count | `schedule.matches` or `rounds` | `--matches`, `--rounds` | matches only |
| Random seed | `schedule.seed` | `--seed` | yes |
| Minimum gap | `schedule.min_gap` | `--min-gap` | yes |
| CSV of matches | `schedule.csv` | `--csv`, `--generate` | yes |
| First match, cycle time | `timing.start`, `cycle_minutes` | `--start`, `--cycle` | yes |
| Fields | `timing.fields` | `--field` | yes |
| Breaks | `[[timing.breaks]]` | `--break` | yes |
| Match prefix | `timing.match_prefix` | `--match-prefix` | yes |
| Rows per page | `table.rows_per_page` | `--rows-per-page` | yes |
| PDF properties | `[pdf]` | `--pdf-title`, `--author`, `--creator` | yes |
| Column labels, order, widths | `[[table.columns]]` | no | no |
| Fonts | `[fonts]` | `--font-file`, `--font-regular`, ... | no |

## Match data

**Generated:** each round, every team plays once (teams take turns sitting out if the count isn't divisible by 4). Teams are swapped to minimize repeat partners and opponents and to balance red and blue, while keeping teams at least the minimum gap apart. The same seed always gives the same schedule. It follows the reference's pattern, not its exact pairings.

**From CSV:** needs `Red 1`, `Red 2`, `Blue 1` and `Blue 2` columns. `Match`, `Field` and `Time` are optional; any that are missing are filled in from the timing settings.

## Limits

- Text must fit the Mac Roman character set (English and Western European). Anything else stops with an error naming the character.
- Text that's too long for its cell is cut off, and a title or event name can run under the logo. Both print a warning.
- Other fonts work through `--font-file` or `[fonts]`, but must be TrueType (`.ttf`, `.ttc`), and the result won't match the reference exactly.
- The reference is exactly 10 full pages, so a short last page was never observed. The footer stays in place and the table simply ends, which is an assumption about Tournament Manager.

## How it matches the reference

Measured from the reference, and used in `vexcopy/layout.py` and `vexcopy/pdfwriter.py`:

- Layout uses a 2550×3300 logical page mapped into the printable area with different x and y scales (0.22588 and 0.2224), so text is about 1.6% wider than normal.
- Cell text is centred with `left + floor((width − round(textWidth)) / 2)`.
- Glyphs and kerning come from the system's `Helvetica.ttc`, faces 0, 1 and 2. The event name also has an 11° skew.
- `fi` and `fl` use ligature glyphs tagged with `/ActualText`.
- Embedded fonts keep the full font's bounding box. Poppler uses it to size glyphs, so a tighter one shifts the text.
- One kerning value is overridden: `T`+`o` in Oblique is −271 units in the reference, but −227 in both the font's table and CoreText. See `KERN_OVERRIDES`.

## Tests

```sh
.venv/bin/python -m unittest discover tests
.venv/bin/python tests/compare.py refernce.pdf out.pdf   # detailed comparison of any two PDFs
```

The tests require the reference's content streams to be byte-identical, and zero differing pixels at 300 dpi. A macOS update that changes Helvetica could make these fail.
