# Cloclo — générateur de flux RSS

Fabrique un flux RSS pour les pages web qui n'en proposent pas, afin de les
suivre dans un agrégateur comme **Feeder**.

Vous donnez l'adresse d'une page qui liste des articles, `rssgen` en déduit les
publications et produit un fichier `.xml` conforme à RSS 2.0. Publié par GitHub
Pages, ce fichier a une adresse stable que Feeder interroge comme n'importe
quel flux officiel.

## Installation

```bash
git clone https://github.com/thomasstamm75/Cloclo.git
cd Cloclo
pip install -r requirements.txt
```

Python 3.10 ou plus récent. Pour disposer de la commande `rssgen` partout :
`pip install -e .` (sinon, remplacez `rssgen` par `python -m rssgen` dans les
exemples).

## Prise en main

### 1. Analyser la page

```bash
rssgen inspect https://exemple.fr/actualites
```

La commande commence par vérifier que le site ne publie pas déjà un flux, y
compris à une adresse non annoncée sur la page. Si c'est le cas, elle vous donne
l'adresse : inutile d'en fabriquer un, collez-la dans Feeder.

Sinon, elle affiche la liste d'articles repérée, un aperçu du flux et le bloc de
configuration correspondant.

### 2. Ajouter la page

```bash
rssgen add https://exemple.fr/actualites --title "Exemple — actualités"
```

L'entrée est écrite dans `feeds.yaml`. Options utiles : `--item` pour imposer un
sélecteur CSS, `--full-text` pour le texte intégral, `--include` / `--exclude`
pour filtrer par mots-clés.

### 3. Générer les flux

```bash
rssgen build
```

Écrit un `docs/feeds/<id>.xml` par flux, une page d'accueil `docs/index.html`
qui liste les adresses à copier, et `docs/feeds.opml` pour tout importer en une
fois dans Feeder.

## Publication automatique

Le dépôt contient un workflow qui régénère les flux toutes les trois heures et
les publie sur GitHub Pages.

1. Renseignez `site.base_url` dans `feeds.yaml` avec votre adresse Pages
   (`https://<utilisateur>.github.io/<dépôt>`).
2. Dans **Settings → Pages**, choisissez la source **GitHub Actions**.
3. Poussez sur `main`. Les flux seront servis à
   `https://<utilisateur>.github.io/<dépôt>/feeds/<id>.xml`.

Le workflow réenregistre `docs/` et `state/` dans le dépôt à chaque passage.

## Ajouter les flux dans Feeder

**Tout d'un coup** — ouvrez `https://<votre-adresse>/feeds.opml`, puis dans
Feeder : menu ⋮ → *Importer des flux* → sélectionnez le fichier OPML.

**Un par un** — dans Feeder, bouton *+*, puis collez l'adresse
`https://<votre-adresse>/feeds/<id>.xml`.

## Comment les articles sont repérés

Trois stratégies, essayées dans cet ordre :

1. **Les sélecteurs CSS** de `feeds.yaml`, quand vous en donnez ;
2. **Les données structurées JSON-LD** (schema.org), publiées par beaucoup de
   sites d'actualité et de blogs ;
3. **La détection automatique** : après avoir écarté navigation, barre latérale
   et pied de page, `rssgen` regroupe les liens qui partagent la même structure
   HTML et retient la plus grande famille cohérente.

Pour chaque article, le titre, le lien, la date, le résumé, l'auteur et l'image
sont extraits du bloc correspondant.

### Les dates

C'est le point délicat des flux fabriqués à la main. Beaucoup de pages
n'affichent aucune date : leur donner l'heure courante à chaque génération
ferait remonter en permanence les mêmes articles en haut de Feeder.

`rssgen` enregistre donc dans `state/<id>.json` la date à laquelle chaque
article est apparu pour la première fois, et s'y tient. Une date lue sur la page
reste toujours prioritaire. C'est pourquoi le dossier `state/` est versionné :
il porte la mémoire des flux.

Les formats français sont reconnus (`12 janvier 2026`, `3 mars 2025 à 14h30`,
`01/02/2026`, `il y a 3 heures`), au même titre que l'ISO 8601 et le RFC 822.

## Configuration

`feeds.yaml` contient des exemples commentés pour chaque cas. Les clés d'un
flux :

| Clé | Rôle |
| --- | --- |
| `id` | identifiant, donne son nom au fichier `.xml` |
| `url` | page à suivre |
| `title` | titre affiché dans l'agrégateur |
| `selectors` | `item`, `title`, `link`, `date`, `summary`, `author`, `image`, `content` |
| `max_items` | nombre d'articles conservés (25 par défaut) |
| `full_text` | télécharge chaque article pour insérer son texte intégral |
| `include` / `exclude` | expressions régulières filtrant les titres et résumés |
| `enabled` | `false` met le flux en pause sans le supprimer |

Les valeurs communes se placent sous `defaults:`.

## Quand la détection automatique se trompe

Ouvrez la page dans un navigateur, inspectez un article, repérez la balise qui
l'entoure entièrement, puis testez :

```bash
rssgen preview https://exemple.fr/actualites --item "article.card"
```

Ajustez jusqu'à obtenir la bonne liste, puis reportez le sélecteur dans
`feeds.yaml`. Un sélecteur explicite est aussi plus stable dans le temps qu'une
détection automatique.

## Mode serveur

Pour générer les flux à la demande plutôt que de les publier :

```bash
rssgen serve --port 8777 --open-urls
```

- `http://localhost:8777/feeds/<id>.xml` — un flux de `feeds.yaml` ;
- `http://localhost:8777/feed?url=https://exemple.fr/actus` — n'importe quelle
  page, sans configuration préalable.

Le mode `--open-urls` fabrique un flux pour toute adresse qu'on lui passe :
ne l'exposez pas sur un réseau public, il servirait de relais à des requêtes
sortantes arbitraires.

## Commandes

| Commande | Rôle |
| --- | --- |
| `rssgen inspect <url>` | cherche un flux existant, propose des sélecteurs |
| `rssgen preview <url>` | affiche le flux sans toucher à la configuration |
| `rssgen add <url>` | ajoute la page à `feeds.yaml` |
| `rssgen build` | génère tous les flux dans `docs/` |
| `rssgen list` | liste les flux configurés |
| `rssgen check` | valide `feeds.yaml` sans rien télécharger |
| `rssgen serve` | sert les flux à la demande |

## Bon voisinage

L'outil s'identifie clairement, espace ses requêtes (`request_delay`) et
n'interroge une page qu'une fois par génération, en s'appuyant sur les en-têtes
`ETag` et `Last-Modified` pour éviter les téléchargements inutiles. Si un site
devient injoignable, le dernier contenu connu est réutilisé plutôt que de
publier un flux vide qui viderait la liste dans Feeder.

Vérifiez les conditions d'utilisation des sites que vous suivez, et gardez un
rythme de relevé raisonnable.

## Tests

```bash
python -m pytest -q
```

## Licence

MIT.
