import json
import unittest
from datetime import datetime, timezone
from xml.etree import ElementTree

from cloclo.extract import Item
from cloclo.feed import FeedMeta, render, stable_guid

META = FeedMeta(
    title="Mon flux",
    link="https://ex.fr/blog",
    description="Une description",
    self_url="https://cloclo.ex/feed?url=https://ex.fr/blog",
    language="fr",
    icon="https://ex.fr/icon.png",
)
ITEMS = [
    Item(
        title="Titre & <balise>",
        link="https://ex.fr/a?p=1",
        date=datetime(2026, 8, 21, 7, 15, tzinfo=timezone.utc),
        author="Claire Menou",
        summary="Un résumé",
        categories=["philosophie", "lectures"],
        image="https://ex.fr/i.jpg",
        content_html="<p>Texte <em>intégral</em></p>",
    ),
    Item(title="Sans date ni lien"),
]


class RssTest(unittest.TestCase):
    def setUp(self):
        self.body, self.content_type = render(ITEMS, META, "rss")
        self.root = ElementTree.fromstring(self.body)

    def test_xml_valide_et_type_mime(self):
        self.assertEqual(self.content_type, "application/rss+xml; charset=utf-8")
        self.assertEqual(self.root.tag, "rss")

    def test_entete_du_canal(self):
        channel = self.root.find("channel")
        self.assertEqual(channel.findtext("title"), "Mon flux")
        self.assertEqual(channel.findtext("language"), "fr")
        lien = channel.find("{http://www.w3.org/2005/Atom}link")
        self.assertEqual(lien.get("rel"), "self")

    def test_entrees(self):
        items = self.root.find("channel").findall("item")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].findtext("title"), "Titre & <balise>")
        self.assertEqual(items[0].findtext("pubDate"), "Fri, 21 Aug 2026 07:15:00 +0000")
        self.assertEqual(
            items[0].findtext("{http://purl.org/dc/elements/1.1/}creator"), "Claire Menou"
        )
        self.assertEqual(
            [c.text for c in items[0].findall("category")], ["philosophie", "lectures"]
        )

    def test_guid_stable_sans_lien(self):
        items = self.root.find("channel").findall("item")
        self.assertTrue(items[1].findtext("guid").startswith("urn:cloclo:"))
        self.assertEqual(stable_guid(ITEMS[1]), stable_guid(ITEMS[1]))

    def test_texte_integral_optionnel(self):
        sans = ElementTree.fromstring(render(ITEMS, META, "rss")[0])
        avec = ElementTree.fromstring(render(ITEMS, META, "rss", full=True)[0])
        balise = "{http://purl.org/rss/1.0/modules/content/}encoded"
        self.assertIsNone(sans.find("channel/item").find(balise))
        self.assertIn("intégral", avec.find("channel/item").findtext(balise))

    def test_caracteres_de_controle_retires(self):
        body, _ = render([Item(title="a\x0bb", link="https://ex.fr/x")], META, "rss")
        ElementTree.fromstring(body)
        self.assertNotIn("\x0b", body)


class AtomTest(unittest.TestCase):
    def test_structure(self):
        body, content_type = render(ITEMS, META, "atom")
        root = ElementTree.fromstring(body)
        self.assertEqual(content_type, "application/atom+xml; charset=utf-8")
        namespace = "{http://www.w3.org/2005/Atom}"
        entries = root.findall(namespace + "entry")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].findtext(namespace + "id"), "https://ex.fr/a?p=1")
        self.assertEqual(
            entries[0].findtext(namespace + "published"), "2026-08-21T07:15:00Z"
        )


class JsonFeedTest(unittest.TestCase):
    def test_structure(self):
        body, content_type = render(ITEMS, META, "json")
        payload = json.loads(body)
        self.assertEqual(content_type, "application/feed+json; charset=utf-8")
        self.assertEqual(payload["version"], "https://jsonfeed.org/version/1.1")
        self.assertEqual(payload["items"][0]["authors"], [{"name": "Claire Menou"}])
        self.assertEqual(payload["items"][0]["date_published"], "2026-08-21T07:15:00Z")
        self.assertNotIn("url", payload["items"][1])


class FormatTest(unittest.TestCase):
    def test_format_inconnu(self):
        with self.assertRaises(ValueError):
            render(ITEMS, META, "opml")


if __name__ == "__main__":
    unittest.main()
