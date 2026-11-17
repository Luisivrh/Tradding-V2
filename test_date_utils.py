import unittest
from datetime import datetime, timezone
from date_utils import parse_iso_datetime


class TestDateUtils(unittest.TestCase):
    def test_parse_iso_with_z_suffix(self):
        """Verifica que fechas ISO con 'Z' se parseen correctamente y no lancen ValueError ni TypeError al restar."""
        val = "2026-10-05T13:34:23.883409Z"
        dt = parse_iso_datetime(val)
        self.assertIsInstance(dt, datetime)
        self.assertIsNone(dt.tzinfo)
        # Debe poder restarse directamente con datetime.now() sin TypeError
        diff = datetime.now() - dt
        self.assertIsInstance(diff.days, int)

    def test_parse_iso_with_lowercase_z(self):
        val = "2026-10-05T13:34:23.883409z"
        dt = parse_iso_datetime(val)
        self.assertIsNone(dt.tzinfo)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 10)

    def test_parse_iso_with_offset(self):
        val = "2026-10-05T13:34:23.883409+00:00"
        dt = parse_iso_datetime(val)
        self.assertIsNone(dt.tzinfo)

    def test_parse_standard_string(self):
        val = "2026-10-12 11:49:09.399069"
        dt = parse_iso_datetime(val)
        self.assertIsNone(dt.tzinfo)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 10)
        self.assertEqual(dt.day, 12)
        self.assertEqual(dt.hour, 11)

    def test_parse_date_only(self):
        val = "2026-10-05"
        dt = parse_iso_datetime(val)
        self.assertIsNone(dt.tzinfo)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 10)
        self.assertEqual(dt.day, 5)

    def test_parse_datetime_instances(self):
        now_naive = datetime.now()
        dt_naive = parse_iso_datetime(now_naive)
        self.assertEqual(dt_naive, now_naive)

        now_utc = datetime.now(timezone.utc)
        dt_utc = parse_iso_datetime(now_utc)
        self.assertIsNone(dt_utc.tzinfo)
        # La diferencia entre ahora local y el UTC convertido a local debe ser casi 0 segundos
        diff = abs((datetime.now() - dt_utc).total_seconds())
        self.assertLess(diff, 2.0)

    def test_default_fallback(self):
        fallback = datetime(2026, 1, 1, 0, 0, 0)
        self.assertEqual(parse_iso_datetime(None, default=fallback), fallback)
        self.assertEqual(parse_iso_datetime("", default=fallback), fallback)
        self.assertEqual(parse_iso_datetime("not-a-date", default=fallback), fallback)

    def test_invalid_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_iso_datetime(None)
        with self.assertRaises(ValueError):
            parse_iso_datetime("")
        with self.assertRaises(ValueError):
            parse_iso_datetime("completely_invalid_date_string")


if __name__ == "__main__":
    unittest.main()
