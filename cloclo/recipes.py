"""Recettes : sélecteurs mémorisés par site, pour les pages que l'heuristique rate."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

BUNDLED = Path(__file__).with_name("recipes.json")
USER_PATHS = [
    Path(os.environ.get("CLOCLO_RECIPES", "")) if os.environ.get("CLOCLO_RECIPES") else None,
    Path.home() / ".config" / "cloclo" / "recipes.json",
    Path.cwd() / "recipes.json",
]

FIELDS = ("item", "title", "link", "date", "summary", "author", "image", "category")


def _load_file(path):
    if not path or not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("recipes", [])
    return [r for r in payload if isinstance(r, dict) and r.get("match")]


def load(extra_path=None):
    """Recettes utilisateur d'abord : elles priment sur celles fournies."""
    recipes = []
    for path in [Path(extra_path) if extra_path else None] + USER_PATHS:
        recipes.extend(_load_file(path))
    recipes.extend(_load_file(BUNDLED))
    return recipes


def find(url, extra_path=None, recipes=None):
    """Première recette dont le motif `match` correspond à l'URL."""
    for recipe in recipes if recipes is not None else load(extra_path):
        pattern = recipe["match"]
        if pattern.startswith("re:"):
            if re.search(pattern[3:], url):
                return recipe
        elif pattern.lower() in url.lower():
            return recipe
    return None


def apply(recipe, options):
    """Complète les champs vides des options avec ceux de la recette."""
    if not recipe:
        return options
    for field in FIELDS:
        if not getattr(options, field, "") and recipe.get(field):
            setattr(options, field, recipe[field])
    if recipe.get("include_paid") is not None:
        options.include_paid = bool(recipe["include_paid"])
    if recipe.get("strategy"):
        options.strategy = recipe["strategy"]
    return options
