import unittest
from datetime import datetime, timedelta, timezone

from cloclo.dates import parse_date, to_rfc3339, to_rfc822

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


class ParseDateTest(unittest.TestCase):
    def test_iso(self):
        parsed = parse_date("2024-05-17T08:30:00+02:00")
        self.assertEqual(parsed.utcoffset(), timedelta(hours=2))
        self.assertEqual(to_rfc3339(parsed), "2024-05-17T06:30:00Z")

    def test_francais(self):
        self.assertEqual(to_rfc3339(parse_date("17 mai 2024")), "2024-05-17T00:00:00Z")
        self.assertEqual(
            to_rfc3339(parse_date("Publié le 1er août 2026 à 09h05")),
            "2026-08-01T09:05:00Z",
        )
        self.assertEqual(to_rfc3339(parse_date("3 déc. 2025")), "2025-12-03T00:00:00Z")

    def test_anglais_et_numerique(self):
        self.assertEqual(to_rfc3339(parse_date("May 17, 2024")), "2024-05-17T00:00:00Z")
        self.assertEqual(to_rfc3339(parse_date("17/05/2024")), "2024-05-17T00:00:00Z")
        self.assertEqual(
            to_rfc3339(parse_date("Fri, 17 May 2024 08:30:00 +0200")),
            "2024-05-17T06:30:00Z",
        )

    def test_horodatages(self):
        self.assertEqual(to_rfc3339(parse_date("1715930000")), "2024-05-17T07:13:20Z")
        self.assertEqual(
            to_rfc3339(parse_date("1715930000000")), "2024-05-17T07:13:20Z"
        )

    def test_relatif(self):
        self.assertEqual(
            to_rfc3339(parse_date("il y a 3 jours", now=NOW)), "2026-08-26T12:00:00Z"
        )
        self.assertEqual(
            to_rfc3339(parse_date("2 hours ago", now=NOW)), "2026-08-29T10:00:00Z"
        )
        self.assertEqual(to_rfc3339(parse_date("hier", now=NOW)), "2026-08-28T12:00:00Z")

    def test_absence_de_date(self):
        self.assertIsNone(parse_date(""))
        self.assertIsNone(parse_date(None))
        self.assertIsNone(parse_date("aucune date ici"))
        self.assertIsNone(parse_date("32/13/2024"))

    def test_formatage(self):
        self.assertEqual(
            to_rfc822(datetime(2024, 5, 17, tzinfo=timezone.utc)),
            "Fri, 17 May 2024 00:00:00 +0000",
        )
        self.assertIsNone(to_rfc822(None))


if __name__ == "__main__":
    unittest.main()
