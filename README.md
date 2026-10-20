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

## Veille bourse

Le dossier bourse/ produit chaque matin de semaine, vers 7h30, un rapport de recommandations sur les actions européennes, envoyé par courriel. Le workflow lit l'actualité économique et géopolitique (flux listés dans bourse/config.yaml), charge les cours de l'univers suivi, demande l'analyse à Claude, puis inscrit chaque signal dans bourse/journal.jsonl. Le rapport du lendemain fait le bilan de ces signaux : objectif atteint, stop touché ou échéance, avec l'écart par rapport à l'Euro Stoxx 50.

Mise en route, dans Settings puis Secrets and variables puis Actions, créer quatre secrets :

1. ANTHROPIC_API_KEY : clé créée sur console.anthropic.com.
2. GMAIL_USER : l'adresse Gmail qui envoie le rapport.
3. GMAIL_APP_PASSWORD : un mot de passe d'application Gmail (Compte Google, Sécurité, Validation en deux étapes, Mots de passe des applications).
4. DESTINATAIRE : l'adresse qui reçoit le rapport. Sans ce secret, le rapport part vers GMAIL_USER.

Pour tester sans attendre le lendemain : onglet Actions, workflow Veille bourse, Run workflow.

Pour modifier les actions suivies ou les sources, éditer bourse/config.yaml. Pour un aperçu local sans réseau : python3 bourse/veille.py --demo.
