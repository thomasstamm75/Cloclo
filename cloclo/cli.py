"""Interface en ligne de commande."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .dates import to_rfc3339
from .extract import Options
from .fetch import FetchError
from .generator import Settings, build, collect


def _add_extraction_flags(parser):
    group = parser.add_argument_group("sélecteurs (facultatifs, suffixe @attr possible)")
    group.add_argument("--item", default="", help="bloc d'une entrée, ex. '.post'")
    group.add_argument("--title", default="", help="titre, ex. 'h2 a'")
    group.add_argument("--link", default="", help="lien, ex. 'h2 a@href'")
    group.add_argument("--date", default="", help="date, ex. 'time@datetime'")
    group.add_argument("--summary", default="", help="résumé, ex. '.chapo'")
    group.add_argument("--author", default="", help="auteur, ex. '.byline'")
    group.add_argument("--image", default="", help="image, ex. 'img@src'")
    group.add_argument("--category", default="", help="catégories, ex. '.tag'")

    group = parser.add_argument_group("extraction")
    group.add_argument("-n", "--limit", type=int, default=30, help="nombre d'entrées (30)")
    group.add_argument(
        "--min-items", type=int, default=3, help="taille minimale d'une série (3)"
    )
    group.add_argument(
        "--include-paid",
        action="store_true",
        help="conserver les entrées repérées comme réservées aux abonnés",
    )
    group.add_argument(
        "--strategy",
        choices=("auto", "selectors", "jsonld", "heuristic", "feed"),
        default="auto",
        help="forcer une stratégie de détection (auto)",
    )


def _add_network_flags(parser):
    group = parser.add_argument_group("réseau")
    group.add_argument("--timeout", type=float, default=15.0, help="délai en secondes (15)")
    group.add_argument("--user-agent", default=None, help="en-tête User-Agent")
    group.add_argument(
        "--no-robots",
        action="store_true",
        help="ignorer robots.txt (à n'utiliser que sur vos propres sites)",
    )
    group.add_argument(
        "--allow-private", action="store_true", help="autoriser les adresses privées"
    )
    group.add_argument("--no-cache", action="store_true", help="ne pas mettre en cache")
    group.add_argument("--recipes", default="", help="fichier de recettes supplémentaire")
    group.add_argument(
        "--no-recipes", action="store_true", help="ignorer les recettes par site"
    )


def _options(args):
    return Options(
        item=args.item,
        title=args.title,
        link=args.link,
        date=args.date,
        summary=args.summary,
        author=args.author,
        image=args.image,
        category=args.category,
        limit=args.limit,
        min_items=args.min_items,
        include_paid=args.include_paid,
        strategy=args.strategy,
    )


def _settings(args, **overrides):
    settings = Settings(
        timeout=args.timeout,
        respect_robots=not args.no_robots,
        allow_private=args.allow_private,
        cache=not args.no_cache,
        recipes_path=args.recipes,
        use_recipes=not args.no_recipes,
    )
    if args.user_agent:
        settings.user_agent = args.user_agent
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def cmd_feed(args):
    settings = _settings(
        args,
        fmt=args.format,
        full=args.full,
        full_limit=args.full_limit,
        title=args.feed_title,
        self_url=args.self_url,
    )
    result = build(args.url, _options(args), settings)
    if args.output and args.output != "-":
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(result.body)
        print(
            f"{len(result.items)} entrée(s) → {args.output}"
            f" (stratégie : {result.extraction.strategy})",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(result.body)
    return 0 if result.items else 3


def cmd_preview(args):
    extraction, recipe = collect(args.url, _options(args), _settings(args))
    if args.json:
        print(
            json.dumps(
                {
                    "url": extraction.page.url,
                    "strategy": extraction.strategy,
                    "selectors": extraction.selectors,
                    "candidates": extraction.candidates,
                    "recipe": recipe,
                    "feeds": extraction.page.feeds,
                    "items": [i.as_dict() for i in extraction.items],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if extraction.items else 3

    print(f"Page      : {extraction.page.title or '—'}")
    print(f"URL       : {extraction.page.url}")
    print(f"Stratégie : {extraction.strategy}")
    if extraction.selectors:
        print(f"Sélecteurs: {extraction.selectors}")
    if recipe:
        print(f"Recette   : {recipe.get('match')}")
    if extraction.page.feeds:
        print("Flux déjà publiés par le site :")
        for entry in extraction.page.feeds:
            print(f"  - {entry['url']}")
    print(f"\n{len(extraction.items)} entrée(s) :\n")
    for index, item in enumerate(extraction.items, 1):
        marker = "" if item.free else " [payant]"
        print(f"{index:3}. {item.title}{marker}")
        print(f"     {item.link}")
        details = []
        if item.date:
            details.append(to_rfc3339(item.date))
        if item.author:
            details.append(item.author)
        if item.categories:
            details.append(", ".join(item.categories))
        if details:
            print(f"     {' · '.join(details)}")
        if item.summary:
            print(f"     {item.summary[:160]}")
    if extraction.candidates and len(extraction.candidates) > 1:
        print("\nAutres séries détectées (à réutiliser avec --item) :")
        for candidate in extraction.candidates[1:5]:
            print(
                f"  {candidate['selector']:<40} score {candidate['score']:>6}"
                f"  {candidate['count']} blocs"
            )
    return 0 if extraction.items else 3


def cmd_discover(args):
    from . import dom, fetch as fetch_module
    from .extract import discover_feeds

    response = fetch_module.fetch(
        args.url,
        timeout=args.timeout,
        respect_robots=not args.no_robots,
        allow_private=args.allow_private,
        cache=not args.no_cache,
    )
    feeds = discover_feeds(dom.parse(response.text), response.url)
    if args.json:
        print(json.dumps(feeds, ensure_ascii=False, indent=2))
    elif feeds:
        for entry in feeds:
            label = f" — {entry['title']}" if entry["title"] else ""
            print(f"{entry['url']}{label}")
    else:
        print("Aucun flux déclaré par cette page.", file=sys.stderr)
    return 0 if feeds else 3


def cmd_serve(args):
    from .server import serve

    serve(
        host=args.host,
        port=args.port,
        base_url=args.base_url,
        ttl=args.ttl,
        respect_robots=not args.no_robots,
        allow_private=args.allow_private,
        allow_hosts=args.allow_host,
        recipes_path=args.recipes,
        max_limit=args.max_limit,
    )
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="cloclo",
        description="Génère un flux RSS/Atom/JSON depuis n'importe quelle page web.",
    )
    parser.add_argument("--version", action="version", version=f"cloclo {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    feed_parser = sub.add_parser("feed", help="produire le flux d'une page")
    feed_parser.add_argument("url")
    feed_parser.add_argument(
        "-f", "--format", choices=("rss", "atom", "json"), default="rss"
    )
    feed_parser.add_argument("-o", "--output", default="-", help="fichier de sortie")
    feed_parser.add_argument(
        "--full", action="store_true", help="télécharger chaque article en entier"
    )
    feed_parser.add_argument(
        "--full-limit", type=int, default=5, help="articles téléchargés en entier (5)"
    )
    feed_parser.add_argument("--feed-title", default="", help="titre du flux")
    feed_parser.add_argument("--self-url", default="", help="URL publique du flux")
    _add_extraction_flags(feed_parser)
    _add_network_flags(feed_parser)
    feed_parser.set_defaults(func=cmd_feed)

    preview_parser = sub.add_parser(
        "preview", help="inspecter la détection sans produire de flux"
    )
    preview_parser.add_argument("url")
    preview_parser.add_argument("--json", action="store_true")
    _add_extraction_flags(preview_parser)
    _add_network_flags(preview_parser)
    preview_parser.set_defaults(func=cmd_preview)

    discover_parser = sub.add_parser(
        "discover", help="lister les flux déjà publiés par la page"
    )
    discover_parser.add_argument("url")
    discover_parser.add_argument("--json", action="store_true")
    _add_network_flags(discover_parser)
    discover_parser.set_defaults(func=cmd_discover)

    serve_parser = sub.add_parser("serve", help="lancer le service HTTP")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("-p", "--port", type=int, default=8787)
    serve_parser.add_argument(
        "--base-url", default="", help="URL publique du service (liens self)"
    )
    serve_parser.add_argument(
        "--ttl", type=float, default=900.0, help="durée du cache en secondes (900)"
    )
    serve_parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="restreindre le service à ces domaines (répétable)",
    )
    serve_parser.add_argument(
        "--max-limit", type=int, default=100, help="plafond du paramètre limit (100)"
    )
    serve_parser.add_argument("--recipes", default="", help="fichier de recettes")
    serve_parser.add_argument("--no-robots", action="store_true")
    serve_parser.add_argument("--allow-private", action="store_true")
    serve_parser.set_defaults(func=cmd_serve)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FetchError as error:
        print(f"Erreur : {error}", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"Erreur : {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
