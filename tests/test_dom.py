import unittest

from cloclo.dom import matches, parse, select


class ParseTest(unittest.TestCase):
    def test_ferme_les_balises_implicites(self):
        doc = parse("<ul><li>un<li>deux</ul><p>a<p>b")
        self.assertEqual(len(select(doc, "li")), 2)
        self.assertEqual([n.text() for n in select(doc, "p")], ["a", "b"])

    def test_ignore_les_fermetures_orphelines(self):
        doc = parse("<div>texte</span></div>")
        self.assertEqual(doc.text(), "texte")

    def test_decode_les_entites(self):
        doc = parse("<p>Caf&eacute; &amp; th&#233;</p>")
        self.assertEqual(doc.text(), "Café & thé")

    def test_balises_auto_fermantes(self):
        doc = parse('<div><img src="a.png"><br>suite</div>')
        self.assertEqual(len(select(doc, "img")), 1)
        self.assertEqual(doc.find("div").text(), "suite")

    def test_texte_ignore_les_scripts(self):
        doc = parse("<div><script>var x = 1;</script>visible</div>")
        self.assertEqual(doc.text(), "visible")
        self.assertIn("var x", doc.find("script").raw_text())

    def test_block_text_conserve_les_paragraphes(self):
        doc = parse("<div><p>un</p><p>deux</p></div>")
        self.assertEqual(doc.block_text(), "un\n\ndeux")

    def test_serialisation_echappee(self):
        doc = parse('<p class="x">a < b & c</p>')
        self.assertEqual(doc.find("p").outer_html(), '<p class="x">a &lt; b &amp; c</p>')


class SelectorTest(unittest.TestCase):
    def setUp(self):
        self.doc = parse(
            '<div id="main" class="wrap"><ul class="l"><li class="a b"><a href="/1" rel="tag">un</a></li>'
            '<li class="a"><a href="/2">deux</a></li></ul>'
            '<section><p class="a">trois</p></section></div>'
        )

    def test_balise_classe_id(self):
        self.assertEqual(len(select(self.doc, "li")), 2)
        self.assertEqual(len(select(self.doc, ".a")), 3)
        self.assertEqual(len(select(self.doc, "#main")), 1)
        self.assertEqual(len(select(self.doc, "li.a.b")), 1)

    def test_descendant_et_enfant(self):
        self.assertEqual(len(select(self.doc, "div a")), 2)
        self.assertEqual(len(select(self.doc, "ul > li")), 2)
        self.assertEqual(len(select(self.doc, "div > a")), 0)

    def test_freres(self):
        self.assertEqual(len(select(self.doc, "li + li")), 1)
        self.assertEqual(len(select(self.doc, "ul ~ section")), 1)

    def test_attributs(self):
        self.assertEqual(len(select(self.doc, "a[href]")), 2)
        self.assertEqual(len(select(self.doc, '[href="/2"]')), 1)
        self.assertEqual(len(select(self.doc, '[href^="/"]')), 2)
        self.assertEqual(len(select(self.doc, "[rel=tag]")), 1)

    def test_groupes_et_pseudos(self):
        self.assertEqual(len(select(self.doc, "p, a")), 3)
        self.assertEqual(len(select(self.doc, "li:first-child")), 1)
        self.assertEqual(len(select(self.doc, "li:nth-of-type(2)")), 1)
        self.assertEqual(len(select(self.doc, "ul:has(a)")), 1)

    def test_matches(self):
        self.assertTrue(matches(select(self.doc, "li")[0], "li.a"))
        self.assertFalse(matches(select(self.doc, "li")[1], "li.b"))

    def test_selecteur_invalide(self):
        with self.assertRaises(ValueError):
            select(self.doc, "!!!")


if __name__ == "__main__":
    unittest.main()
