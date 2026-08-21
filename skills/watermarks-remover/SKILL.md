---
name: watermarks-remover
description: |
  Retire les marqueurs de provenance IA (multi-éditeurs) des documents générés :
  Unicode invisible, espaces exotiques, caractères bidi/tag, manifestes
  C2PA/JUMBF, métadonnées EXIF/XMP, docProps Office (dc:creator, Application…),
  balises generator HTML/Markdown, et marqueurs dans les médias intégrés.
  Couvre .docx .xlsx .pptx .pdf .odt .epub .html .md .svg .txt .csv, images
  (.png .jpg .webp .avif .heic .gif .tiff) et audio/vidéo (.mp4 .mov .m4a .wav
  .mp3). DÉCLENCHER SYSTÉMATIQUEMENT comme dernière étape, juste avant de livrer,
  chaque fois qu'un document ou fichier est produit, généré, exporté ou modifié
  pour Thomas (y compris via les skills docx, xlsx, pptx, pdf) — c'est une
  consigne permanente : tout fichier livré doit d'abord passer par ce nettoyage.
  DÉCLENCHER AUSSI sur demande explicite : « nettoie les watermarks », « enlève
  les marqueurs IA », « strip C2PA », « retire les métadonnées », « /watermarks »,
  ou quand Thomas fournit un fichier à inspecter/nettoyer.
---

# watermarks-remover

Nettoyage déterministe des marqueurs de provenance IA sur les fichiers que
Thomas génère, pour l'hygiène et la confidentialité des contenus **qu'il
possède**. Le pipeline est un portage des scripts de
[`guillaumemeyer/watermarks-remover`](https://github.com/guillaumemeyer/watermarks-remover)
(commit `d5563d2`, MIT), embarqués dans `scripts/` — **Python 3.10+ stdlib
uniquement, aucune dépendance ni réseau requis**.

## Quand l'utiliser

1. **Automatiquement, en fin de tâche.** Dès qu'un fichier destiné à Thomas est
   produit ou modifié (rapport .docx, tableau .xlsx, présentation .pptx, .pdf,
   image, page HTML, note Markdown…), nettoie-le **avant** de le renvoyer avec
   `SendUserFile`. C'est l'usage principal demandé : « faire tourner
   watermarks-remover pour tous les documents que je génère ».
2. **Sur demande explicite** d'inspection ou de nettoyage d'un fichier fourni.

Ne t'en sers pas pour du texte destiné à rester dans le chat (là, c'est le skill
`humain` qui s'applique) : ce skill agit sur des **fichiers**.

## Ce qu'il retire

| Couche | Cible |
| --- | --- |
| Texte (Layer A) | Unicode invisible (ZWSP, word-joiner…), espaces exotiques, bidi, tag chars |
| Conteneurs Office | `docProps/core.xml` (dc:creator, lastModifiedBy…), `app.xml` (Application, Company), `custom.xml`, customXml, relations orphelines |
| Images / médias intégrés | Manifestes C2PA/JUMBF, EXIF, XMP, chunks tEXt PNG, segments APP11 JPEG |
| HTML / Markdown | `<meta name="generator">`, JSON-LD de provenance, attributs `data-ai*`, clés de frontmatter (`generator`, `ai_generated`…) |
| PDF | Métadonnées + reconstruction structurelle (voir outils optionnels) |

Les codepoints invisibles « porteurs de sens » (glue d'emoji, jointures de
script, marques bidi orthographiques…) sont **préservés** par défaut. Les
formats non reconnus ne sont **jamais** réécrits — ils sont laissés intacts.

## Utilisation

Point d'entrée unique : `clean.py`. Toujours l'appeler avec le chemin absolu du
skill.

```bash
SK="$(dirname "$0")"   # ou le chemin où ce skill est installé

# Inspecter sans rien modifier (rapport des marqueurs trouvés)
python3 "$SK/clean.py" --inspect chemin/vers/fichier.docx

# Nettoyer EN PLACE (le fichier livré est le fichier nettoyé) — usage par défaut
python3 "$SK/clean.py" rapport.docx tableau.xlsx presentation.pptx

# Nettoyer tout un dossier (récursif, formats supportés seulement)
python3 "$SK/clean.py" ./sortie/

# Écrire à côté au lieu d'écraser
python3 "$SK/clean.py" --suffix .cleaned rapport.docx     # -> rapport.cleaned.docx

# Garder une sauvegarde <fichier>.orig
python3 "$SK/clean.py" --backup rapport.docx
```

Le nettoyage **en place** est le comportement voulu : après génération d'un
document, lance `clean.py <fichier>` puis livre le même chemin. La sortie liste,
par fichier, ce qui a été retiré et un récapitulatif final
(`N cleaned, M already clean, K error(s)`).

## Flux recommandé (à appliquer après chaque génération)

1. Génère le document normalement (via docx/xlsx/pptx/pdf ou autre).
2. `python3 "$SK/clean.py" <chemin_du_fichier>` — nettoyage en place.
3. Rapporte brièvement à Thomas ce qui a été retiré (1-2 lignes).
4. Livre le fichier nettoyé avec `SendUserFile`.

Si un fichier ressort **already clean**, dis-le simplement — pas de bruit.

## Outils système optionnels (améliorent, ne bloquent pas)

Le cœur fonctionne en stdlib seul. Trois outils, s'ils sont présents sur le
`PATH`, renforcent le nettoyage :

| Outil | Rôle | Sans lui |
| --- | --- | --- |
| `qpdf` | Reconstruction structurelle PDF — **requis pour un vrai strip PDF** | Le PDF n'est que partiellement nettoyé |
| `exiftool` | Purge des métadonnées résiduelles (surtout PDF/images) | Résidus EXIF/XMP possibles |
| `c2patool` | Inspection fine des manifestes C2PA | Détection C2PA quand même faite par heuristique |

Installation (Debian/Ubuntu) : `apt-get install -y qpdf libimage-exiftool-perl`.
Si tu traites un PDF et que `qpdf` manque, installe-le d'abord quand c'est
possible, sinon préviens Thomas que le strip PDF est partiel.

## Notes

- Les scripts vivent dans `scripts/` (portage figé du dépôt amont). Pour mettre à
  jour vers une version ultérieure, re-cloner le dépôt amont et recopier
  `service/scripts/*.py`, puis relancer un test rapide.
- Licence amont : MIT (voir `scripts/LICENSE.upstream`).
- Ce skill agit sur des fichiers **possédés par Thomas** ; c'est un outil
  d'hygiène et de confidentialité, pas de contournement.
