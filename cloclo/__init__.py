"""Cloclo — générateur de flux RSS pour n'importe quelle page au contenu gratuit.

    >>> from cloclo import build, Options, Settings
    >>> result = build("https://exemple.fr/actualites", Options(limit=10))
    >>> print(result.body)

Les sous-modules restent accessibles (`cloclo.extract`, `cloclo.feed`...) ;
c'est pourquoi aucune fonction n'est réexportée sous le nom d'un module.
"""

__version__ = "1.0.0"

from .extract import Extraction, Item, Options, PageInfo  # noqa: F401
from .feed import FeedMeta, render  # noqa: F401
from .generator import Result, Settings, build, collect  # noqa: F401

__all__ = [
    "Item",
    "Options",
    "Extraction",
    "PageInfo",
    "Settings",
    "Result",
    "FeedMeta",
    "render",
    "build",
    "collect",
    "__version__",
]
