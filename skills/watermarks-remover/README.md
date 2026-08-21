# Skill `watermarks-remover`

Skill Claude qui retire les marqueurs de provenance IA (Unicode invisible,
C2PA/JUMBF, EXIF/XMP, docProps Office, balises `generator` HTML/Markdown…) des
fichiers générés. Portage stdlib-only des scripts de
[`guillaumemeyer/watermarks-remover`](https://github.com/guillaumemeyer/watermarks-remover)
(MIT), embarqués dans `scripts/`.

## Contenu

- `SKILL.md` — instructions et déclencheurs du skill.
- `clean.py` — point d'entrée unique (inspecte / nettoie fichiers ou dossiers).
- `scripts/` — pipeline de nettoyage vendoré (Python 3.10+ stdlib, sans réseau).

## Installer de façon permanente

Le conteneur d'une session Claude Code est éphémère : pour que le skill soit
disponible dans **toutes** tes sessions, installe-le une fois côté compte.

**Option A — dossier skills local (Claude Code) :**

```bash
cp -r skills/watermarks-remover ~/.claude/skills/watermarks-remover
```

**Option B — compte claude.ai :** zippe le dossier `skills/watermarks-remover/`
et téléverse-le dans les Skills de ton compte (Paramètres → Skills), pour qu'il
soit synchronisé partout.

```bash
cd skills && zip -r watermarks-remover.zip watermarks-remover
```

## Utilisation rapide

```bash
python3 clean.py --inspect fichier.docx      # rapport seul
python3 clean.py fichier.docx                # nettoie en place
python3 clean.py ./dossier/                  # nettoie un dossier (récursif)
```

## Outils optionnels

`qpdf` et `exiftool` renforcent le strip PDF/images :
`apt-get install -y qpdf libimage-exiftool-perl`. Le cœur fonctionne sans eux.
