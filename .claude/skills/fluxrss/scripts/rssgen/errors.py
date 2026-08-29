"""Exceptions communes, dans un module sans dépendance externe.

Les isoler ici permet à la ligne de commande de les attraper sans importer
« requests » ni « PyYAML » : travailler sur un fichier HTML déjà enregistré ne
demande alors que beautifulsoup4.
"""


class FetchError(RuntimeError):
    """La page n'a pas pu être récupérée après les tentatives prévues."""


class ConfigError(ValueError):
    """La configuration est inutilisable en l'état."""
