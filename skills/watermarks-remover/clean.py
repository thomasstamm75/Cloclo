#!/usr/bin/env python3
"""Entry point for the watermarks-remover skill.

Strips multi-vendor AI provenance marks (invisible Unicode, exotic spaces,
bidi/tag chars, C2PA/JUMBF manifests, EXIF/XMP, Office docProps, HTML/Markdown
generator metadata) from the documents you generate.

Wraps the vendored `scripts/clean_file.py` pipeline (Python stdlib only). Cleans
files in place by default so the delivered file is the clean one.

Usage:
    python3 clean.py FILE [FILE ...]        # clean in place
    python3 clean.py DIR                    # clean every supported file under DIR
    python3 clean.py --inspect FILE         # report marks only, change nothing
    python3 clean.py --suffix .cleaned FILE # write beside the original instead
    python3 clean.py --backup FILE          # keep a .orig copy of each file

Exit code: 0 always for a completed run (per-file errors are reported, not fatal),
2 only on a usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "scripts")
CLEAN = os.path.join(SCRIPTS, "clean_file.py")
INSPECT = os.path.join(SCRIPTS, "inspect_file.py")

# Formats the pipeline knows how to clean. Anything else is skipped (never
# mangled): unknown bytes are left untouched.
SUPPORTED_EXTS = {
    # text / markup
    ".txt", ".md", ".markdown", ".html", ".htm", ".svg", ".csv", ".tsv",
    ".json", ".xml", ".rtf",
    # office / ebook containers
    ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".epub",
    # pdf
    ".pdf",
    # raster / images
    ".png", ".jpg", ".jpeg", ".webp", ".avif", ".heic", ".heif",
    ".bmp", ".gif", ".tiff", ".tif",
    # audio / video
    ".mp4", ".mov", ".m4a", ".m4v", ".wav", ".mp3",
}


def iter_targets(paths):
    """Yield concrete file paths from files and directories."""
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for name in sorted(files):
                    fp = os.path.join(root, name)
                    if os.path.splitext(name)[1].lower() in SUPPORTED_EXTS:
                        yield fp
        elif os.path.isfile(p):
            yield p
        else:
            print(f"skip (not found): {p}", file=sys.stderr)


def run_json(script, args):
    """Run a vendored script with --json and return (ok, parsed_or_text)."""
    proc = subprocess.run(
        [sys.executable, script, *args, "--json"],
        capture_output=True, text=True,
    )
    out = proc.stdout.strip()
    try:
        return True, json.loads(out)
    except json.JSONDecodeError:
        return False, (out or proc.stderr.strip() or f"exit {proc.returncode}")


def summarize_clean(report):
    """Turn a clean report dict into a short human line list."""
    lines = []
    # Text Layer A stats
    stats = report.get("stats") or report.get("report", {}).get("stats")
    if isinstance(stats, dict):
        rc = stats.get("removed_count", 0)
        pc = stats.get("replaced_count", 0)
        if rc or pc:
            lines.append(f"text: removed {rc}, replaced {pc} codepoint(s)")
    # Container / image change notes
    for key in ("changes", "notes", "actions"):
        vals = report.get(key)
        if isinstance(vals, list):
            lines.extend(f"- {v}" for v in vals)
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Strip AI provenance marks from generated documents.")
    ap.add_argument("paths", nargs="+", help="files or directories")
    ap.add_argument("--inspect", action="store_true",
                    help="report marks only; change nothing")
    ap.add_argument("--suffix", default=None,
                    help="write cleaned file beside original with this suffix "
                         "(e.g. .cleaned) instead of cleaning in place")
    ap.add_argument("--backup", action="store_true",
                    help="keep a <file>.orig copy before cleaning in place")
    ap.add_argument("--nfkc", action="store_true",
                    help="also NFKC-normalize text")
    args = ap.parse_args(argv)

    targets = list(iter_targets(args.paths))
    if not targets:
        print("no supported files found", file=sys.stderr)
        return 0

    changed, clean_already, failed = 0, 0, 0

    for fp in targets:
        rel = fp
        if args.inspect:
            ok, rep = run_json(INSPECT, [fp])
            if not ok:
                print(f"[error] {rel}: {rep}", file=sys.stderr)
                failed += 1
                continue
            suspicious = bool(
                rep.get("has_c2pa")
                or rep.get("has_ai_metadata")
                or rep.get("suspicious_total")
                or rep.get("layer_a_hits")
            )
            flag = "MARKS" if suspicious else "clean"
            print(f"[{flag}] {rel}  kind={rep.get('kind', '?')}")
            for finding in (rep.get("findings") or [])[:6]:
                print(f"    - {finding}")
            continue

        extra = ["--nfkc"] if args.nfkc else []
        if args.suffix:
            base, ext = os.path.splitext(fp)
            out = f"{base}{args.suffix}{ext}"
            ok, rep = run_json(CLEAN, [fp, "-o", out, *extra])
            dest = out
        else:
            if args.backup:
                shutil.copy2(fp, fp + ".orig")
            # Clean to a temp path in the same dir, then atomically replace.
            d = os.path.dirname(os.path.abspath(fp)) or "."
            fd, tmp = tempfile.mkstemp(dir=d, suffix=os.path.splitext(fp)[1])
            os.close(fd)
            ok, rep = run_json(CLEAN, [fp, "-o", tmp, *extra])
            if ok:
                os.replace(tmp, fp)
                dest = fp
            else:
                if os.path.exists(tmp):
                    os.remove(tmp)
                dest = fp

        if not ok:
            print(f"[error] {rel}: {rep}", file=sys.stderr)
            failed += 1
            continue

        notes = summarize_clean(rep if isinstance(rep, dict) else {})
        if notes:
            changed += 1
            print(f"[cleaned] {dest}")
            for n in notes:
                print(f"    {n}")
        else:
            clean_already += 1
            print(f"[ok] {dest} (no marks found)")

    print(f"\n{len(targets)} file(s): {changed} cleaned, "
          f"{clean_already} already clean, {failed} error(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
