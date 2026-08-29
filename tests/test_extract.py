import unittest
from pathlib import Path

from cloclo.dom import parse
from cloclo.extract import (
    Options,
    clean_url,
    discover_feeds,
    extract,
    item_is_free,
    page_is_free,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class UrlTest(unittest.TestCase):
    def test_absolutise_et_nettoie(self):
        self.assertEqual(
            clean_url("/a?utm_source=x&p=1#frag", "https://ex.fr/blog/"),
            "https://ex.fr/a?p=1",
        )

    def test_rejette_les_liens_non_navigables(self):
        for href in ("#", "javascript:void(0)", "mailto:a@b.fr", "", None):
            self.assertEqual(clean_url(href, "https://ex.fr"), "")


class BlogTest(unittest.TestCase):
    def setUp(self):
        self.result = extract(load("blog.html"), "https://carnet.example/")

    def test_detecte_la_liste_des_billets(self):
        self.assertEqual(self.result.strategy, "heuristic")
        self.assertEqual(len(self.result.items), 3)

    def test_ignore_navigation_pied_de_page_et_colonne_laterale(self):
        liens = [item.link for item in self.result.items]
        self.assertTrue(all("/2026/" in lien for lien in liens))
        self.assertNotIn("https://carnet.example/contact", liens)
        self.assertFalse(any("vieux-billet" in lien for lien in liens))

    def test_champs_de_la_premiere_entree(self):
        item = self.result.items[0]
        self.assertEqual(item.title, "Relire Les Mots et les Choses")
        self.assertEqual(
            item.link, "https://carnet.example/2026/08/relire-les-mots-et-les-choses/"
        )
        self.assertEqual(item.date.year, 2026)
        self.assertEqual(item.author, "Claire Menou")
        self.assertIn("archéologie des savoirs", item.summary)
        self.assertEqual(item.categories, ["philosophie"])
        self.assertTrue(item.image.endswith("foucault.jpg"))

    def test_ordre_du_document_conserve(self):
        self.assertEqual(
            [item.title for item in self.result.items],
            [
                "Relire Les Mots et les Choses",
                "Carnet de vacances en Cévennes",
                "Pourquoi je quitte les réseaux",
            ],
        )

    def test_selecteur_propose_reutilisable(self):
        selecteur = self.result.selectors["item"]
        force = extract(
            load("blog.html"), "https://carnet.example/", Options(item=selecteur)
        )
        self.assertEqual(force.strategy, "selectors")
        self.assertEqual(len(force.items), 3)

    def test_flux_deja_publies(self):
        feeds = discover_feeds(parse(load("blog.html")), "https://carnet.example/")
        self.assertIn("https://carnet.example/feed/", [f["url"] for f in feeds])

    def test_limite(self):
        court = extract(load("blog.html"), "https://carnet.example/", Options(limit=2))
        self.assertEqual(len(court.items), 2)


class JsonLdTest(unittest.TestCase):
    def test_lit_les_donnees_structurees(self):
        result = extract(load("jsonld.html"), "https://agence.example/actualites")
        self.assertEqual(result.strategy, "jsonld")
        self.assertEqual(
            [item.title for item in result.items],
            ["Ouverture de la consultation publique", "Rapport annuel 2025"],
        )
        premier = result.items[0]
        self.assertEqual(premier.author, "Service presse")
        self.assertEqual(premier.categories, ["Consultations"])
        self.assertEqual(premier.date.day, 25)

    def test_entree_payante_exclue_par_defaut(self):
        result = extract(load("jsonld.html"), "https://agence.example/actualites")
        self.assertNotIn(
            "Note de conjoncture réservée", [item.title for item in result.items]
        )

    def test_entree_payante_conservee_sur_demande(self):
        result = extract(
            load("jsonld.html"),
            "https://agence.example/actualites",
            Options(include_paid=True),
        )
        self.assertEqual(len(result.items), 3)
        self.assertFalse(result.items[2].free)


class ListeSimpleTest(unittest.TestCase):
    def test_liste_de_liens_datee(self):
        result = extract(load("liste.html"), "https://minist.example/presse")
        self.assertEqual(len(result.items), 4)
        self.assertEqual(result.items[0].title, "Présentation du budget 2027")
        self.assertEqual(result.items[0].date.month, 8)

    def test_pas_de_resume_redondant_avec_le_titre(self):
        result = extract(load("liste.html"), "https://minist.example/presse")
        self.assertEqual(result.items[0].summary, "")


class PaywallTest(unittest.TestCase):
    def test_filtre_les_entrees_reservees(self):
        result = extract(load("paywall.html"), "https://quotidien.example/")
        titres = [item.title for item in result.items]
        self.assertEqual(len(titres), 2)
        self.assertIn("L'inflation ralentit en août", titres)
        self.assertNotIn("Enquête sur les marges de la distribution", titres)

    def test_marque_sans_filtrer_avec_include_paid(self):
        result = extract(
            load("paywall.html"), "https://quotidien.example/", Options(include_paid=True)
        )
        self.assertEqual([item.free for item in result.items], [True, False, True, False])

    def test_signaux_de_page(self):
        self.assertTrue(page_is_free(parse("<html><body><p>libre</p></body></html>")))
        self.assertFalse(
            page_is_free(
                parse('<meta property="article:content_tier" content="locked">')
            )
        )
        self.assertFalse(
            page_is_free(
                parse(
                    '<script type="application/ld+json">'
                    '{"@type":"NewsArticle","isAccessibleForFree":false}</script>'
                )
            )
        )

    def test_signaux_de_bloc(self):
        self.assertFalse(item_is_free(parse("<div class='premium'>x</div>").find("div")))
        self.assertFalse(
            item_is_free(parse("<div><span>Réservé aux abonnés</span></div>").find("div"))
        )
        self.assertTrue(item_is_free(parse("<div><span>Gratuit</span></div>").find("div")))


class FluxExistantTest(unittest.TestCase):
    def test_normalise_un_rss(self):
        result = extract(
            load("flux.xml"), "https://source.example/rss", content_type="application/rss+xml"
        )
        self.assertEqual(result.strategy, "feed")
        self.assertEqual([item.title for item in result.items], ["Entrée A", "Entrée B"])
        self.assertEqual(result.items[0].author, "Alice")
        self.assertEqual(result.items[1].guid, "tag:source,2026:b")

    def test_atom(self):
        atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
        <title>F</title><entry><title>E1</title><id>urn:1</id>
        <link rel="alternate" href="https://s.fr/1"/><published>2026-08-01T10:00:00Z</published>
        <summary>résumé</summary></entry></feed>"""
        result = extract(atom, "https://s.fr/atom.xml", content_type="application/atom+xml")
        self.assertEqual(result.strategy, "feed")
        self.assertEqual(result.items[0].link, "https://s.fr/1")


class RobustesseTest(unittest.TestCase):
    def test_page_vide(self):
        result = extract("", "https://ex.fr")
        self.assertEqual(result.items, [])
        self.assertEqual(result.strategy, "empty")

    def test_page_sans_liste(self):
        result = extract("<html><body><h1>Titre</h1><p>Texte</p></body></html>", "https://ex.fr")
        self.assertEqual(result.items, [])

    def test_doublons_supprimes(self):
        html = "<div class='l'>" + "".join(
            f'<div class="i"><h3><a href="/a">Un titre unique et assez long</a></h3>'
            f'<p>Un résumé suffisamment long pour être conservé ici {n}.</p></div>'
            for n in range(4)
        ) + "</div>"
        result = extract(html, "https://ex.fr")
        self.assertEqual(len(result.items), 1)


if __name__ == "__main__":
    unittest.main()
