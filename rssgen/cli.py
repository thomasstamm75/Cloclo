"""Interface en ligne de commande de rssgen."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .builder import Builder
from .config import Config, ConfigError, append_feed, load_config
from .detect import inspect as inspect_page
from .extract import Rules, extract_articles
from .fetch import Fetcher, FetchError
from .rss import build_rss
from .util import domain_of, slugify

DEFAULT_CONFIG = "feeds.yaml"
DEFAULT_OUTPUT = "docs"
DEFAULT_STATE = "state"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return args.handler(args)
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 2
    except FetchError as exc:
        print(f"Réseau : {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\nInterrompu.", file=sys.stderr)
        return 130


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rssgen",
        description="Fabrique des flux RSS pour les pages web qui n'en proposent pas.")
    parser.add_argument("--version", action="version", version=f"rssgen {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text, description=help_text)
        return sub

    inspect_cmd = add("inspect", "Analyse une page : flux existant, structure, sélecteurs.")
    inspect_cmd.add_argument("url")
    inspect_cmd.add_argument("--no-probe", action="store_true",
                             help="ne pas tester les adresses de flux conventionnelles")
    inspect_cmd.set_defaults(handler=cmd_inspect)

    preview_cmd = add("preview", "Affiche le flux d'une page sans toucher à la configuration.")
    preview_cmd.add_argument("url")
    preview_cmd.add_argument("--item", default="", help="sélecteur CSS d'un article")
    preview_cmd.add_argument("--title-sel", default="")
    preview_cmd.add_argument("--link-sel", default="")
    preview_cmd.add_argument("--date-sel", default="")
    preview_cmd.add_argument("--summary-sel", default="")
    preview_cmd.add_argument("--limit", type=int, default=15)
    preview_cmd.add_argument("--xml", action="store_true", help="sortir le XML complet")
    preview_cmd.set_defaults(handler=cmd_preview)

    add_cmd = add("add", "Ajoute une page à feeds.yaml.")
    add_cmd.add_argument("url")
    add_cmd.add_argument("--id", default="", help="identifiant du flux (sinon déduit de l'URL)")
    add_cmd.add_argument("--title", default="", help="titre affiché dans l'agrégateur")
    add_cmd.add_argument("--item", default="", help="sélecteur CSS d'un article")
    add_cmd.add_argument("--full-text", action="store_true",
                         help="récupérer le texte intégral de chaque article")
    add_cmd.add_argument("--max-items", type=int, default=25)
    add_cmd.add_argument("--include", default="", help="ne garder que les titres correspondants")
    add_cmd.add_argument("--exclude", default="", help="écarter les titres correspondants")
    add_cmd.add_argument("--force", action="store_true",
                         help="ajouter même si le site publie déjà un flux")
    add_cmd.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    add_cmd.set_defaults(handler=cmd_add)

    build_cmd = add("build", "Génère tous les flux configurés.")
    build_cmd.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    build_cmd.add_argument("-o", "--output", default=DEFAULT_OUTPUT)
    build_cmd.add_argument("--state", default=DEFAULT_STATE)
    build_cmd.add_argument("--cache", default=".cache")
    build_cmd.add_argument("--only", nargs="*", default=None,
                           help="ne générer que ces identifiants de flux")
    build_cmd.add_argument("--fail-fast", action="store_true",
                           help="code de sortie non nul dès qu'un flux échoue")
    build_cmd.set_defaults(handler=cmd_build)

    list_cmd = add("list", "Liste les flux configurés.")
    list_cmd.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    list_cmd.set_defaults(handler=cmd_list)

    check_cmd = add("check", "Vérifie la configuration sans rien télécharger.")
    check_cmd.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    check_cmd.set_defaults(handler=cmd_check)

    serve_cmd = add("serve", "Sert les flux à la demande sur le réseau local.")
    serve_cmd.add_argument("-c", "--config", default=DEFAULT_CONFIG)
    serve_cmd.add_argument("-p", "--port", type=int, default=8777)
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--state", default=DEFAULT_STATE)
    serve_cmd.add_argument("--open-urls", action="store_true",
                           help="autoriser /feed?url=… pour n'importe quelle page")
    serve_cmd.set_defaults(handler=cmd_serve)

    return parser


# ------------------------------------------------------------------ commandes
def cmd_inspect(args) -> int:
    fetcher = Fetcher(cache_dir=None)
    report = inspect_page(args.url, fetcher, probe=not args.no_probe)

    print(f"Page       : {report['url']}")
    print(f"Titre      : {report['site_title']}")

    if report["existing_feeds"]:
        print("\nCe site publie déjà un flux — inutile d'en fabriquer un :")
        for feed in report["existing_feeds"]:
            print(f"  → {feed['url']}")
            print(f"    ({feed['title']}, {feed['source']})")
        print("\nCollez cette adresse directement dans Feeder.")
        print("Pour générer un flux malgré tout : rssgen add <url> --force")
        return 0

    print("\nAucun flux existant détecté : un flux peut être généré.")
    structure = report["structure"]
    if not structure["found"]:
        print("\nAucune liste d'articles reconnue automatiquement.")
        print("Indiquez un sélecteur CSS à la main, par exemple :")
        print("  rssgen preview <url> --item 'div.article'")
        return 1

    print(f"\nStructure détectée : {structure['count']} articles "
          f"(sélecteur « {structure['item_selector']} »)")
    for sample in structure["sample"]:
        print(f"  • {sample}")

    articles = extract_articles(report["html"], report["url"], limit=5)
    if articles:
        print("\nAperçu du flux :")
        for article in articles:
            date = article.published.strftime("%d/%m/%Y") if article.published else "sans date"
            print(f"  [{date}] {article.title}")

    print("\nÀ ajouter dans feeds.yaml :")
    print(_yaml_block(report, structure))
    print(f"Ou directement : rssgen add {report['url']}")
    return 0


def _yaml_block(report: dict, structure: dict) -> str:
    return (
        f"\n  - id: {report['suggested_id']}\n"
        f"    title: \"{report['site_title'][:70]}\"\n"
        f"    url: {report['url']}\n"
        f"    selectors:\n"
        f"      item: \"{structure['item_selector']}\"\n"
    )


def cmd_preview(args) -> int:
    fetcher = Fetcher(cache_dir=None)
    reply = fetcher.get(args.url, use_cache=False)
    rules = Rules(item=args.item, title=args.title_sel, link=args.link_sel,
                  date=args.date_sel, summary=args.summary_sel)
    articles = extract_articles(reply.text, reply.url, rules, limit=args.limit)
    if not articles:
        print("Aucun article trouvé. Essayez « rssgen inspect » pour repérer un sélecteur.",
              file=sys.stderr)
        return 1

    if args.xml:
        print(build_rss(articles, title=domain_of(reply.url), link=reply.url), end="")
        return 0

    print(f"{len(articles)} article(s) depuis {reply.url}\n")
    for article in articles:
        date = article.published.strftime("%d/%m/%Y %H:%M") if article.published else "sans date"
        print(f"[{date}] {article.title}")
        print(f"    {article.link}")
        if article.summary:
            print(f"    {article.summary[:110]}")
        print()
    return 0


def cmd_add(args) -> int:
    fetcher = Fetcher(cache_dir=None)
    report = inspect_page(args.url, fetcher, probe=not args.force)

    if report["existing_feeds"] and not args.force:
        print("Ce site publie déjà un flux :", file=sys.stderr)
        for feed in report["existing_feeds"]:
            print(f"  → {feed['url']}", file=sys.stderr)
        print("Ajoutez-le tel quel dans Feeder, ou relancez avec --force.",
              file=sys.stderr)
        return 1

    item_selector = args.item or report["structure"].get("item_selector", "")
    rules = Rules(item=item_selector)
    articles = extract_articles(report["html"], report["url"], rules, limit=args.max_items)
    if not articles:
        print("Aucun article extrait : préciser --item avec un sélecteur CSS.",
              file=sys.stderr)
        return 1

    feed_id = slugify(args.id or report["url"])
    config_path = Path(args.config)
    if config_path.exists():
        existing = load_config(config_path)
        if existing.get(feed_id):
            print(f"Le flux « {feed_id} » existe déjà dans {config_path}.", file=sys.stderr)
            return 1

    entry: dict = {
        "id": feed_id,
        "title": args.title or report["site_title"][:80],
        "url": report["url"],
    }
    if item_selector:
        entry["selectors"] = {"item": item_selector}
    if args.max_items != 25:
        entry["max_items"] = args.max_items
    if args.full_text:
        entry["full_text"] = True
    if args.include:
        entry["include"] = args.include
    if args.exclude:
        entry["exclude"] = args.exclude

    append_feed(config_path, entry)
    print(f"Flux « {feed_id} » ajouté à {config_path} ({len(articles)} articles détectés).")
    print(f"Générez-le avec : rssgen build --only {feed_id}")
    return 0


def cmd_build(args) -> int:
    config = load_config(args.config)
    if not config.active:
        print("Aucun flux actif dans la configuration.", file=sys.stderr)
        return 1

    builder = Builder(config, output_dir=args.output, state_dir=args.state,
                      cache_dir=args.cache or None)
    results = builder.build_all(only=args.only)

    for result in results:
        print(result.status_line)

    failures = [r for r in results if not r.ok]
    total = sum(r.items for r in results if r.ok)
    new = sum(r.new_items for r in results if r.ok)
    print(f"\n{len(results) - len(failures)}/{len(results)} flux générés — "
          f"{total} articles, {new} nouveau(x).")
    print(f"Sortie : {Path(args.output).resolve()}")

    if failures and args.fail_fast:
        return 1
    # Un site en panne ne doit pas faire échouer toute la publication ; on ne
    # signale une erreur que si absolument rien n'a été produit.
    return 0 if len(failures) < len(results) else 1


def cmd_list(args) -> int:
    config = load_config(args.config)
    if not config.feeds:
        print("Aucun flux configuré. Ajoutez-en un avec « rssgen add <url> ».")
        return 0
    for feed in config.feeds:
        state = " " if feed.enabled else "désactivé"
        selector = feed.selectors.item or "détection auto"
        print(f"{feed.id:<28} {state:<10} {feed.url}")
        print(f"{'':<28} {'':<10} sélecteur : {selector}, {feed.max_items} articles max")
    return 0


def cmd_check(args) -> int:
    config = load_config(args.config)
    print(f"{args.config} : {len(config.feeds)} flux "
          f"({len(config.active)} actifs), configuration valide.")
    if not config.site.base_url:
        print("Note : « site.base_url » est vide — les flux ne porteront pas "
              "leur adresse canonique (atom:link self).")
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    config = _load_or_empty(args.config)
    serve(config, host=args.host, port=args.port, state_dir=args.state,
          open_urls=args.open_urls)
    return 0


def _load_or_empty(path: str) -> Config:
    try:
        return load_config(path)
    except ConfigError:
        return Config()


if __name__ == "__main__":
    sys.exit(main())
