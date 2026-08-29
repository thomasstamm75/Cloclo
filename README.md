# Cloclo

Générateur personnel de flux RSS. Il suit des pages web qui ne proposent pas de flux et publie un fichier RSS par page, lisible dans Feeder, Inoreader ou NetNewsWire.

## Fonctionnement

Le fichier feeds.yaml liste les pages suivies. Le workflow GitHub Actions relit ces pages toutes les trois heures, à chaque modification de feeds.yaml et à la demande. Il écrit les flux dans docs/ et sa mémoire dans state/. GitHub Pages sert le contenu de docs/.

La console de pilotage est la page d'accueil du site. Elle liste les flux publiés avec leur adresse à coller dans un agrégateur, ajoute des sources et relance la génération. Elle demande un jeton fine-grained limité à ce dépôt, avec Contents en lecture et écriture, Actions en lecture et écriture, Pages en lecture.

## Mise en route

1. Dans Settings puis Pages, choisir Deploy from a branch, branche main, dossier /docs.
2. Ouvrir l'adresse du site et suivre la console.

## Ajouter une source à la main

Ajouter une entrée à feeds.yaml, en suivant le modèle commenté en tête de fichier. Le commit déclenche la régénération.
