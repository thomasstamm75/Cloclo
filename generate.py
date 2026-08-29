#!/usr/bin/env python3
"""Cloclo, générateur de flux RSS.

Lit feeds.yaml, visite chaque page suivie, en extrait les publications
et écrit un flux RSS 2.0 par source dans docs/. La mémoire des dates et
des caches vit dans state/.

Usage :
    python3 generate.py --base-url https://exemple.github.io/Cloclo/
"""

import argparse
import html as htmlmod
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import requests
import yaml
from bs4 import BeautifulSoup

UA = "Cloclo-RSS/1.0 (generateur personnel de flux; github.com/thomasstamm75/Cloclo)"
TIMEOUT = 25
DOCS = Path("docs")
STATE = Path("state")
PARIS = timezone(timedelta(hours=1))

MOIS_FR = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12,
    "jan": 1, "fev": 2, "fév": 2, "avr": 4, "juil": 7, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12, "déc": 12,
}


def slugifier(texte):
    t = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t or "flux"


def texte_propre(s):
    return re.sub(r"\s+", " ", s or "").strip()


def analyser_date(brut, reference=None):
    """Comprend les dates ISO, JJ/MM/AAAA, « 12 mars 2026 » et « il y a N heures »."""
    if not brut:
        return None
    brut = texte_propre(brut)
    ref = reference or datetime.now(timezone.utc)

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?", brut)
    if m:
        a, mo, j = int(m.group(1)), int(m.group(2)), int(m.group(3))
        h = int(m.group(4) or 12)
        mi = int(m.group(5) or 0)
        try:
            return datetime(a, mo, j, h, mi, tzinfo=PARIS).astimezone(timezone.utc)
        except ValueError:
            pass

    m = re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b", brut)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                            12, 0, tzinfo=PARIS).astimezone(timezone.utc)
        except ValueError:
            pass

    m = re.search(r"\b(\d{1,2})(?:er)?\s+([a-zA-Zéûôè]+)\.?\s+(\d{4})\b", brut)
    if m:
        mois = MOIS_FR.get(m.group(2).lower())
        if mois:
            try:
                return datetime(int(m.group(3)), mois, int(m.group(1)),
                                12, 0, tzinfo=PARIS).astimezone(timezone.utc)
            except ValueError:
                pass

    m = re.search(r"il y a\s+(\d+)\s+(minute|heure|jour|semaine|mois)", brut.lower())
    if m:
        n = int(m.group(1))
        unite = m.group(2)
        deltas = {"minute": timedelta(minutes=n), "heure": timedelta(hours=n),
                  "jour": timedelta(days=n), "semaine": timedelta(weeks=n),
                  "mois": timedelta(days=30 * n)}
        return ref - deltas[unite]

    return None


def dates_jsonld(soup, base):
    """Relève les datePublished du JSON-LD, indexées par adresse absolue."""
    resultat = {}
    for balise in soup.find_all("script", type="application/ld+json"):
        try:
            donnees = json.loads(balise.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        pile = [donnees]
        while pile:
            d = pile.pop()
            if isinstance(d, list):
                pile.extend(d)
            elif isinstance(d, dict):
                url = d.get("url") or d.get("@id") or d.get("mainEntityOfPage")
                if isinstance(url, dict):
                    url = url.get("@id")
                date = d.get("datePublished") or d.get("dateCreated")
                if url and date:
                    quand = analyser_date(str(date))
                    if quand:
                        resultat[urljoin(base, str(url))] = quand
                pile.extend(v for v in d.values() if isinstance(v, (dict, list)))
    return resultat


def lien_valable(href, base):
    if not href:
        return False
    href = href.strip()
    if href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return False
    absolu = urljoin(base, href)
    return absolu.rstrip("/") != base.rstrip("/") and urlparse(absolu).scheme in ("http", "https")


def blocs_automatiques(soup):
    """Repère la liste de publications sans sélecteur fourni.

    Priorité aux balises article. Sinon, regroupe les enfants de même
    signature (balise + classes) sous un même parent et retient le groupe
    le plus fourni qui contient de vrais liens de contenu.
    """
    articles = soup.find_all("article")
    if len(articles) >= 3:
        return articles

    meilleur, score_max = [], 0
    for parent in soup.find_all(True):
        groupes = {}
        for enfant in parent.find_all(True, recursive=False):
            signature = (enfant.name, tuple(sorted(enfant.get("class") or [])))
            groupes.setdefault(signature, []).append(enfant)
        for membres in groupes.values():
            if len(membres) < 3:
                continue
            porteurs = []
            for m in membres:
                a = m.find("a", href=True)
                if a and len(texte_propre(a.get_text())) >= 15:
                    porteurs.append(m)
            if len(porteurs) < 3:
                continue
            score = len(porteurs) * sum(
                len(texte_propre(m.find("a").get_text())) for m in porteurs
            ) / len(porteurs)
            if score > score_max:
                score_max, meilleur = score, porteurs
    return meilleur


def extraire_items(html_brut, url_page, selecteur=None):
    soup = BeautifulSoup(html_brut, "lxml")
    ld = dates_jsonld(soup, url_page)
    blocs = soup.select(selecteur) if selecteur else blocs_automatiques(soup)

    items, vus = [], set()
    for bloc in blocs:
        lien_el = None
        for h in bloc.find_all(re.compile("^h[1-4]$")):
            a = h.find("a", href=True)
            if a and lien_valable(a["href"], url_page):
                lien_el = a
                break
        if lien_el is None:
            candidats = [a for a in bloc.find_all("a", href=True)
                         if lien_valable(a["href"], url_page)]
            if candidats:
                lien_el = max(candidats, key=lambda a: len(texte_propre(a.get_text())))
        if lien_el is None:
            continue

        lien = urljoin(url_page, lien_el["href"])
        if lien in vus:
            continue
        vus.add(lien)

        entete = bloc.find(re.compile("^h[1-4]$"))
        titre = texte_propre(entete.get_text() if entete else lien_el.get_text())
        if len(titre) < 4:
            continue

        quand = None
        t = bloc.find("time")
        if t is not None:
            quand = analyser_date(t.get("datetime") or t.get_text())
        if quand is None:
            quand = ld.get(lien)
        if quand is None:
            quand = analyser_date(bloc.get_text(" ")[:400])

        resume = ""
        for p in bloc.find_all("p"):
            tp = texte_propre(p.get_text())
            if len(tp) >= 40 and tp != titre:
                resume = tp[:400]
                break

        image = ""
        img = bloc.find("img")
        if img is not None:
            src = img.get("src") or img.get("data-src") or ""
            if src and not src.startswith("data:"):
                image = urljoin(url_page, src)

        items.append({"title": titre, "link": lien, "summary": resume,
                      "image": image,
                      "date": quand.isoformat() if quand else None})
    return items


def filtrer(items, inclure, exclure):
    def contient(item, mots):
        cible = (item["title"] + " " + item.get("summary", "")).lower()
        return any(m.lower() in cible for m in mots)

    if exclure:
        items = [i for i in items if not contient(i, exclure)]
    if inclure:
        items = [i for i in items if contient(i, inclure)]
    return items


def texte_integral(session, url, cache):
    if url in cache:
        return cache[url]
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException:
        return ""
    soup = BeautifulSoup(r.text, "lxml")
    for indesirable in soup(["script", "style", "nav", "header", "footer", "aside"]):
        indesirable.decompose()
    meilleur, taille_max = None, 0
    for conteneur in soup.find_all(["article", "main", "div", "section"]):
        paragraphes = conteneur.find_all("p", recursive=False) or conteneur.find_all("p")
        taille = sum(len(texte_propre(p.get_text())) for p in paragraphes)
        if taille > taille_max:
            taille_max, meilleur = taille, paragraphes
    if not meilleur or taille_max < 200:
        return ""
    corps = "".join("<p>%s</p>" % escape(texte_propre(p.get_text()))
                    for p in meilleur if texte_propre(p.get_text()))
    cache[url] = corps
    time.sleep(1)
    return corps


def ecrire_rss(chemin, source, items, adresse_flux):
    ET.register_namespace("atom", "http://www.w3.org/2005/Atom")
    rss = ET.Element("rss", version="2.0")
    canal = ET.SubElement(rss, "channel")
    ET.SubElement(canal, "title").text = source["title"]
    ET.SubElement(canal, "link").text = source["url"]
    ET.SubElement(canal, "description").text = (
        "Flux fabriqué par Cloclo à partir de " + source["url"])
    ET.SubElement(canal, "language").text = "fr"
    ET.SubElement(canal, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc))
    ET.SubElement(canal, "{http://www.w3.org/2005/Atom}link",
                  href=adresse_flux, rel="self", type="application/rss+xml")

    for it in items:
        item = ET.SubElement(canal, "item")
        ET.SubElement(item, "title").text = it["title"]
        ET.SubElement(item, "link").text = it["link"]
        ET.SubElement(item, "guid", isPermaLink="true").text = it["link"]
        ET.SubElement(item, "pubDate").text = format_datetime(
            datetime.fromisoformat(it["pubdate"]))
        morceaux = []
        if it.get("image"):
            morceaux.append('<img src="%s" alt=""/>' % escape(it["image"], {'"': "&quot;"}))
        if it.get("corps"):
            morceaux.append(it["corps"])
        elif it.get("summary"):
            morceaux.append("<p>%s</p>" % escape(it["summary"]))
        if morceaux:
            ET.SubElement(item, "description").text = "".join(morceaux)

    ET.indent(rss)
    chemin.write_bytes(b'<?xml version="1.0" encoding="UTF-8"?>\n'
                       + ET.tostring(rss, encoding="utf-8"))


def traiter_source(session, source, base_url):
    titre = source.get("title") or source["url"]
    slug = source.get("slug") or slugifier(titre)
    chemin_etat = STATE / (slug + ".json")
    etat = {}
    if chemin_etat.exists():
        try:
            etat = json.loads(chemin_etat.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            etat = {}
    premiere_vue = etat.get("first_seen", {})
    cache_corps = etat.get("full_text_cache", {})

    entetes = {}
    if etat.get("etag"):
        entetes["If-None-Match"] = etat["etag"]
    if etat.get("last_modified"):
        entetes["If-Modified-Since"] = etat["last_modified"]

    items, avertissement = None, ""
    try:
        r = session.get(source["url"], headers=entetes, timeout=TIMEOUT)
        if r.status_code == 304:
            items = etat.get("last_good_items")
            avertissement = "page inchangée, flux reconduit"
        else:
            r.raise_for_status()
            etat["etag"] = r.headers.get("ETag", "")
            etat["last_modified"] = r.headers.get("Last-Modified", "")
            items = extraire_items(r.text, source["url"], source.get("item"))
    except requests.RequestException as e:
        items = etat.get("last_good_items")
        avertissement = "site injoignable (%s), dernier flux connu reconduit" % type(e).__name__

    if items is None:
        return slug, 0, "aucun contenu et aucune mémoire, flux non écrit"

    items = filtrer(items, source.get("include") or [], source.get("exclude") or [])

    maintenant = datetime.now(timezone.utc).isoformat()
    for it in items:
        if it["link"] not in premiere_vue:
            premiere_vue[it["link"]] = it["date"] or maintenant
        it["pubdate"] = it["date"] or premiere_vue[it["link"]]

    items.sort(key=lambda i: i["pubdate"], reverse=True)
    limite = int(source.get("limit") or 30)
    items = items[:limite]

    if source.get("full_text"):
        for it in items:
            it["corps"] = texte_integral(session, it["link"], cache_corps)

    liens_courants = {i["link"] for i in items}
    etat["first_seen"] = {u: d for u, d in premiere_vue.items() if u in liens_courants}
    etat["full_text_cache"] = {u: c for u, c in cache_corps.items() if u in liens_courants}
    etat["last_good_items"] = items
    chemin_etat.write_text(json.dumps(etat, ensure_ascii=False, indent=1),
                           encoding="utf-8")

    ecrire_rss(DOCS / (slug + ".xml"), source, items, base_url + slug + ".xml")
    return slug, len(items), avertissement


def charger_sources(chemin):
    donnees = yaml.safe_load(Path(chemin).read_text(encoding="utf-8")) or []
    if isinstance(donnees, dict):
        donnees = donnees.get("feeds") or []
    sources = []
    for entree in donnees:
        if isinstance(entree, dict) and entree.get("url"):
            entree.setdefault("title", entree["url"])
            sources.append(entree)
    return sources


def principal():
    p = argparse.ArgumentParser(description="Générateur de flux RSS Cloclo")
    p.add_argument("--base-url", required=True,
                   help="Adresse publique du site, avec la barre finale")
    p.add_argument("--sources", default="feeds.yaml")
    args = p.parse_args()
    base = args.base_url if args.base_url.endswith("/") else args.base_url + "/"

    DOCS.mkdir(exist_ok=True)
    STATE.mkdir(exist_ok=True)

    try:
        sources = charger_sources(args.sources)
    except (yaml.YAMLError, OSError) as e:
        print("feeds.yaml illisible :", e, file=sys.stderr)
        return 1

    if not sources:
        print("Aucune source dans feeds.yaml. Rien à générer.")
        return 0

    session = requests.Session()
    session.headers["User-Agent"] = UA

    soucis = 0
    for source in sources:
        slug, n, note = traiter_source(session, source, base)
        ligne = "%-40s %3d articles" % (slug + ".xml", n)
        if note:
            ligne += "  [" + note + "]"
            soucis += 1
        print(ligne)
        time.sleep(1)

    print("Terminé :", len(sources), "source(s),", soucis, "avertissement(s).")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
