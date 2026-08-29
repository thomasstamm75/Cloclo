---
name: fluxrss
description: Fabrique un flux RSS pour une page web qui n'en propose pas, afin de la suivre dans un agrégateur comme Feeder, Inoreader ou NetNewsWire. Déclencher ce skill dès que Thomas donne l'adresse d'une page en demandant de la suivre, d'en être tenu informé, d'en faire un flux, ou signale qu'un site n'a pas de RSS. Déclencher aussi pour « fais-moi un flux RSS de cette page », « ce site n'a pas de flux », « je veux suivre ces publications dans Feeder », « génère un flux pour telle rubrique », « comment être prévenu des nouvelles notes de ce site », « /fluxrss », « /fluxRSS », ou quand il colle l'URL d'une page d'actualités, de publications, de rapports ou de communiqués en demandant d'en recevoir les nouveautés. Couvre la recherche d'un flux existant, l'extraction des articles, l'écriture du fichier .xml et sa mise en ligne à une adresse stable.
---

# Générateur de flux RSS

Beaucoup de sites publient régulièrement sans proposer de flux : on ne peut ni
les suivre dans un agrégateur, ni être prévenu de leurs nouveautés autrement
qu'en y retournant à la main. Ce skill fabrique le flux manquant.

Le paquet `scripts/rssgen/` fait le travail d'extraction et écrit un RSS 2.0
valide. Vous l'orchestrez : choisir la bonne page, vérifier que le résultat
correspond à ce que Thomas veut suivre, et le rendre joignable par son lecteur.

**Le point à ne pas manquer :** un fichier `.xml` posé dans un bac à sable ne
sert à rien à Feeder. Un agrégateur interroge une **adresse**, régulièrement.
Tant que le flux n'est pas hébergé quelque part, le travail n'est qu'à moitié
fait — l'étape 4 n'est pas optionnelle.

## Préparation

```bash
pip install beautifulsoup4          # strict minimum
pip install requests lxml           # si le sandbox a accès au réseau
```

Toutes les commandes se lancent depuis `scripts/` :

```bash
cd <chemin-du-skill>/scripts && python3 -m rssgen --help
```

## Étape 1 — Chercher un flux existant

Commencez toujours par là. Beaucoup de sites publient un flux sans l'annoncer
visiblement : le trouver évite de fabriquer un doublon de moins bonne qualité,
et donne à Thomas les dates et les résumés officiels.

```bash
python3 -m rssgen inspect https://exemple.fr/actualites
```

La commande lit les balises `<link rel="alternate">` puis essaie les adresses
conventionnelles (`/feed`, `/rss.xml`, `/atom.xml`…). Si elle en trouve un,
donnez l'adresse à Thomas et arrêtez-vous là : il n'a plus qu'à la coller dans
Feeder. Fabriquer un flux concurrent serait du travail perdu.

Si l'accès réseau du sandbox est bloqué, `inspect` échoue avec une erreur de
proxy. Passez à l'étape 2, voie B ou C, et vérifiez vous-même l'absence de flux
en regardant le `<head>` de la page.

## Étape 2 — Récupérer les articles

Trois voies. Prenez la première qui fonctionne dans l'environnement courant :
la qualité du flux décroît de A à C, parce que rssgen exploite le HTML brut
(dates réelles, JSON-LD, images, dédoublonnage) que les deux autres voies
perdent en partie.

### Voie A — le sandbox a accès au réseau

```bash
python3 -m rssgen make https://exemple.fr/actualites \
  --title "Exemple — actualités" -o flux.xml --state ./etat
```

### Voie B — vous pouvez enregistrer la page

Si vous disposez de `curl` ou d'un outil rendant le HTML brut :

```bash
curl -sL https://exemple.fr/actualites -o page.html
python3 -m rssgen make https://exemple.fr/actualites --html page.html \
  --title "Exemple — actualités" -o flux.xml --state ./etat
```

L'URL reste obligatoire même avec `--html` : elle sert à transformer les liens
relatifs de la page en adresses absolues, sans quoi les articles seraient
inouvrables depuis Feeder.

### Voie C — vous ne pouvez que lire la page

Quand seul un outil de lecture web est disponible (il rend du markdown, pas du
HTML), relevez vous-même les articles et écrivez-les en JSON :

```json
[
  {"title": "Titre de l'article", "link": "/2026/03/exemple",
   "date": "12 mars 2026", "summary": "Une phrase de résumé.",
   "author": "Nom", "image": "/media/1.jpg"}
]
```

Seuls `title` et `link` sont obligatoires ; les liens relatifs sont acceptés.
Les dates sont comprises en français (`12 mars 2026`, `01/02/2026`,
`il y a 3 heures`) comme en ISO.

```bash
python3 -m rssgen from-items articles.json --url https://exemple.fr/actualites \
  --title "Exemple — actualités" -o flux.xml --state ./etat
```

Cette voie demande de la rigueur : ne relevez que les vraies publications, pas
les liens de menu ni les encarts « à lire aussi », et gardez l'ordre du plus
récent au plus ancien.

### Pourquoi `--state`

Les pages sans date sont fréquentes. Sans mémoire, chaque régénération leur
donnerait l'heure courante et Feeder remonterait sans cesse les mêmes articles
en tête de liste. Le dossier passé à `--state` retient la date de première
apparition de chaque article et s'y tient. Conservez-le à côté du flux dès que
celui-ci sera régénéré plus tard.

## Étape 3 — Vérifier avant de livrer

Regardez le flux produit et jugez-le comme un lecteur :

```bash
python3 -m rssgen preview https://exemple.fr/actualites --html page.html
```

- Les titres sont-ils ceux des articles, ou des libellés de navigation ?
- Le nombre d'articles correspond-il à ce que la page affiche ?
- Les dates sont-elles plausibles et bien ordonnées ?
- Les liens ouvrent-ils bien les articles ?

Si la détection automatique attrape le mauvais bloc, imposez un sélecteur CSS.
`inspect` en propose un ; sinon, repérez la balise qui entoure exactement un
article et testez :

```bash
python3 -m rssgen make <url> --html page.html --item "article.post" -o flux.xml
```

Un sélecteur explicite vaut mieux qu'une détection automatique dès que le flux
doit durer : il résiste mieux aux changements de mise en page.

Montrez à Thomas les trois ou quatre premiers titres avec leurs dates. C'est
le moyen le plus rapide pour lui de confirmer que le flux suit bien ce qu'il
voulait suivre.

## Étape 4 — Rendre le flux joignable par Feeder

Le flux n'existe pour l'agrégateur qu'à partir du moment où il a une adresse
publique, et il doit être régénéré pour rester à jour. Proposez la voie qui
correspond à la situation.

**Thomas dispose du dépôt `Cloclo`** (son générateur, sur GitHub) — c'est la
voie normale, tout y est déjà en place : ajout de la page à `feeds.yaml`,
régénération automatique toutes les trois heures et publication sur GitHub
Pages. Voir `references/hebergement.md` pour les commandes exactes.

**Sinon** — n'importe quel hébergement de fichiers statiques accessible en HTTP
convient (GitHub Pages, un dossier public sur un serveur, un partage web). Ce
qui compte : l'adresse ne doit pas changer, et le fichier doit être réécrit
régulièrement, sinon le flux se fige.

Terminez toujours en donnant à Thomas l'adresse exacte à coller dans Feeder
(bouton **+**, puis l'URL du `.xml`), et dites-lui à quelle fréquence le flux
sera rafraîchi. Un flux dont il ignore la fréquence de mise à jour lui laissera
croire que le site ne publie plus.

## Réglages utiles

| Besoin | Option |
| --- | --- |
| Ne garder que certains sujets | `--item` puis un filtre dans `feeds.yaml` (`include` / `exclude`) |
| Plus ou moins d'articles | `--limit 40` |
| Titre affiché dans Feeder | `--title "..."` |
| Adresse finale du flux | `--feed-url https://.../flux.xml` |

Le texte intégral des articles (`full_text`) se règle dans `feeds.yaml`, décrit
dans `references/hebergement.md`. Il télécharge chaque article : utile pour
lire hors ligne, plus lourd pour le site suivi.

## Pièges rencontrés

**Une page qui charge ses articles en JavaScript** ne donne rien : le HTML brut
est presque vide. Cherchez la page qui liste les articles côté serveur (souvent
une rubrique ou une archive), ou passez par la voie C.

**Une seule ou deux entrées détectées** : la détection automatique exige au
moins trois blocs semblables pour éviter de prendre un menu pour une liste
d'articles. Avec `--item`, cette limite ne s'applique plus.

**Plusieurs rubriques sur un même site** : mieux vaut un flux par rubrique
qu'un flux fourre-tout depuis l'accueil. Thomas peut alors ranger, filtrer ou
retirer une rubrique sans toucher aux autres.

**Le site est momentanément injoignable** : `rssgen` réutilise le dernier
contenu connu plutôt que de publier un flux vide, ce qui viderait la liste dans
Feeder. Un flux inchangé après une panne est donc normal.

## Bon voisinage

L'outil s'identifie, espace ses requêtes et s'appuie sur les en-têtes `ETag` et
`Last-Modified` pour ne pas retélécharger une page inchangée. Gardez un rythme
de relevé raisonnable — quelques passages par jour suffisent pour un site qui
publie quelques fois par semaine — et signalez à Thomas si un site interdit
explicitement ce genre d'usage dans ses conditions.
