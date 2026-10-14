#!/usr/bin/env python3
"""Reproduce the provenance verification for the installed WeChatDataAnalysis build.

Shows that the installed Electron app's main-process source is byte-identical
(modulo CRLF line endings) to the public upstream repository.

Usage:
  python verify_provenance.py <extracted-asar-src-dir> [ref]

  ref defaults to "main"; try "v2.9.0" too.
"""
import hashlib
import json
import os
import sys
import urllib.request

REPO = "LifeArchiveProject/WeChatDataAnalysis"


def get_raw(path, ref):
    url = f"https://raw.githubusercontent.com/{REPO}/{ref}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "provenance-check"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def norm(b):
    """Normalise BOM + line endings, since the packager rewrites LF -> CRLF."""
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def sha(b):
    return hashlib.sha256(norm(b)).hexdigest()


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else r"<repo>\app-asar\src"
    ref = sys.argv[2] if len(sys.argv) > 2 else "main"
    names = sorted(
        f for f in os.listdir(src)
        if os.path.isfile(os.path.join(src, f)) and f.endswith((".cjs", ".js", ".json"))
    )
    exact, differ = [], []
    for name in names:
        local = open(os.path.join(src, name), "rb").read()
        try:
            remote = get_raw("desktop/src/" + name, ref)
        except Exception as exc:  # noqa: BLE001
            differ.append((name, f"fetch failed: {exc}"))
            continue
        if sha(remote) == sha(local):
            exact.append(name)
        else:
            differ.append((name, "content differs"))

    print(f"upstream : {REPO}@{ref}")
    print(f"compared : {len(names)} files")
    print(f"identical: {len(exact)}")
    for name, why in differ:
        print(f"  DIFF {name}: {why}")
    if not differ:
        print("RESULT: installed build matches public source exactly (CRLF-normalised).")


if __name__ == "__main__":
    main()
