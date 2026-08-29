"""Tests bout en bout : serveur de fixtures local + génération et service du flux."""

import json
import threading
import unittest
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree

from cloclo import cli, fetch
from cloclo.extract import Options
from cloclo.generator import Settings, build
from cloclo.server import FeedHandler, FeedServer

FIXTURES = Path(__file__).parent / "fixtures"


class _QuietFiles(SimpleHTTPRequestHandler):
    """Sert les fixtures sans polluer la sortie des tests."""

    def log_message(self, fmt, *args):
        pass


def _start(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


class LiveSiteTest(unittest.TestCase):
    """Le site de test est servi sur 127.0.0.1 : `allow_private` est requis."""

    @classmethod
    def setUpClass(cls):
        cls.site = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(_QuietFiles, directory=str(FIXTURES))
        )
        _start(cls.site)
        cls.base = f"http://127.0.0.1:{cls.site.server_address[1]}"

        cls.service = FeedServer(
            ("127.0.0.1", 0),
            FeedHandler,
            allow_private=True,
            ttl=60,
            verbose=False,
        )
        _start(cls.service)
        cls.api = f"http://127.0.0.1:{cls.service.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        for server in (cls.site, cls.service):
            server.shutdown()
            server.server_close()

    def setUp(self):
        fetch.clear_cache()
        self.service.cache.clear()
        self.settings = Settings(allow_private=True, cache=False)

    def get(self, path):
        with urllib.request.urlopen(self.api + path, timeout=10) as response:
            return response.status, response.headers, response.read().decode("utf-8")

    # -- génération ------------------------------------------------------
    def test_build_produit_un_rss_valide(self):
        result = build(f"{self.base}/blog.html", Options(), self.settings)
        root = ElementTree.fromstring(result.body)
        self.assertEqual(root.tag, "rss")
        self.assertEqual(len(result.items), 3)
        self.assertEqual(root.find("channel").findtext("title"), "Le Carnet — journal de bord")

    def test_build_json_et_atom(self):
        for fmt, check in (("json", "jsonfeed.org"), ("atom", "<feed")):
            result = build(
                f"{self.base}/blog.html", Options(limit=1), Settings(allow_private=True, fmt=fmt)
            )
            self.assertIn(check, result.body)

    def test_texte_integral(self):
        options = Options(item=".post", link="h2 a@href", limit=1)
        settings = Settings(allow_private=True, full=True, full_limit=1, cache=False)
        # Le premier billet pointe vers un article inexistant : l'échec doit être
        # absorbé sans casser le flux.
        result = build(f"{self.base}/blog.html", options, settings)
        self.assertEqual(len(result.items), 1)
        ElementTree.fromstring(result.body)

    def test_flux_existant_normalise(self):
        result = build(f"{self.base}/flux.xml", Options(), self.settings)
        self.assertEqual(result.extraction.strategy, "feed")
        self.assertEqual(len(result.items), 2)

    def test_url_introuvable(self):
        with self.assertRaises(fetch.FetchError) as erreur:
            build(f"{self.base}/inconnu.html", Options(), self.settings)
        self.assertEqual(erreur.exception.status, 404)

    def test_adresse_privee_refusee_par_defaut(self):
        with self.assertRaises(fetch.FetchError):
            build(f"{self.base}/blog.html", Options(), Settings())

    # -- service HTTP ----------------------------------------------------
    def test_accueil_et_sonde(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("Cloclo", body)
        status, _, body = self.get("/healthz")
        self.assertEqual(json.loads(body)["status"], "ok")

    def test_endpoint_feed(self):
        status, headers, body = self.get(f"/feed?url={self.base}/blog.html&limit=2")
        self.assertEqual(status, 200)
        self.assertIn("application/rss+xml", headers["Content-Type"])
        self.assertTrue(headers["ETag"])
        root = ElementTree.fromstring(body)
        self.assertEqual(len(root.findall("channel/item")), 2)
        self.assertIn("/feed?url=", root.find("channel/{http://www.w3.org/2005/Atom}link").get("href"))

    def test_endpoint_feed_formats(self):
        _, headers, body = self.get(f"/feed?url={self.base}/blog.html&format=json")
        self.assertIn("feed+json", headers["Content-Type"])
        self.assertEqual(len(json.loads(body)["items"]), 3)

    def test_etag_renvoie_304(self):
        chemin = f"/feed?url={self.base}/blog.html"
        _, headers, _ = self.get(chemin)
        request = urllib.request.Request(
            self.api + chemin, headers={"If-None-Match": headers["ETag"]}
        )
        # urllib traite 304 comme une erreur : c'est bien la réponse attendue.
        with self.assertRaises(urllib.error.HTTPError) as erreur:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(erreur.exception.code, 304)

    def test_endpoint_preview(self):
        _, _, body = self.get(f"/preview?url={self.base}/paywall.html&include_paid=1")
        payload = json.loads(body)
        self.assertEqual(payload["strategy"], "heuristic")
        self.assertEqual([i["free"] for i in payload["items"]], [True, False, True, False])

    def test_endpoint_discover(self):
        _, _, body = self.get(f"/discover?url={self.base}/blog.html")
        self.assertIn("/feed/", json.dumps(json.loads(body)["feeds"]))

    def test_erreurs_du_service(self):
        for chemin, attendu in (
            ("/feed", 400),
            ("/inconnu", 404),
            (f"/feed?url={self.base}/inconnu.html", 404),
        ):
            with self.assertRaises(urllib.error.HTTPError) as erreur:
                self.get(chemin)
            self.assertEqual(erreur.exception.code, attendu)

    def test_liste_blanche_de_domaines(self):
        self.service.allow_hosts = ["exemple.fr"]
        try:
            with self.assertRaises(urllib.error.HTTPError) as erreur:
                self.get(f"/feed?url={self.base}/blog.html")
            self.assertEqual(erreur.exception.code, 403)
        finally:
            self.service.allow_hosts = []

    # -- ligne de commande ------------------------------------------------
    def test_cli_feed_vers_fichier(self):
        import tempfile

        with tempfile.TemporaryDirectory() as dossier:
            sortie = Path(dossier, "flux.xml")
            code = cli.main(
                ["feed", f"{self.base}/blog.html", "--allow-private", "-o", str(sortie)]
            )
            self.assertEqual(code, 0)
            ElementTree.fromstring(sortie.read_text(encoding="utf-8"))

    def test_cli_preview_json(self):
        code = cli.main(["preview", f"{self.base}/liste.html", "--allow-private", "--json"])
        self.assertEqual(code, 0)

    def test_cli_erreur_reseau(self):
        self.assertEqual(cli.main(["feed", f"{self.base}/inconnu.html", "--allow-private"]), 2)

    def test_cli_sans_entree(self):
        self.assertEqual(cli.main(["feed", f"{self.base}/article.html", "--allow-private"]), 3)


if __name__ == "__main__":
    unittest.main()
