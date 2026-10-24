# Cloclo, consignes pour Claude

## Objet

Cloclo fabrique des flux RSS pour des pages web qui n'en proposent pas. Un flux par page suivie, lu dans Feeder, Inoreader ou NetNewsWire. Le propriétaire est Thomas (thomasstamm75). Il n'est pas développeur : explique chaque changement en français simple, avec ce qu'il verra changer côté agrégateur ou console.

## Architecture

- `feeds.yaml` : liste des pages suivies. Seul fichier à modifier pour ajouter ou retirer une source.
- `generate.py` : moteur. Lit `feeds.yaml`, visite chaque page, écrit `docs/<slug>.xml` et `state/<slug>.json`.
- `.github/workflows/flux.yml` : régénération toutes les trois heures (minute 17), à chaque push de `feeds.yaml` ou `generate.py` sur main, et à la demande.
- `docs/index.html` : console Fluxothèque, page d'accueil du site Pages (https://thomasstamm75.github.io/Cloclo/). Seul fichier écrit à la main dans `docs/`.
- `docs/*.xml` et `state/*.json` : écrits par le robot cloclo-bot. Ne jamais les modifier à la main.

## Commandes

```
# Dépendances (les mêmes que dans le workflow)
pip install requests beautifulsoup4 lxml pyyaml

# Vérifier que feeds.yaml reste un YAML valide
python3 -c "import yaml; yaml.safe_load(open('feeds.yaml'))"

# Tester une seule source sans toucher au reste
python3 generate.py --base-url https://thomasstamm75.github.io/Cloclo/ --sources /tmp/essai.yaml

# Annuler les fichiers produits par un essai local avant tout commit
git restore docs state && git clean -fd docs state
```

Un essai local écrit dans `docs/` et `state/`. Ces fichiers ne partent jamais dans un commit manuel. Le workflow les régénère.

## Schéma de feeds.yaml

Liste racine d'entrées. `url` et `title` obligatoires. Clés facultatives :

- `item` : sélecteur CSS d'un article. Sans lui, le moteur cherche les balises `article`, puis le groupe de blocs frères le plus fourni en liens.
- `limit` : nombre d'articles gardés, 30 par défaut.
- `include` et `exclude` : listes de mots cherchés dans le titre et le résumé.
- `full_text` : `true` pour récupérer le texte intégral de chaque article.
- `slug` : nom du fichier `.xml`. Sans lui, le slug dérive du titre.

## Règles à ne jamais enfreindre

1. L'adresse d'un flux dépend du slug, donc du titre. Changer un titre casse l'abonnement dans l'agrégateur. Avant de renommer, ajoute `slug` avec l'ancienne valeur.
2. Deux entrées ne partagent jamais le même slug. Vérifie avant chaque ajout.
3. Le `guid` d'un article reste son lien. Les agrégateurs s'en servent pour repérer les doublons.
4. Garde le repli sur `last_good_items` : un site injoignable reconduit le dernier flux connu au lieu de le vider.
5. Garde la politesse envers les sites : une seconde entre deux requêtes et l'agent `Cloclo-RSS/1.0` qui identifie le projet.
6. Le dépôt est public. Aucun jeton, mot de passe ou adresse personnelle dans le code, les commits ou les messages. Le jeton GitHub se colle dans la console en ligne, jamais dans le chat.
7. Aucune nouvelle dépendance sans justification écrite. Toute dépendance ajoutée entre aussi dans la ligne `pip install` du workflow.

## Méthode de travail

1. Avant de coder, écris en quelques lignes le besoin et un critère de réussite vérifiable. Exemple : « le flux france-info-monde.xml contient au moins dix articles datés ».
2. Pour une source qui ne remonte rien, essaie dans cet ordre : un flux RSS existant sur le site, puis un sélecteur `item`, puis une modification du moteur.
3. Teste sur la seule source concernée avec `--sources`, puis vérifie le XML produit.
4. Choisis le changement le plus simple. Pas d'abstraction pour un besoin futur.
5. Un commit par changement. Préfixe `Cloclo :` pour le moteur, le workflow et la documentation. Le préfixe `Fluxothèque :` reste réservé aux commits de la console.

## Limites connues

- Le moteur lit le HTML brut avec requests. Une page dont la liste s'affiche en JavaScript sort vide. Cherche alors un flux natif ou une page alternative du site.
- Le fuseau `PARIS` vaut UTC+1 toute l'année. En heure d'été, les dates sans heure explicite sont décalées d'une heure.
- Un site qui répond 304 reconduit le flux sans relire la page.

## Style des textes

Code, messages, commentaires et documentation en français. Phrases courtes, voix active. Pas de tiret long, pas de point-virgule.
