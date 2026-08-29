import unittest
from pathlib import Path

from cloclo.article import readable

FIXTURES = Path(__file__).parent / "fixtures"


class ReadableTest(unittest.TestCase):
    def setUp(self):
        html = (FIXTURES / "article.html").read_text(encoding="utf-8")
        self.page = readable(html, "https://carnet.example/article")

    def test_metadonnees(self):
        self.assertEqual(self.page["title"], "Relire Les Mots et les Choses")
        self.assertEqual(self.page["byline"], "Claire Menou")
        self.assertEqual(self.page["date"].year, 2026)
        self.assertEqual(self.page["image"], "https://carnet.example/img/foucault.jpg")

    def test_corps_extrait(self):
        self.assertIn("On ouvre", self.page["text"])
        self.assertIn("<h2>Un livre de 1966", self.page["html"])
        self.assertIn("<em>Les Mots et les Choses</em>", self.page["html"])

    def test_mobilier_de_page_ecarte(self):
        for indesirable in ("Accueil", "À lire aussi", "© Le Carnet"):
            self.assertNotIn(indesirable, self.page["text"])

    def test_html_assaini(self):
        page = readable(
            "<article><p>Bonjour</p><script>alert(1)</script>"
            "<p onclick='x()'>Suite du texte, assez longue pour peser dans le score, "
            "avec virgules, points.</p></article>",
            "https://ex.fr/a",
        )
        self.assertNotIn("script", page["html"])
        self.assertNotIn("onclick", page["html"])

    def test_liens_absolus(self):
        page = readable(
            "<article><p>Un texte de longueur raisonnable, avec des virgules, et "
            "<a href='/interne'>un lien relatif</a> à résoudre.</p></article>",
            "https://ex.fr/rubrique/page",
        )
        self.assertIn('href="https://ex.fr/interne"', page["html"])

    def test_page_sans_corps(self):
        page = readable("<html><body></body></html>", "https://ex.fr")
        self.assertEqual(page["text"], "")


if __name__ == "__main__":
    unittest.main()
