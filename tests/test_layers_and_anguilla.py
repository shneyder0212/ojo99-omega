import os
import unittest
from datetime import date
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


class LayerMapTests(unittest.TestCase):
    def test_layer_map_is_complete_and_ordered(self):
        self.assertEqual(len(main.SYSTEM_LAYERS), 17)
        self.assertEqual([code for code, _ in main.SYSTEM_LAYERS], [f"C{i:02d}" for i in range(17)])
        self.assertEqual(dict(main.SYSTEM_LAYERS)["C00"], "ESCUDO V1")
        self.assertEqual(dict(main.SYSTEM_LAYERS)["C16"], "Operación 3 de 3")


if __name__ == "__main__":
    unittest.main()
