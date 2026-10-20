#!/usr/bin/env python3
"""Veille boursière quotidienne sur les actions européennes.

Chaque matin : lit l'actualité économique et géopolitique, charge les cours
de l'univers suivi, fait le bilan des signaux passés, demande à Claude
des recommandations d'achat et de vente, envoie le rapport par courriel
et inscrit les nouveaux signaux au journal.

Variables d'environnement :
    ANTHROPIC_API_KEY    clé de l'API Anthropic
    GMAIL_USER           adresse Gmail qui envoie le rapport
    GMAIL_APP_PASSWORD   mot de passe d'application Gmail
    DESTINATAIRE         adresse qui reçoit le rapport (GMAIL_USER par défaut)

Usage :
    python3 bourse/veille.py                 rapport complet et envoi
    python3 bourse/veille.py --sans-envoi    écrit le rapport dans bourse/dernier-rapport.html
    python3 bourse/veille.py --demo          données simulées, sans réseau ni API
"""

import argparse
import html
import json
import math
import os
import random
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import yaml

ICI = Path(__file__).resolve().parent
CONFIG = ICI / "config.yaml"
JOURNAL = ICI / "journal.jsonl"
APERCU = ICI / "dernier-rapport.html"
PARIS = ZoneInfo("Europe/Paris")
UA = "Mozilla/5.0 (Cloclo veille boursiere; github.com/thomasstamm75/Cloclo)"
MODELE = "claude-opus-5-5"
MAX_ARTICLES = 220
MAX_RECOS = 5


# Actualités

def heures_de_veille(maintenant):
    """Le lundi, la veille couvre le week-end."""
    return 72 if maintenant.weekday() == 0 else 26


def lire_flux(config, heures):
    import feedparser
    import requests

    limite = datetime.now(timezone.utc) - timedelta(hours=heures)
    sources = [(f["nom"], f["url"]) for f in config.get("flux", [])]
    for q in config.get("recherches_google_news", []):
        url = ("https://news.google.com/rss/search?q=" + quote_plus(f"{q} when:{max(1, heures // 24)}d")
               + "&hl=fr&gl=FR&ceid=FR:fr")
        sources.append((f"Google News : {q}", url))

    articles, vus, erreurs = [], set(), []
    for nom, url in sources:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
            r.raise_for_status()
            flux = feedparser.parse(r.content)
        except Exception as e:  # une source en panne ne bloque pas le rapport
            erreurs.append(f"{nom} : {e.__class__.__name__}")
            continue
        for e in flux.entries[:30]:
            date = e.get("published_parsed") or e.get("updated_parsed")
            if date:
                quand = datetime(*date[:6], tzinfo=timezone.utc)
                if quand < limite:
                    continue
            else:
                quand = None
            titre = " ".join((e.get("title") or "").split())
            cle = titre.lower()[:90]
            if not titre or cle in vus:
                continue
            vus.add(cle)
            resume = " ".join(html.unescape(_sans_balises(e.get("summary") or "")).split())[:280]
            articles.append({
                "tri": quand.isoformat() if quand else "",
                "source": nom,
                "date": quand.astimezone(PARIS).strftime("%d/%m %Hh%M") if quand else "",
                "titre": titre,
                "resume": resume if resume.lower() != titre.lower() else "",
            })
    articles.sort(key=lambda a: a["tri"], reverse=True)
    return articles[:MAX_ARTICLES], erreurs


def _sans_balises(texte):
    sortie, dedans = [], False
    for c in texte:
        if c == "<":
            dedans = True
        elif c == ">":
            dedans = False
        elif not dedans:
            sortie.append(c)
    return "".join(sortie)


# Cours et indicateurs

def charger_cours(codes):
    """Renvoie {code: liste des clôtures quotidiennes sur un an}."""
    import yfinance as yf

    donnees = yf.download(codes, period="1y", interval="1d", auto_adjust=True,
                          group_by="ticker", progress=False, threads=True)
    cours = {}
    for code in codes:
        try:
            serie = donnees[code]["Close"].dropna()
        except KeyError:
            continue
        if len(serie) >= 30:
            cours[code] = [float(x) for x in serie.tolist()]
    return cours


def rsi(clotures, n=14):
    gains, pertes = 0.0, 0.0
    variations = [b - a for a, b in zip(clotures[:-1], clotures[1:])]
    if len(variations) < n:
        return None
    for v in variations[:n]:
        gains += max(v, 0)
        pertes += max(-v, 0)
    gains, pertes = gains / n, pertes / n
    for v in variations[n:]:
        gains = (gains * (n - 1) + max(v, 0)) / n
        pertes = (pertes * (n - 1) + max(-v, 0)) / n
    if pertes == 0:
        return 100.0
    return 100 - 100 / (1 + gains / pertes)


def indicateurs(clotures):
    dernier = clotures[-1]

    def var(jours):
        return round((dernier / clotures[-1 - jours] - 1) * 100, 1) if len(clotures) > jours else None

    mm50 = sum(clotures[-50:]) / min(50, len(clotures))
    mm200 = sum(clotures[-200:]) / min(200, len(clotures))
    haut = max(clotures[-252:])
    rendements = [b / a - 1 for a, b in zip(clotures[-21:-1], clotures[-20:])]
    moyenne = sum(rendements) / len(rendements)
    vol = math.sqrt(sum((r - moyenne) ** 2 for r in rendements) / len(rendements)) * math.sqrt(252) * 100
    return {
        "cours": round(dernier, 2),
        "var_1j": var(1),
        "var_5j": var(5),
        "var_1m": var(21),
        "var_3m": var(63),
        "ecart_mm50": round((dernier / mm50 - 1) * 100, 1),
        "ecart_mm200": round((dernier / mm200 - 1) * 100, 1),
        "ecart_plus_haut_1an": round((dernier / haut - 1) * 100, 1),
        "rsi14": round(rsi(clotures[-120:]) or 0, 0),
        "volatilite_1m": round(vol, 0),
    }


# Journal des signaux

def lire_journal():
    if not JOURNAL.exists():
        return []
    return [json.loads(l) for l in JOURNAL.read_text(encoding="utf-8").splitlines() if l.strip()]


def ecrire_journal(lignes):
    JOURNAL.write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in lignes), encoding="utf-8")


def jours_ouvres(debut, fin):
    d0, d1 = datetime.fromisoformat(debut).date(), datetime.fromisoformat(fin).date()
    n, j = 0, d0
    while j < d1:
        j += timedelta(days=1)
        if j.weekday() < 5:
            n += 1
    return n


def mettre_a_jour_journal(journal, cours, indice_ref, aujourdhui):
    """Clôt les signaux qui ont touché l'objectif, le stop ou leur échéance."""
    for s in journal:
        if s["statut"] != "ouvert" or s["code"] not in cours:
            continue
        px = cours[s["code"]][-1]
        sens = 1 if s["sens"] == "achat" else -1
        s["dernier_cours"] = round(px, 2)
        s["gain_pct"] = round(sens * (px / s["prix_ref"] - 1) * 100, 2)
        if indice_ref in cours and s.get("indice_ref"):
            s["gain_indice_pct"] = round(sens * (cours[indice_ref][-1] / s["indice_ref"] - 1) * 100, 2)
        motif = None
        if sens * (px - s["objectif"]) >= 0:
            motif = "objectif atteint"
        elif sens * (px - s["stop_loss"]) <= 0:
            motif = "stop touché"
        elif jours_ouvres(s["date"], aujourdhui) >= s["horizon_jours"]:
            motif = "échéance"
        if motif:
            s.update(statut="clos", motif=motif, date_cloture=aujourdhui)
    return journal


def bilan(journal):
    clos = [s for s in journal if s["statut"] == "clos" and "gain_pct" in s]
    ouverts = [s for s in journal if s["statut"] == "ouvert"]
    if not clos:
        return {"clos": 0, "ouverts": len(ouverts)}
    gagnants = sum(1 for s in clos if s["gain_pct"] > 0)
    gain = sum(s["gain_pct"] for s in clos) / len(clos)
    indice = [s["gain_indice_pct"] for s in clos if "gain_indice_pct" in s]
    return {
        "clos": len(clos),
        "ouverts": len(ouverts),
        "taux_reussite": round(gagnants / len(clos) * 100),
        "gain_moyen": round(gain, 2),
        "surperf_moyenne": round(gain - sum(indice) / len(indice), 2) if indice else None,
    }


# Analyse par Claude

SYSTEME = """Tu es l'analyste actions d'un investisseur particulier français. Il gère \
lui-même un compte-titres et un PEA. Il lit ton rapport à 7h30, avant l'ouverture \
d'Euronext à 9h, et passe ses ordres dans la journée. Horizon : quelques jours à \
quelques semaines.

Ta méthode :
1. Repère dans l'actualité des dernières heures les faits économiques, monétaires et \
géopolitiques qui changent les perspectives d'une société ou d'un secteur coté. \
Un fait déjà connu depuis plusieurs jours est déjà dans les cours : ne le retiens \
pas comme catalyseur.
2. Croise chaque fait avec les indicateurs techniques fournis. Un fait favorable sur \
une valeur déjà en hausse de 15 % en un mois avec un RSI au-dessus de 75 justifie \
rarement un achat. Un fait favorable sur une valeur en tendance haussière \
(cours au-dessus de la moyenne mobile 200 jours) qui consolide est un meilleur point d'entrée.
3. Tiens compte des changements de recommandation d'analystes et des achats de \
dirigeants quand l'actualité en signale.
4. Propose au plus 5 recommandations, et zéro si rien ne le justifie. La qualité \
prime sur le nombre. Chaque recommandation porte un code de l'univers fourni, \
et aucun autre.
5. Pour un achat : stop_loss sous le dernier cours, objectif au-dessus. Pour une \
vente (signal baissier : vendre si on détient, éviter sinon) : stop_loss au-dessus, \
objectif en dessous. Le gain visé vaut au moins 1,5 fois la perte acceptée. \
Cale le stop sur la volatilité fournie.
6. Pour chaque signal encore ouvert dans le journal, dis s'il faut conserver, \
alléger ou solder, au vu de l'actualité du jour.
7. Note la confiance de 1 (faible) à 5 (forte). Réserve 4 et 5 aux cas où \
l'actualité et la technique vont dans le même sens.

Style : français, phrases courtes, voix active, ton direct. Cite les faits avec \
leurs chiffres. Pas de tiret long, pas de point-virgule, pas de formule creuse. \
Écris les noms de sociétés tels que fournis."""

SCHEMA = {
    "type": "object",
    "properties": {
        "synthese": {"type": "string", "description": "Lecture du marché du jour en 4 à 6 phrases."},
        "faits_marquants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fait": {"type": "string"},
                    "lecture_marche": {"type": "string"},
                    "codes_concernes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["fait", "lecture_marche", "codes_concernes"],
                "additionalProperties": False,
            },
        },
        "recommandations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "sens": {"type": "string", "enum": ["achat", "vente"]},
                    "these": {"type": "string"},
                    "catalyseur": {"type": "string"},
                    "stop_loss": {"type": "number"},
                    "objectif": {"type": "number"},
                    "horizon_jours": {"type": "integer", "description": "Jours ouvrés."},
                    "confiance": {"type": "integer", "description": "De 1 à 5."},
                    "sources": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["code", "sens", "these", "catalyseur", "stop_loss", "objectif",
                             "horizon_jours", "confiance", "sources"],
                "additionalProperties": False,
            },
        },
        "positions_a_revoir": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "decision": {"type": "string", "enum": ["conserver", "alléger", "solder"]},
                    "raison": {"type": "string"},
                },
                "required": ["code", "decision", "raison"],
                "additionalProperties": False,
            },
        },
        "a_surveiller": {"type": "array", "items": {"type": "string"},
                         "description": "Rendez-vous et chiffres attendus aujourd'hui et cette semaine."},
        "risques": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["synthese", "faits_marquants", "recommandations", "positions_a_revoir",
                 "a_surveiller", "risques"],
    "additionalProperties": False,
}


def demander_a_claude(contexte):
    import anthropic

    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=MODELE,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEME,
        messages=[{"role": "user", "content": contexte}],
    ) as flux:
        reponse = flux.get_final_message()
    if reponse.stop_reason == "refusal":
        raise RuntimeError(f"Analyse refusée : {reponse.stop_details}")
    if reponse.stop_reason == "max_tokens":
        raise RuntimeError("Analyse tronquée : max_tokens atteint")
    texte = next(b.text for b in reponse.content if b.type == "text")
    return json.loads(texte)


def construire_contexte(maintenant, articles, cours, config, journal):
    univers, reperes = config["univers"], config["reperes"]
    jour = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"][maintenant.weekday()]
    lignes = [f"Nous sommes le {jour} {maintenant.strftime('%d/%m/%Y')}, {maintenant.strftime('%Hh%M')} à Paris.", ""]
    lignes.append("REPÈRES DE MARCHÉ (dernière clôture)")
    for code, nom in reperes.items():
        if code in cours:
            i = indicateurs(cours[code])
            lignes.append(f"{nom} : {i['cours']} | 1j {i['var_1j']} % | 5j {i['var_5j']} % | 1m {i['var_1m']} %")
    lignes += ["", "UNIVERS SUIVI (code | nom | cours | var 1j, 5j, 1m, 3m en % | écart MM50, MM200, "
               "plus haut 1 an en % | RSI14 | volatilité annualisée 1m en %)"]
    for code, nom in univers.items():
        if code in cours:
            i = indicateurs(cours[code])
            lignes.append(f"{code} | {nom} | {i['cours']} | {i['var_1j']}, {i['var_5j']}, {i['var_1m']}, "
                          f"{i['var_3m']} | {i['ecart_mm50']}, {i['ecart_mm200']}, {i['ecart_plus_haut_1an']} "
                          f"| {i['rsi14']:.0f} | {i['volatilite_1m']:.0f}")
    ouverts = [s for s in journal if s["statut"] == "ouvert"]
    lignes += ["", "SIGNAUX OUVERTS DANS LE JOURNAL"]
    if ouverts:
        for s in ouverts:
            lignes.append(f"{s['code']} | {s['sens']} le {s['date']} à {s['prix_ref']} | stop {s['stop_loss']} "
                          f"| objectif {s['objectif']} | gain actuel {s.get('gain_pct', 0)} %")
    else:
        lignes.append("Aucun.")
    lignes += ["", f"ACTUALITÉ ({len(articles)} articles)"]
    for a in articles:
        resume = f" : {a['resume']}" if a["resume"] else ""
        lignes.append(f"[{a['date']} | {a['source']}] {a['titre']}{resume}")
    lignes += ["", "Produis ton analyse du jour."]
    return "\n".join(lignes)


def valider(analyse, cours, univers):
    """Écarte les recommandations hors univers ou aux niveaux incohérents."""
    gardees, ecartees = [], []
    for r in analyse["recommandations"]:
        code = r["code"].strip().upper()
        if code not in univers or code not in cours:
            ecartees.append(f"{r['code']} : hors univers")
            continue
        px = cours[code][-1]
        sens = 1 if r["sens"] == "achat" else -1
        risque, gain = sens * (px - r["stop_loss"]), sens * (r["objectif"] - px)
        if risque <= 0 or gain <= 0:
            ecartees.append(f"{code} : stop ou objectif du mauvais côté du cours")
            continue
        r.update(code=code, nom=univers[code], prix_ref=round(px, 2),
                 confiance=min(5, max(1, int(r["confiance"]))),
                 horizon_jours=min(60, max(1, int(r["horizon_jours"]))),
                 ratio=round(gain / risque, 1))
        gardees.append(r)
    analyse["recommandations"] = gardees[:MAX_RECOS]
    return ecartees


# Rapport

def e(texte):
    return html.escape(str(texte))


def rendre_html(maintenant, analyse, cours, config, journal, stats, erreurs_flux, nb_articles):
    univers = config["univers"]
    nom = lambda c: univers.get(c, c)
    vert, rouge, gris = "#1a7f37", "#c62828", "#5f6368"
    p = [f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:680px;margin:auto;color:#202124;line-height:1.45">
<h2 style="margin:0 0 4px">Veille bourse du {maintenant.strftime('%d/%m/%Y')}</h2>
<p style="color:{gris};margin:0 0 16px">{nb_articles} articles lus, {len(cours)} cours chargés, analyse {MODELE}</p>"""]

    reperes = []
    for code, n in config["reperes"].items():
        if code in cours:
            i = indicateurs(cours[code])
            couleur = vert if (i["var_1j"] or 0) >= 0 else rouge
            reperes.append(f"{e(n)} <b>{i['cours']}</b> <span style='color:{couleur}'>{i['var_1j']:+.1f} %</span>")
    if reperes:
        p.append(f"<p style='font-size:13px'>{' &nbsp;|&nbsp; '.join(reperes)}</p>")

    p.append(f"<h3>Lecture du marché</h3><p>{e(analyse['synthese'])}</p>")

    p.append("<h3>Recommandations du jour</h3>")
    if not analyse["recommandations"]:
        p.append("<p>Aucun signal assez solide aujourd'hui. Ne rien faire est une décision.</p>")
    for r in analyse["recommandations"]:
        couleur = vert if r["sens"] == "achat" else rouge
        etoiles = "●" * r["confiance"] + "○" * (5 - r["confiance"])
        p.append(f"""<div style="border-left:4px solid {couleur};padding:8px 12px;margin:0 0 14px;background:#f8f9fa">
<b style="color:{couleur};text-transform:uppercase">{e(r['sens'])}</b> <b>{e(r['nom'])}</b> ({e(r['code'])}) &nbsp; <span title="confiance">{etoiles}</span><br>
<span style="font-size:13px">Cours {r['prix_ref']} &nbsp;|&nbsp; stop {r['stop_loss']} &nbsp;|&nbsp; objectif {r['objectif']} &nbsp;|&nbsp; gain/risque {r['ratio']} &nbsp;|&nbsp; horizon {r['horizon_jours']} j. ouvrés</span>
<p style="margin:6px 0">{e(r['these'])}</p>
<p style="margin:6px 0;font-size:13px"><b>Catalyseur :</b> {e(r['catalyseur'])}</p>
<p style="margin:0;font-size:12px;color:{gris}">Sources : {e(' / '.join(r['sources']))}</p></div>""")

    if analyse["positions_a_revoir"]:
        p.append("<h3>Signaux en cours</h3><ul>")
        for x in analyse["positions_a_revoir"]:
            p.append(f"<li><b>{e(nom(x['code']))}</b> : {e(x['decision'])}. {e(x['raison'])}</li>")
        p.append("</ul>")

    if analyse["faits_marquants"]:
        p.append("<h3>Faits marquants</h3><ul>")
        for f in analyse["faits_marquants"]:
            concernes = ", ".join(nom(c) for c in f["codes_concernes"])
            suite = f" <i style='color:{gris}'>({e(concernes)})</i>" if concernes else ""
            p.append(f"<li><b>{e(f['fait'])}</b> {e(f['lecture_marche'])}{suite}</li>")
        p.append("</ul>")

    for titre, cle in (("À surveiller", "a_surveiller"), ("Risques", "risques")):
        if analyse[cle]:
            p.append(f"<h3>{titre}</h3><ul>" + "".join(f"<li>{e(x)}</li>" for x in analyse[cle]) + "</ul>")

    p.append("<h3>Bilan des signaux passés</h3>")
    if stats["clos"]:
        surperf = f", écart moyen avec l'Euro Stoxx 50 {stats['surperf_moyenne']:+.2f} pts" if stats["surperf_moyenne"] is not None else ""
        p.append(f"<p>{stats['clos']} signaux clos : {stats['taux_reussite']} % gagnants, gain moyen "
                 f"{stats['gain_moyen']:+.2f} %{surperf}. {stats['ouverts']} signaux ouverts.</p>")
    else:
        p.append(f"<p>Pas encore de signal clos. {stats['ouverts']} signaux ouverts. Le bilan devient parlant après une trentaine de signaux.</p>")
    clos_recents = [s for s in journal if s["statut"] == "clos" and s.get("date_cloture") == maintenant.date().isoformat()]
    if clos_recents:
        p.append("<ul>" + "".join(f"<li>{e(s['nom'])} ({e(s['sens'])} du {e(s['date'])}) : {e(s['motif'])}, "
                                    f"{s['gain_pct']:+.2f} %</li>" for s in clos_recents) + "</ul>")

    if erreurs_flux:
        p.append(f"<p style='font-size:12px;color:{gris}'>Sources indisponibles ce matin : {e(', '.join(erreurs_flux))}</p>")
    p.append(f"<p style='font-size:12px;color:{gris}'>Analyse automatisée, sans garantie de résultat. "
             "Les ordres restent ta décision.</p></div>")
    return "\n".join(p)


def envoyer(sujet, corps_html):
    expediteur = os.environ["GMAIL_USER"]
    destinataire = os.environ.get("DESTINATAIRE") or expediteur
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = sujet, expediteur, destinataire
    msg.attach(MIMEText(corps_html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(expediteur, os.environ["GMAIL_APP_PASSWORD"])
        smtp.sendmail(expediteur, [destinataire], msg.as_string())


# Mode démonstration

def donnees_demo(config):
    random.seed(7)
    cours = {}
    for code in list(config["univers"]) + list(config["reperes"]):
        px, serie = random.uniform(20, 400), []
        for _ in range(252):
            px *= 1 + random.gauss(0.0004, 0.016)
            serie.append(px)
        cours[code] = serie
    articles = [{"source": "Démo", "date": "20/10 06h00", "titre": "L'Union européenne relève son budget de défense",
                 "resume": "Les Vingt-Sept s'accordent sur 150 milliards d'euros de prêts."}]
    codes = list(config["univers"])
    px = lambda c: cours[c][-1]
    analyse = {
        "synthese": "Séance attendue en légère hausse. La défense européenne reste portée par les annonces budgétaires.",
        "faits_marquants": [{"fait": "Accord européen sur 150 Md€ de prêts défense.",
                             "lecture_marche": "Soutien aux carnets de commandes des industriels.",
                             "codes_concernes": ["RHM.DE", "HO.PA"]}],
        "recommandations": [
            {"code": "HO.PA", "sens": "achat", "these": "Thales profite de l'accord européen.",
             "catalyseur": "Accord sur les prêts défense.", "stop_loss": round(px("HO.PA") * 0.94, 2),
             "objectif": round(px("HO.PA") * 1.10, 2), "horizon_jours": 15, "confiance": 4,
             "sources": ["Démo"]},
            {"code": "ZZZ.PA", "sens": "achat", "these": "Code inventé, doit être écarté.", "catalyseur": "",
             "stop_loss": 1, "objectif": 2, "horizon_jours": 5, "confiance": 3, "sources": []},
            {"code": codes[0], "sens": "vente", "these": "Niveaux incohérents, doit être écarté.", "catalyseur": "",
             "stop_loss": round(px(codes[0]) * 0.9, 2), "objectif": round(px(codes[0]) * 0.8, 2),
             "horizon_jours": 5, "confiance": 2, "sources": []},
        ],
        "positions_a_revoir": [],
        "a_surveiller": ["Indice PMI zone euro à 10h00."],
        "risques": ["Tensions commerciales avec les États-Unis."],
    }
    return cours, articles, analyse


# Programme principal

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sans-envoi", action="store_true", help="écrit le rapport sans l'envoyer")
    ap.add_argument("--demo", action="store_true", help="données simulées, sans réseau ni API")
    args = ap.parse_args()

    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    maintenant = datetime.now(PARIS)
    aujourdhui = maintenant.date().isoformat()
    indice_ref = config.get("indice_reference", "^STOXX50E")
    journal = [] if args.demo else lire_journal()

    if args.demo:
        cours, articles, analyse = donnees_demo(config)
        erreurs_flux = []
    else:
        articles, erreurs_flux = lire_flux(config, heures_de_veille(maintenant))
        print(f"{len(articles)} articles, {len(erreurs_flux)} sources en échec")
        codes = list(config["univers"]) + list(config["reperes"])
        cours = {}
        for essai in range(3):
            cours = charger_cours(codes)
            if len(cours) >= len(codes) // 2:
                break
            time.sleep(10 * (essai + 1))
        print(f"{len(cours)} cours chargés sur {len(codes)}")
        if len(cours) < len(codes) // 2:
            sys.exit("Trop peu de cours disponibles, rapport annulé.")

    journal = mettre_a_jour_journal(journal, cours, indice_ref, aujourdhui)

    if not args.demo:
        contexte = construire_contexte(maintenant, articles, cours, config, journal)
        analyse = demander_a_claude(contexte)

    ecartees = valider(analyse, cours, config["univers"])
    if ecartees:
        print("Recommandations écartées : " + " / ".join(ecartees))

    for r in analyse["recommandations"]:
        journal.append({
            "date": aujourdhui, "code": r["code"], "nom": r["nom"], "sens": r["sens"],
            "prix_ref": r["prix_ref"], "stop_loss": r["stop_loss"], "objectif": r["objectif"],
            "horizon_jours": r["horizon_jours"], "confiance": r["confiance"],
            "indice_ref": round(cours[indice_ref][-1], 2) if indice_ref in cours else None,
            "statut": "ouvert",
        })

    stats = bilan(journal)
    corps = rendre_html(maintenant, analyse, cours, config, journal, stats, erreurs_flux, len(articles))
    nb = len(analyse["recommandations"])
    sujet = f"Veille bourse {maintenant.strftime('%d/%m')} : {nb} signal{'s' if nb > 1 else ''}"
    if analyse["recommandations"]:
        sujet += " (" + ", ".join(f"{r['sens']} {r['nom']}" for r in analyse["recommandations"]) + ")"

    if args.sans_envoi or args.demo:
        APERCU.write_text(corps, encoding="utf-8")
        print(f"Rapport écrit dans {APERCU}")
    else:
        envoyer(sujet, corps)
        print(f"Rapport envoyé : {sujet}")
        ecrire_journal(journal)


if __name__ == "__main__":
    main()
