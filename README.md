# Cloclo

Un flux RSS pour n'importe quelle page au contenu gratuit.

Vous suivez un site qui ne publie pas de flux — un blog, une rubrique
« actualités », une page de communiqués, un fil de dépêches. Cloclo lit la page,
y repère la liste des entrées et la sert en RSS 2.0, Atom 1.0 ou JSON Feed 1.1.
Vous collez l'adresse dans votre lecteur, et c'est fini.

- **Aucune dépendance** : Python 3.9+ et sa bibliothèque standard, rien d'autre.
- **Détection automatique**, avec repli sur des sélecteurs CSS quand elle se trompe.
- **Contenu gratuit uniquement** : les entrées réservées aux abonnés sont écartées
  par défaut (voir [Contenu gratuit](#contenu-gratuit)).
- **En ligne de commande ou en service HTTP**, avec cache et page d'assistance.

## Installation

```bash
git clone https://github.com/thomasstamm75/cloclo.git
cd cloclo
pip install .          # fournit la commande « cloclo »
```

Sans installation : `python3 -m cloclo …` depuis le dossier du dépôt.

## Prise en main

```bash
# Voir ce que Cloclo détecte, avant de s'abonner
cloclo preview https://exemple.fr/actualites

# Écrire le flux
cloclo feed https://exemple.fr/actualites -o actualites.xml

# Le site publie-t-il déjà un flux ? (inutile de le régénérer)
cloclo discover https://exemple.fr/actualites

# Servir des flux à la demande
cloclo serve --port 8787
```

`preview` affiche les entrées trouvées, la stratégie employée et le sélecteur
retenu — c'est la commande à lancer en premier quand un résultat surprend :

```
Page      : Le Carnet — journal de bord
Stratégie : heuristic
Sélecteurs: {'item': 'article.post', 'score': 84.8}

3 entrée(s) :

  1. Relire Les Mots et les Choses
     https://carnet.example/2026/08/relire-les-mots-et-les-choses/
     2026-08-21T07:15:00Z · Claire Menou · philosophie
```

## Comment la détection fonctionne

Quatre stratégies, essayées dans l'ordre :

| Stratégie | Déclenchement | Ce qu'elle fait |
|---|---|---|
| `selectors` | vous passez `--item` (ou une recette correspond) | applique vos sélecteurs CSS |
| `feed` | la page *est* un RSS/Atom | le normalise et le réémet |
| `jsonld` | la page contient du `schema.org` en JSON-LD | lit `ItemList`, `Article`, `NewsArticle`… |
| `heuristic` | sinon | repère les fratries répétées contenant un lien titré |

L'heuristique note chaque série de blocs semblables : nombre de blocs, part de
blocs porteurs d'un lien et d'un titre, présence de dates, longueur moyenne du
texte, mots de la classe CSS (`post`, `card`, `article`… contre `nav`, `footer`,
`sidebar`, `related`…), et position dans la page. La mieux notée gagne ; les
autres sont affichées par `preview` pour que vous puissiez en choisir une autre.

Pour chaque entrée, Cloclo extrait le titre, le lien (absolutisé, débarrassé des
paramètres de traçage `utm_*`, `fbclid`…), la date, le résumé, l'auteur, l'image
et les mots-clés. Les dates sont comprises en français comme en anglais, en ISO,
en `jj/mm/aaaa`, en horodatage Unix et en relatif (« il y a 3 jours »).

## Quand la détection se trompe

Reprenez la main avec des sélecteurs CSS. Chacun accepte le suffixe `@attribut`
(`@href`, `@datetime`, `@content`, `@src`…) ; sans suffixe, c'est le texte.

```bash
cloclo feed https://exemple.fr/actualites \
  --item ".liste-actus > article" \
  --title "h2" \
  --link "h2 a@href" \
  --date "time@datetime" \
  --summary ".chapo" \
  --author ".signature" \
  --image "img@src"
```

Seul `--item` est indispensable : les autres champs restent déduits
automatiquement à l'intérieur de chaque bloc si vous ne les précisez pas.

Sélecteurs pris en charge : balise, `.classe`, `#id`, `[attr]`, `[attr=valeur]`,
`[attr^=`, `$=`, `*=`, `~=`, `|=`, descendance, `>`, `+`, `~`, groupes séparés
par des virgules, `:first-child`, `:last-child`, `:nth-child(n)`,
`:nth-of-type(n)`, `:not(…)`, `:has(…)`, `:empty`.

### Recettes par site

Pour ne pas retaper vos sélecteurs, enregistrez-les dans
`~/.config/cloclo/recipes.json` (ou passez `--recipes fichier.json`) :

```json
{
  "recipes": [
    {
      "match": "exemple.fr/actualites",
      "item": ".liste-actus > article",
      "link": "h2 a@href",
      "date": "time@datetime"
    },
    { "match": "re:^https://[a-z]+\\.presse\\.fr/", "item": ".depeche" }
  ]
}
```

`match` est une sous-chaîne de l'URL, ou une expression régulière préfixée par
`re:`. La première recette qui correspond s'applique ; vos options de ligne de
commande restent prioritaires sur elle.

## Contenu gratuit

Cloclo est fait pour les pages en accès libre et s'y tient : une entrée repérée
comme réservée aux abonnés est **exclue du flux**. Sont considérés comme des
signaux d'accès restreint :

- `isAccessibleForFree: false` en JSON-LD, sur l'entrée ou sur la page ;
- `<meta property="article:content_tier" content="locked">` ;
- une classe ou un attribut contenant `premium`, `paywall`, `abonné`,
  `subscriber`, `payant`, `locked`… ;
- une mention visible du type « Réservé aux abonnés », « Premium », « 🔒 ».

`--include-paid` (ou `include_paid=1` côté service) conserve ces entrées, en les
marquant `free: false` — utile si vous êtes abonné et que votre lecteur ouvre
les articles avec votre session. Cloclo ne contourne aucun paywall : il ne lit
que ce que le serveur envoie à un visiteur anonyme.

Par ailleurs, `robots.txt` est respecté par défaut (`--no-robots` pour l'ignorer
sur vos propres sites), les adresses privées sont refusées, les réponses de plus
de 5 Mo sont rejetées et les pages sont mises en cache pour ne pas marteler la
source. Restez raisonnable sur les fréquences de rafraîchissement.

## Texte intégral

`--full` télécharge chaque article et en extrait le corps (titre, texte, images),
publié dans `<content:encoded>`. Les blocs de navigation, encadrés « à lire
aussi », commentaires et pieds de page sont retirés ; le HTML conservé est
restreint à un jeu de balises sûres.

```bash
cloclo feed https://exemple.fr/blog --full --full-limit 10
```

`--full-limit` borne le nombre d'articles téléchargés (5 par défaut) : c'est
autant de requêtes vers le site source.

## Service HTTP

```bash
cloclo serve --port 8787 --base-url https://flux.mondomaine.fr
```

| Route | Rôle |
|---|---|
| `/` | formulaire d'assistance : coller une URL, prévisualiser, copier l'adresse du flux |
| `/feed?url=…` | le flux (`format=rss\|atom\|json`) |
| `/preview?url=…` | diagnostic JSON de la détection |
| `/discover?url=…` | flux déjà publiés par la page |
| `/healthz` | sonde de vivacité |

Paramètres de `/feed` : `url`, `format`, `limit`, `full`, `full_limit`,
`include_paid`, `strategy`, `feed_title`, et les sélecteurs `item`, `title`,
`link`, `date`, `summary`, `author`, `image`, `category`.

```
http://localhost:8787/feed?url=https://exemple.fr/actualites&item=.post&limit=20
```

Les réponses portent un `ETag` (les `304` économisent la bande passante des
lecteurs) et sont mises en cache `--ttl` secondes (900 par défaut). En exposition
publique, restreignez les domaines interrogeables :

```bash
cloclo serve --allow-host exemple.fr --allow-host presse.fr --max-limit 50
```

### Docker

```bash
docker build -t cloclo .
docker run -p 8787:8787 cloclo
```

### systemd

```ini
[Unit]
Description=Cloclo — générateur de flux RSS
After=network-online.target

[Service]
ExecStart=/usr/local/bin/cloclo serve --host 127.0.0.1 --port 8787 --ttl 1800
Restart=on-failure
DynamicUser=yes

[Install]
WantedBy=multi-user.target
```

## Bibliothèque Python

```python
from cloclo import Options, Settings, build

result = build(
    "https://exemple.fr/actualites",
    Options(limit=10, include_paid=False),
    Settings(fmt="atom", full=True, full_limit=3),
)

print(result.extraction.strategy, len(result.items))
open("flux.atom", "w", encoding="utf-8").write(result.body)
```

Sans réseau, sur du HTML déjà en mémoire :

```python
from cloclo.extract import extract
from cloclo.feed import FeedMeta, render

extraction = extract(html, "https://exemple.fr/actualites")
corps, type_mime = render(extraction.items, FeedMeta("Mon flux", "https://exemple.fr"))
```

## Options principales

| Option | Effet |
|---|---|
| `-n, --limit` | nombre d'entrées (30) |
| `--min-items` | taille minimale d'une série pour être retenue (3) |
| `--strategy` | forcer `selectors`, `jsonld`, `heuristic` ou `feed` |
| `--include-paid` | conserver les entrées réservées aux abonnés |
| `--full`, `--full-limit` | texte intégral |
| `-f, --format` | `rss`, `atom`, `json` |
| `--feed-title`, `--self-url` | métadonnées du flux |
| `--timeout`, `--user-agent` | réglages réseau |
| `--no-robots`, `--allow-private` | lever les garde-fous (sur vos sites) |
| `--recipes`, `--no-recipes` | recettes par site |

Codes de sortie : `0` succès, `2` erreur (réseau, arguments), `3` aucune entrée
trouvée — pratique pour surveiller une tâche planifiée.

## Limites connues

- Les pages dont la liste est construite en JavaScript ne livrent rien : Cloclo
  ne lit que le HTML servi. Cherchez alors l'API JSON du site, ou son flux caché
  (`cloclo discover`).
- Une page très hétérogène (blocs publicitaires intercalés, listes multiples de
  même structure) peut faire élire la mauvaise série : `preview` affiche les
  autres candidates, `--item` tranche.
- Les dates absentes de la page de liste ne sont récupérées qu'avec `--full`.

## Développement

```bash
python3 -m unittest discover -s tests -t .
```

83 tests, sans accès réseau : les fixtures HTML de `tests/fixtures/` couvrent un
blog, des données structurées, une liste de communiqués, une page mêlant
gratuit et payant, un flux existant et un article ; les tests d'intégration
montent un site local et le service HTTP sur des ports éphémères.

Organisation du code :

| Module | Rôle |
|---|---|
| `dom.py` | mini-DOM tolérant et moteur de sélecteurs CSS |
| `fetch.py` | HTTP, encodages, robots.txt, cache, garde-fous |
| `dates.py` | analyse des dates français/anglais/ISO/relatives |
| `extract.py` | détection des entrées (les quatre stratégies) |
| `article.py` | extraction du corps d'un article |
| `feed.py` | sérialisation RSS / Atom / JSON Feed |
| `recipes.py` | sélecteurs mémorisés par site |
| `generator.py` | chaîne complète URL → flux |
| `server.py` | service HTTP et page d'assistance |
| `cli.py` | ligne de commande |
