"""Run with: .venv/bin/python -m unittest discover tests -v"""

import collections
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from compare import page_streams, pixel_report  # noqa: E402

from vexcopy import VexCopyError  # noqa: E402
from vexcopy.config import Config, load_config  # noqa: E402
from vexcopy.layout import render  # noqa: E402
from vexcopy.schedule import TEAM_KEYS, generate_pairings, pairing_rows, slot_times  # noqa: E402

REFERENCE = ROOT / "refernce.pdf"


def pdf_text(path):
    return subprocess.run(["pdftotext", "-layout", str(path), "-"],
                          capture_output=True, text=True, check=True).stdout


class ReferenceFidelity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "out.pdf"
        pdf, cls.warnings, cls.rows = render(load_config(ROOT / "examples/config.toml"))
        cls.path.write_bytes(pdf)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_no_warnings(self):
        self.assertEqual(self.warnings, [])

    def test_content_streams_are_byte_identical(self):
        reference, generated = page_streams(REFERENCE), page_streams(self.path)
        self.assertEqual(len(reference), 10)
        self.assertEqual(reference, generated)

    def test_zero_pixel_difference_at_300_dpi(self):
        for page, count, _big, bbox in pixel_report(REFERENCE, self.path):
            self.assertEqual(count, 0, f"page {page} differs inside {bbox}")

    def test_extracted_text_matches(self):
        self.assertEqual(pdf_text(REFERENCE), pdf_text(self.path))

    def test_line_count_per_page(self):
        for stream in page_streams(self.path):
            self.assertEqual(len(re.findall(rb" l S", stream)), 649)

    def test_fonts_are_listed_the_same(self):
        def fonts(path):
            output = subprocess.run(["pdffonts", str(path)], capture_output=True, text=True,
                                    check=True).stdout
            return [line.split()[:6] for line in output.splitlines()[2:]]
        self.assertEqual(fonts(REFERENCE), fonts(self.path))

    def test_document_properties_match(self):
        def info(path):
            output = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True,
                                    check=True).stdout
            return dict(line.split(":", 1) for line in output.splitlines() if ":" in line)
        reference, generated = info(REFERENCE), info(self.path)
        for key in ("Title", "Author", "Creator", "Pages", "Page size"):
            self.assertEqual(reference[key], generated[key], key)


class Generator(unittest.TestCase):
    def check(self, teams, matches, min_gap, seed=7):
        rows = pairing_rows(generate_pairings(teams, matches, seed, min_gap))
        self.assertEqual(len(rows), matches)
        last = {}
        for index, row in enumerate(rows):
            playing = [row[k] for k in TEAM_KEYS]
            self.assertEqual(len(set(playing)), 4, "a team appears twice in one match")
            for team in playing:
                if team in last:
                    self.assertGreaterEqual(index - last[team], min_gap)
                last[team] = index
        return rows

    def test_matches_the_reference_pattern(self):
        teams = [str(i) for i in range(1, 25)]
        rows = self.check(teams, 420, 3)
        plays = collections.Counter(row[k] for row in rows for k in TEAM_KEYS)
        self.assertEqual(set(plays.values()), {70})
        for start in range(0, 420, 6):
            block = {row[k] for row in rows[start:start + 6] for k in TEAM_KEYS}
            self.assertEqual(len(block), 24)
        red = collections.Counter(row[k] for row in rows for k in TEAM_KEYS[:2])
        self.assertLessEqual(max(red.values()) - min(red.values()), 2)

    def test_balance_is_no_worse_than_the_reference(self):
        teams = [str(i) for i in range(1, 25)]
        rows = self.check(teams, 420, 3)
        partners = collections.Counter()
        for row in rows:
            partners[frozenset((row["red1"], row["red2"]))] += 1
            partners[frozenset((row["blue1"], row["blue2"]))] += 1
        self.assertLessEqual(max(partners.values()), 8)  # the reference's maximum

    def test_same_seed_same_schedule(self):
        teams = [str(i) for i in range(1, 17)]
        self.assertEqual(generate_pairings(teams, 60, "abc"), generate_pairings(teams, 60, "abc"))
        self.assertNotEqual(generate_pairings(teams, 60, 1), generate_pairings(teams, 60, 2))

    def test_team_count_not_divisible_by_four_shares_sit_outs(self):
        teams = [str(i) for i in range(1, 31)]
        rows = self.check(teams, 210, 3)
        plays = collections.Counter(row[k] for row in rows for k in TEAM_KEYS)
        self.assertLessEqual(max(plays.values()) - min(plays.values()), 1)

    def test_every_team_count_gets_a_valid_schedule(self):
        for count in range(4, 61):
            teams = [str(i) for i in range(1, count + 1)]
            per_round = count // 4
            gap = min(3, max(1, per_round - 1))
            rows = self.check(teams, per_round * 12, gap, seed=count)
            plays = collections.Counter(row[k] for row in rows for k in TEAM_KEYS)
            self.assertEqual(len(plays), count)
            self.assertLessEqual(max(plays.values()) - min(plays.values()), 1, f"{count} teams")

    def test_tightest_possible_gap_works(self):
        self.check([str(i) for i in range(1, 9)], 20, 2)
        self.check([str(i) for i in range(1, 17)], 60, 4)

    def test_impossible_gap_is_a_clear_error(self):
        with self.assertRaisesRegex(VexCopyError, "impossible"):
            generate_pairings([str(i) for i in range(1, 9)], 10, 1, 3)

    def test_too_few_teams(self):
        with self.assertRaisesRegex(VexCopyError, "needs 4 teams"):
            render(Config(teams=3))


class Options(unittest.TestCase):
    def test_breaks_move_matches_to_the_end(self):
        start = datetime(2026, 10, 31, 11, 58)
        times = slot_times(4, start, 1, [(datetime(2026, 10, 31, 12, 0), 60)])
        self.assertEqual([t.strftime("%H:%M") for t in times],
                         ["11:58", "11:59", "13:00", "13:01"])

    def test_several_fields_rotate(self):
        config = Config(teams=8, matches=4, fields=["A", "B"], start="2026-10-31 09:00")
        _pdf, _warnings, rows = render(config)
        self.assertEqual([r["field"] for r in rows], ["A", "B", "A", "B"])

    def test_alphanumeric_teams_and_partial_last_page(self):
        teams = [f"{i}A" for i in range(100, 124)]
        config = Config(teams=teams, matches=50, start="2026-10-31 09:00", timestamp="now")
        pdf, warnings, rows = render(config)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.pdf"
            path.write_bytes(pdf)
            text = pdf_text(path)
            lines = [len(re.findall(rb" l S", s)) for s in page_streams(path)]
        self.assertEqual(warnings, [])
        self.assertIn("Page 2 of 2", text)
        self.assertIn("100A", text)
        self.assertEqual(lines, [649, 2 + 15 + 1 + 8 * 15 + 1])

    def test_long_text_warns(self):
        config = Config(teams=8, matches=2, event_name="Very Long Event Name " * 10,
                        fields=["A very long field name that cannot possibly fit in its cell "
                                "no matter how the table is arranged on the printed page"],
                        start="2026-10-31 09:00")
        _pdf, warnings, _rows = render(config)
        self.assertEqual(len(warnings), 2)

    def test_unsupported_character_names_the_character(self):
        with self.assertRaisesRegex(VexCopyError, "日"):
            render(Config(teams=8, matches=2, event_name="日本", start="2026-10-31 09:00"))

    def test_accented_text_prints(self):
        pdf, _w, _r = render(Config(teams=8, matches=2, event_name="Coupe de Québec – Fiesta",
                                    start="2026-10-31 09:00"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.pdf"
            path.write_bytes(pdf)
            self.assertIn("Québec", pdf_text(path))

    def test_csv_with_only_team_columns_fills_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.csv"
            path.write_text("Red 1,Red 2,Blue 1,Blue 2\n1,2,3,4\n5,6,7,8\n")
            config = Config(csv=str(path), start="2026-10-31 09:00", cycle_minutes=5)
            _pdf, _w, rows = render(config)
        self.assertEqual(rows[1]["match"], "Q2")
        self.assertEqual(rows[1]["time"], "Sat 9:05 AM")

    def test_csv_missing_team_column_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.csv"
            path.write_text("Red 1,Red 2,Blue 1\n1,2,3\n")
            with self.assertRaisesRegex(VexCopyError, "missing column.*Blue 2"):
                render(Config(csv=str(path), start="2026-10-31 09:00"))

    def test_unknown_setting_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text('titel = "oops"\n')
            with self.assertRaisesRegex(VexCopyError, "titel"):
                load_config(path)

    def test_no_logo(self):
        pdf, _w, _r = render(Config(teams=8, matches=2, logo="", start="2026-10-31 09:00"))
        self.assertNotIn(b"/Subtype /Image", pdf)


if __name__ == "__main__":
    unittest.main()
