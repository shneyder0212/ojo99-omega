import os
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("COLLECTOR_ENABLED", "false")

from app import main


ANGUILLA_HTML = """
<html><body>
  <table><tbody>
    <tr>
      <td>miércoles 2 de septiembre de 2026</td>
      <td>10:00 a. m.</td>
      <td class="nums"><span>26</span><span>80</span><span>79</span></td>
    </tr>
    <tr>
      <td>01/09/2026</td>
      <td>10:00 AM</td>
      <td class="nums"><span>48</span><span>45</span><span>55</span></td>
    </tr>
  </tbody></table>
</body></html>
"""

KINO_HTML = """
<html><body><table><tbody>
  <tr>
    <td>jueves 11 de junio de 2026</td><td>8:55 PM</td>
    <td>01 02 09 14 17 18 20 23 31 32 34 38 40 42 46 53 58 60 64 77</td>
  </tr>
  <tr>
    <td>10/06/2026</td><td>8:55 p. m.</td>
    <td>03 16 20 23 30 34 37 39 40 41 46 50 51 52 57 60 63 70 77</td>
  </tr>
</tbody></table></body></html>
"""


class AnguillaParserTests(unittest.TestCase):
    def test_parser_accepts_long_numeric_dates_and_time_variants(self):
        rows = main.extract_anguilla_manana_history(ANGUILLA_HTML)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "Anguilla Mañana")
        self.assertEqual(rows[0][2], [26, 80, 79])
        self.assertEqual(rows[1][2], [48, 45, 55])

    def test_history_uses_one_direct_request(self):
        source = type("Source", (), {"url": "https://example.test/resultados/"})()
        parsed = main.extract_anguilla_manana_history(ANGUILLA_HTML)
        with patch.object(main, "fetch_source", return_value=parsed) as fetch:
            result = main.fetch_anguilla_manana_history(source, date(2026, 9, 2))
        self.assertEqual(result["rows"][0][2], [26, 80, 79])
        fetch.assert_called_once_with(
            source,
            "https://example.test/resultados/anguilla-manana/?date=02-09-2026",
            expected_game="Anguilla Mañana",
        )

    def test_recent_sync_uses_yesterday_before_draw_and_ingests_all_rows(self):
        source = type("Source", (), {
            "key": "primary",
            "url": "https://example.test/resultados/",
            "pause_until": None,
        })()
        parsed = main.extract_anguilla_manana_history(ANGUILLA_HTML)
        before_draw = datetime(2026, 9, 3, 9, 0, tzinfo=main.DR_TZ)
        with patch.object(main, "fetch_source", return_value=parsed) as fetch:
            result = main.sync_anguilla_manana_recent(source, now=before_draw)
        self.assertEqual(result["target_date"], "2026-09-02")
        self.assertEqual(result["rows"], 2)
        fetch.assert_called_once_with(
            source,
            "https://example.test/resultados/anguilla-manana/?date=02-09-2026",
            expected_game="Anguilla Mañana",
        )


class LayerMapTests(unittest.TestCase):
    def test_layer_map_is_complete_and_ordered(self):
        self.assertEqual(len(main.SYSTEM_LAYERS), 17)
        self.assertEqual([code for code, _ in main.SYSTEM_LAYERS], [f"C{i:02d}" for i in range(17)])
        self.assertEqual(dict(main.SYSTEM_LAYERS)["C00"], "ESCUDO V1")
        self.assertEqual(dict(main.SYSTEM_LAYERS)["C16"], "Operación 3 de 3")

    def test_c16_is_deterministic_and_blocks_publication_without_300_draws(self):
        draws = []
        for i in range(100):
            dt = datetime(2026, 1, 1, tzinfo=main.timezone.utc) + main.timedelta(days=i)
            nums = [i % 100, (i * 3 + 7) % 100, (i * 7 + 11) % 100]
            draws.append(SimpleNamespace(draw_time=dt, numbers_json=main.json.dumps(nums)))
        first = main.build_operation_3of3("Anguilla Mañana", draws, max_tests=40)
        main._c16_cache.clear()
        second = main.build_operation_3of3("Anguilla Mañana", draws, max_tests=40)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "PREPARANDO_DATOS")
        self.assertEqual(first["top20"], [])
        self.assertFalse(first["random_numbers"])
        self.assertEqual(len(first["strategies"]), 4)


class SuperKinoParserTests(unittest.TestCase):
    def test_kino_accepts_long_date_and_only_complete_20_number_rows(self):
        rows = main.extract_super_kino_history(KINO_HTML)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Super Kino TV")
        self.assertEqual(len(rows[0][2]), 20)
        self.assertEqual(rows[0][2][:3], [1, 2, 9])

    def test_kino_history_uses_one_direct_request(self):
        source = type("Source", (), {"url": "https://example.test/resultados/"})()
        parsed = main.extract_super_kino_history(KINO_HTML)
        with patch.object(main, "fetch_source", return_value=parsed) as fetch:
            result = main.fetch_super_kino_history(source, date(2026, 6, 11))
        self.assertEqual(len(result["rows"]), 1)
        fetch.assert_called_once_with(
            source,
            "https://example.test/resultados/super-kino-tv/?date=11-06-2026",
            expected_game="Super Kino TV",
        )


if __name__ == "__main__":
    unittest.main()
