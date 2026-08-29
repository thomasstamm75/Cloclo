# Héberger le flux et le tenir à jour

Un agrégateur interroge une adresse à intervalles réguliers. Le flux doit donc
vivre à une URL stable, et être réécrit régulièrement pour refléter les
nouveautés du site suivi.

## Voie recommandée : le dépôt Cloclo de Thomas

Le dépôt <https://github.com/thomasstamm75/Cloclo> contient le générateur et
tout le nécessaire de publication. C'est la voie à privilégier : la
régénération et la mise en ligne y sont déjà automatisées.

### Ajouter une page

```bash
git clone https://github.com/thomasstamm75/Cloclo.git
cd Cloclo && pip install -r requirements.txt
python -m rssgen add https://exemple.fr/actualites --title "Exemple — actualités"
python -m rssgen build
```

`add` écrit l'entrée dans `feeds.yaml` ; `build` produit `docs/feeds/<id>.xml`.
Il reste à valider le résultat, puis à pousser :

```bash
git add feeds.yaml docs state && git commit -m "Ajoute le flux Exemple" && git push
```

### Ce que fait le dépôt ensuite

Un workflow GitHub Actions régénère tous les flux toutes les trois heures et
les publie sur GitHub Pages. Les adresses sont de la forme :

```
https://thomasstamm75.github.io/Cloclo/feeds/<id>.xml
```

La page d'accueil `https://thomasstamm75.github.io/Cloclo/` liste tous les flux
avec leur adresse à copier, et `feeds.opml` permet de tout importer d'un coup
dans Feeder.

Deux réglages à faire une seule fois, si ce n'est pas déjà le cas :
`site.base_url` dans `feeds.yaml`, et **Settings → Pages → Source : GitHub
Actions** sur le dépôt.

## Réglages de feeds.yaml

Une entrée complète :

```yaml
feeds:
  - id: exemple-actus
    title: "Exemple — actualités"
    url: https://exemple.fr/actualites
    selectors:
      item: "article.post"        # le bloc entourant un article
      title: "h2 a"
      date: "time"
      summary: "p.chapo"
    max_items: 30
    include: "budget|finances"    # ne garder que ces sujets
    exclude: "publicité"
    full_text: true               # insérer le corps de chaque article
    full_text_selector: "div.article-body"
    enabled: true
```

Les valeurs communes à tous les flux se placent sous `defaults:`.

Le texte intégral télécharge chaque article une fois, puis garde le résultat en
cache. C'est agréable pour lire hors ligne, mais cela multiplie les requêtes
vers le site : à réserver aux sources qui publient peu.

## Sans le dépôt

N'importe quel hébergement de fichiers statiques servi en HTTP convient. Deux
conditions seulement :

- l'adresse du `.xml` ne change pas — Feeder identifie un flux par son URL ;
- le fichier est réécrit régulièrement, sinon le flux se fige et Thomas croira
  que le site ne publie plus.

Une tâche planifiée (`cron`, une action GitHub, un service systemd) qui relance
`python3 -m rssgen make <url> -o <fichier> --state <dossier>` suffit. Conservez
le dossier d'état entre deux exécutions : il porte les dates de première
apparition des articles.

## Ajouter le flux dans Feeder

**Un flux** : bouton **+**, coller l'adresse du `.xml`.

**Plusieurs d'un coup** : menu ⋮ → *Importer des flux* → choisir le fichier
OPML (`feeds.opml` à la racine du site publié).

Feeder relève les flux selon sa propre fréquence, réglable dans ses paramètres.
Inutile de la fixer plus courte que le rythme de régénération du flux.
