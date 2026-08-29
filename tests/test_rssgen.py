"""Tests de rssgen : extraction, dates, état, génération XML, configuration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from rssgen.config import ConfigError, load_config
from rssgen.dates import parse_date, to_rfc822
from rssgen.detect import declared_feeds
from rssgen.extract import (Rules, describe_autodetect, extract_articles,
                            extract_full_content)
from rssgen.rss import build_opml, build_rss
from rssgen.state import FeedState
from rssgen.util import canonical_url, clean_text, slugify, truncate

FIXTURES = Path(__file__).parent / "fixtures"
UTC = timezone.utc


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


# ------------------------------------------------------------------- util
def test_canonical_url_retire_le_tracking_et_le_fragment():
    assert canonical_url("https://X.fr/a/?utm_source=news&p=2#bas") == "https://x.fr/a?p=2"


def test_canonical_url_ignore_le_slash_final():
    assert canonical_url("https://x.fr/a/") == canonical_url("https://x.fr/a")


def test_clean_text_normalise_les_espaces_insecables():
    assert clean_text("  bonjour   le\n\tmonde ") == "bonjour le monde"


def test_truncate_coupe_sur_un_mot_entier():
    result = truncate("un deux trois quatre cinq", 12)
    assert result.endswith("…") and "quatr" not in result


def test_slugify_produit_un_identifiant_de_fichier():
    assert slugify("https://www.Exemple.fr/Actualités/") == "exemple-fr-actualit-s"


# ------------------------------------------------------------------ dates
@pytest.mark.parametrize("raw, expected", [
    ("12 janvier 2026", (2026, 1, 12)),
    ("1er avril 2024", (2024, 4, 1)),
    ("3 déc. 2025", (2025, 12, 3)),
    ("2026-01-12T09:05:00+02:00", (2026, 1, 12)),
    ("01/02/2026", (2026, 2, 1)),
    ("January 12, 2026", (2026, 1, 12)),
    ("Mon, 12 Jan 2026 10:00:00 +0100", (2026, 1, 12)),
])
def test_parse_date_reconnait_les_formats_courants(raw, expected):
    parsed = parse_date(raw)
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day) == expected


@pytest.mark.parametrize("raw", ["", None, "page 12", "31/13/2020", "lorem ipsum"])
def test_parse_date_rejette_ce_qui_n_est_pas_une_date(raw):
    assert parse_date(raw) is None


def test_parse_date_relative_est_dans_le_passe():
    reference = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    assert parse_date("il y a 3 heures", reference) == reference - timedelta(hours=3)
    assert parse_date("2 days ago", reference) == reference - timedelta(days=2)


def test_parse_date_ecarte_les_dates_trop_lointaines():
    assert parse_date("12 janvier 2099") is None


def test_parse_date_heure_francaise():
    parsed = parse_date("3 mars 2025 à 14h30")
    assert (parsed.hour, parsed.minute) == (14, 30)


def test_to_rfc822_respecte_le_format_rss():
    stamp = datetime(2026, 1, 12, 9, 5, tzinfo=UTC)
    assert to_rfc822(stamp) == "Mon, 12 Jan 2026 09:05:00 +0000"


# -------------------------------------------------------------- extraction
def test_detection_auto_sur_un_blog_wordpress():
    articles = extract_articles(fixture("wordpress"), "https://blog.example/")
    assert len(articles) == 4
    first = articles[0]
    assert first.title == "La réforme budgétaire de 2026 en détail"
    assert first.link == "https://blog.example/2026/03/reforme-budgetaire/"
    assert first.author == "Marie Dupont"
    assert first.published.year == 2026
    assert "loi de finances" in first.summary


def test_detection_auto_ignore_navigation_barre_laterale_et_pied_de_page():
    articles = extract_articles(fixture("wordpress"), "https://blog.example/")
    liens = {a.link for a in articles}
    assert not any("/vieux-" in lien or "mentions-legales" in lien for lien in liens)


def test_detection_auto_sur_une_grille_de_cartes():
    articles = extract_articles(fixture("cards"), "https://actu.example/")
    assert len(articles) == 4
    assert articles[0].image.endswith("/media/1.webp")  # lu depuis data-src
    assert articles[0].published.day == 15


def test_lecture_du_jsonld():
    articles = extract_articles(fixture("jsonld"), "https://revue.example/")
    assert len(articles) == 3
    assert articles[0].author == "Claire Bernard"
    assert articles[0].link == "https://revue.example/analyse-cour-comptes"


def test_selecteurs_explicites():
    rules = Rules(item="div.card", title="h3.card__title", date="span.card__date")
    articles = extract_articles(fixture("cards"), "https://actu.example/", rules)
    assert len(articles) == 4
    assert articles[1].title == "Deuxième annonce sur le financement"


def test_selecteur_sans_correspondance_ne_leve_pas_d_erreur():
    rules = Rules(item="div.inexistant")
    assert extract_articles(fixture("cards"), "https://actu.example/", rules) == []


def test_page_sans_date_donne_des_articles_sans_date():
    articles = extract_articles(fixture("nodate"), "https://veille.example/")
    assert len(articles) == 4
    assert all(a.published is None for a in articles)


def test_describe_autodetect_propose_un_selecteur_utilisable():
    report = describe_autodetect(fixture("wordpress"), "https://blog.example/")
    assert report["found"] and report["item_selector"] == "article.post"


def test_deduplication_fusionne_les_liens_identiques():
    html = """<ul>
      <li class="e"><a href="/a">Un titre suffisamment long ici</a></li>
      <li class="e"><a href="/a?utm_source=x">Un titre suffisamment long ici</a></li>
      <li class="e"><a href="/b">Un autre titre suffisamment long</a></li>
      <li class="e"><a href="/c">Un troisième titre suffisamment long</a></li>
    </ul>"""
    articles = extract_articles(html, "https://x.fr/")
    assert len({a.guid for a in articles}) == len(articles) == 3


def test_extraction_du_texte_integral_absolutise_les_liens():
    html = """<html><body><article class="post-content">
      <p>Un paragraphe de contenu.</p><a href="/interne">lien</a>
      <script>alert(1)</script></article></body></html>"""
    content = extract_full_content(html, "https://x.fr/page")
    assert "https://x.fr/interne" in content
    assert "<script>" not in content


# --------------------------------------------------------------- détection
def test_flux_declare_dans_le_head():
    html = ('<html><head><link rel="alternate" type="application/rss+xml" '
            'title="RSS" href="/feed"></head></html>')
    feeds = declared_feeds(html, "https://x.fr/page")
    assert feeds and feeds[0]["url"] == "https://x.fr/feed"


def test_link_alternate_generique_est_ignore():
    html = ('<html><head><link rel="alternate" type="application/xml" '
            'href="/sitemap.xml"></head></html>')
    assert declared_feeds(html, "https://x.fr/") == []


# --------------------------------------------------------------------- RSS
def _parse(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def test_le_flux_genere_est_un_rss2_valide():
    articles = extract_articles(fixture("wordpress"), "https://blog.example/")
    xml = build_rss(articles, title="Blog", link="https://blog.example/",
                    feed_url="https://moi.github.io/feeds/blog.xml")
    root = _parse(xml)
    assert root.tag == "rss" and root.get("version") == "2.0"
    channel = root.find("channel")
    assert channel.findtext("title") == "Blog"
    assert len(channel.findall("item")) == 4
    assert channel.find("{http://www.w3.org/2005/Atom}link") is not None


def test_chaque_article_porte_un_guid_et_une_date():
    articles = extract_articles(fixture("wordpress"), "https://blog.example/")
    channel = _parse(build_rss(articles, title="B", link="https://b.fr/")).find("channel")
    for item in channel.findall("item"):
        assert item.findtext("guid")
        assert item.findtext("pubDate")
        assert item.findtext("link")


def test_les_caracteres_de_controle_sont_retires():
    from rssgen.extract import Article
    article = Article(title="Titre\x0bcassé", link="https://x.fr/a")
    xml = build_rss([article], title="T\x00", link="https://x.fr/")
    _parse(xml)  # ne doit pas lever
    assert "\x0b" not in xml and "\x00" not in xml


def test_le_resume_est_echappe_et_non_interprete():
    from rssgen.extract import Article
    article = Article(title="T", link="https://x.fr/a", summary="<script>alert(1)</script>")
    channel = _parse(build_rss([article], title="T", link="https://x.fr/")).find("channel")
    description = channel.find("item").findtext("description")
    assert "&lt;script&gt;" in description


def test_opml_liste_les_flux():
    xml = build_opml([("Blog", "https://x.fr/f.xml", "https://blog.fr/")])
    outline = _parse(xml).find("body/outline")
    assert outline.get("xmlUrl") == "https://x.fr/f.xml"
    assert outline.get("type") == "rss"


# -------------------------------------------------------------------- état
def test_l_etat_fige_la_date_de_premiere_apparition(tmp_path):
    path = tmp_path / "flux.json"
    state = FeedState(path)
    assert state.is_new("guid-1")
    premiere = state.stamp("guid-1", "Titre", None)
    state.save()

    relu = FeedState(path)
    assert not relu.is_new("guid-1")
    assert relu.stamp("guid-1", "Titre", None) == premiere


def test_une_date_de_la_page_prime_sur_l_etat(tmp_path):
    state = FeedState(tmp_path / "flux.json")
    publiee = datetime(2026, 2, 1, tzinfo=UTC)
    assert state.stamp("guid", "T", publiee) == publiee


def test_l_etat_est_borne_en_taille(tmp_path):
    from rssgen.state import MAX_TRACKED_ITEMS
    path = tmp_path / "flux.json"
    state = FeedState(path)
    base = datetime(2020, 1, 1, tzinfo=UTC)
    for index in range(MAX_TRACKED_ITEMS + 50):
        state.stamp(f"guid-{index}", "T", base + timedelta(days=index))
    state.save()
    assert len(FeedState(path).items) == MAX_TRACKED_ITEMS


def test_un_fichier_d_etat_corrompu_ne_bloque_pas(tmp_path):
    path = tmp_path / "flux.json"
    path.write_text("{ ceci n'est pas du json", encoding="utf-8")
    assert FeedState(path).items == {}


# ----------------------------------------------------------- configuration
def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "feeds.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_chargement_d_une_configuration_minimale(tmp_path):
    path = write_config(tmp_path, "feeds:\n  - url: https://x.fr/actus\n")
    config = load_config(path)
    assert len(config.feeds) == 1
    assert config.feeds[0].id == "x-fr-actus"
    assert config.feeds[0].max_items == 25


def test_les_valeurs_par_defaut_s_appliquent(tmp_path):
    path = write_config(tmp_path, """
defaults:
  max_items: 5
  full_text: true
feeds:
  - id: a
    url: https://x.fr/a
  - id: b
    url: https://x.fr/b
    max_items: 40
""")
    config = load_config(path)
    assert config.get("a").max_items == 5 and config.get("a").full_text
    assert config.get("b").max_items == 40


def test_url_manquante_refusee(tmp_path):
    path = write_config(tmp_path, "feeds:\n  - id: a\n")
    with pytest.raises(ConfigError, match="url"):
        load_config(path)


def test_identifiant_en_double_refuse(tmp_path):
    path = write_config(tmp_path,
                        "feeds:\n  - id: a\n    url: https://x.fr/1\n"
                        "  - id: a\n    url: https://x.fr/2\n")
    with pytest.raises(ConfigError, match="double"):
        load_config(path)


def test_selecteur_inconnu_refuse(tmp_path):
    path = write_config(tmp_path,
                        "feeds:\n  - id: a\n    url: https://x.fr/1\n"
                        "    selectors:\n      titre: h2\n")
    with pytest.raises(ConfigError, match="inconnu"):
        load_config(path)


def test_expression_reguliere_invalide_refusee(tmp_path):
    path = write_config(tmp_path,
                        "feeds:\n  - id: a\n    url: https://x.fr/1\n    include: '[('\n")
    with pytest.raises(ConfigError, match="régulière"):
        load_config(path)


def test_yaml_invalide_refuse(tmp_path):
    path = write_config(tmp_path, "feeds: [\n  - id\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_filtres_include_et_exclude(tmp_path):
    path = write_config(tmp_path, """
feeds:
  - id: a
    url: https://x.fr/1
    include: "budget|finances"
    exclude: "publicité"
""")
    feed = load_config(path).get("a")
    assert feed.matches("Le budget 2026", "", "")
    assert not feed.matches("Un autre sujet", "", "")
    assert not feed.matches("Le budget publicité", "", "")


def test_flux_desactive_absent_des_actifs(tmp_path):
    path = write_config(tmp_path,
                        "feeds:\n  - id: a\n    url: https://x.fr/1\n    enabled: false\n")
    config = load_config(path)
    assert len(config.feeds) == 1 and config.active == []
