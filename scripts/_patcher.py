#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_patcher.py — generic exact-anchor patcher harness.

A small, self-contained library for writing patch scripts that edit
source files by exact-string replacement. Intended for the case where
you want to hand a collaborator (or your future self) a script that
safely applies a known set of edits to a known set of files — without
git, without `diff`/`patch`, and without a build step.

Drop this file into any Python project at `scripts/_patcher.py`, add
an empty `scripts/__init__.py`, and write a patch script at the
project root. See "Usage" below.


Design goals
============

1. Idempotent. Re-running a script must not duplicate insert-style
   edits. `classify` recognises when a patch is already in the
   applied state and reports it as such, rather than applying again.

2. Atomic per file. If any anchor for a file fails to match, the whole
   file is left unchanged. There is no partial application.

3. Backs up before writing. Each touched file gets a pristine `.orig`
   (written once, on the first run) and a pre-run `.bak` (overwritten
   each run). `--restore` copies every `.orig` back over its file.

4. Verifies before writing. Python sources are syntax-checked with
   `ast.parse`; a parse failure leaves the file untouched and reports
   the SyntaxError.

5. Traces failure. When an anchor is not found, the first line of the
   anchor is printed, so the user can grep the file for the drift.
   When an anchor is ambiguous, the actual match count is printed.


Usage
=====

Project layout:

    project-root/
      mypatch.py                # your patch script
      scripts/
        __init__.py             # empty
        _patcher.py             # this file

Your patch script (mypatch.py):

    from scripts._patcher import Patch, run

    PATCHES = [
        ("src/foo.py", [
            Patch("rename bar to baz",
                  'def bar():',
                  'def baz():'),
            Patch("add import os",
                  'import sys',
                  'import os\\nimport sys'),
        ]),
        ("js/app.js", [
            Patch("rename the button",
                  '<button id="go">',
                  '<button id="start">'),
        ]),
    ]

    VERIFY = \"\"\"\\
    Verify:
      python -c "import foo; foo.baz()"
      grep -n 'id=\"start\"' js/app.js

    Undo:
      python mypatch.py --restore
    \"\"\"

    if __name__ == "__main__":
        import sys
        sys.exit(run(
            PATCHES,
            root_marker="src/foo.py",
            verify_notes=VERIFY,
            prog="mypatch",
        ))

The CLI:

    python mypatch.py --check         # verify anchors match; no writes
    python mypatch.py --dry-run -v    # show the diff; no writes
    python mypatch.py                 # apply
    python mypatch.py --restore       # restore every .orig over its file
    python mypatch.py --root <path>   # override auto-detection


The `count` parameter
=====================

Most patches are "replace exactly one match of this string". That is
the default (`count=1`).

An insert-after patch looks like this:

    Patch("add a line",
          'first line',
          'first line\\nsecond line')

The old anchor `'first line'` remains present after the patch is
applied — it is the leading portion of the new text. Without special
handling, a rerun would insert a second copy. `classify` detects this
case by checking whether the new text is present the expected number
of times, and returns `already` before considering `apply`.

For a multi-site patch (the same edit applies in N places), pass
`count=N`. The replacement is applied to every occurrence, and
`classify` verifies both before and after that the count matches.

    Patch("add imgdescVersion to every loader",
          OLD_THREE_PLACE_SNIPPET,
          NEW_THREE_PLACE_SNIPPET,
          count=3)


Log prefixes
============

Every line of output begins with one of three symbols:

    ✓    success          (patch applied, file written, restore done)
    -    no action needed (already applied, no change, no backups)
    ✗    failure          (missing anchor, ambiguous anchor,
                           parse error, root not found)

The summary at the end aggregates these per file and per patch.


Exit codes
==========

    0    all patches applied or already applied; or --check passed
    1    precondition failure (missing or ambiguous anchor), or a
         patched source failed to parse
    2    could not locate the project root


Caveats
=======

- Exact-string matching only. No fuzzy matching, no regex. If you
  need to match around whitespace variation, use a larger anchor.

- Patches within a file are applied in order. If patch 2's anchor is
  a substring of patch 1's replacement text, the harness cannot tell
  the difference between an original occurrence and one it just
  introduced. Don't write patches that overlap.

- The root marker is a single file path relative to the project root.
  Detection walks up from the script's location, then the CWD. If
  neither finds it, pass --root explicitly.

- `--restore` only restores files named in the patch table. It does
  not touch unrelated `.orig` files that may exist elsewhere in the
  tree.


Testing
=======

`Patch`, `classify`, `apply_file`, and `restore_all` are exported for
unit testing. The following properties are worth asserting:

    classify(src, insert_after_patch) == "apply"     # first run
    classify(patched, insert_after_patch) == "already"  # rerun

    classify(src, replace_patch) == "apply"
    classify(patched, replace_patch) == "already"

    classify(src, multi_site_patch_with_count_3) == "apply"
    classify(patched, multi_site_patch_with_count_3) == "already"

    classify("same same", Patch("x", "same", "diff")) == "ambiguous"

See the "Design goals" above for why the first two are the interesting
cases.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "Patch",
    "classify",
    "apply_file",
    "restore_all",
    "run",
    "S_APPLY",
    "S_ALREADY",
    "S_MISSING",
    "S_AMBIG",
]


# ══════════════════════════════════════════════════════════════════════
# Data model and classification
# ══════════════════════════════════════════════════════════════════════

class Patch:
    """One exact-string edit.

    Attributes
    ----------
    name : str
        Short human-readable label, used in the log. Appears with a ✓
        when applied, a - when already applied, and a ✗ when the
        anchor is missing or ambiguous.

    old : str
        The exact source text to find. Must appear in the source
        exactly `count` times.

    new : str
        The replacement text. For a plain replacement, `old` should
        not appear in `new`. For an insertion, it typically does — the
        classify function handles that case.

    count : int
        How many occurrences of `old` to replace. Default 1. Use a
        higher value when the same edit applies at multiple sites.
    """

    __slots__ = ("name", "old", "new", "count")

    def __init__(self, name: str, old: str, new: str, *, count: int = 1):
        if count < 1:
            raise ValueError("count must be >= 1")
        self.name = name
        self.old = old
        self.new = new
        self.count = count

    def __repr__(self) -> str:
        return f"Patch({self.name!r}, count={self.count})"


S_APPLY = "apply"
S_ALREADY = "already"
S_MISSING = "missing"
S_AMBIG = "ambiguous"


def classify(src: str, patch: Patch) -> str:
    """Decide what to do with one patch against one source string.

    Returns one of:

        S_APPLY      anchor present the expected number of times
        S_ALREADY    patch appears to already be in effect
        S_MISSING    anchor not present
        S_AMBIG      anchor present more times than expected

    Order matters. For insert-after patches (where `new` extends
    `old`), the old anchor remains present after the patch is applied
    — it is the leading portion of `new`. So the already-applied test
    must come before the apply test, or a rerun duplicates the
    inserted block.

    The two signals for "already applied" are:

        replacement:  old gone (n_old == 0), new present n_new times
        insertion:    old still present (as a prefix of new),
                      and new present n_new times

    Both require `n_new == patch.count`. Anything else falls through
    to the apply/missing/ambig checks.
    """
    n_old = src.count(patch.old)
    n_new = src.count(patch.new) if patch.new else 0

    if n_new == patch.count:
        if n_old == 0:
            return S_ALREADY
        if patch.old in patch.new and n_old == patch.count:
            return S_ALREADY

    if n_old > patch.count:
        return S_AMBIG
    if n_old == patch.count:
        return S_APPLY
    return S_MISSING


# ══════════════════════════════════════════════════════════════════════
# Small helpers
# ══════════════════════════════════════════════════════════════════════

def _first_line(text: str, n: int = 80) -> str:
    """Return the first line of `text`, truncated to n chars.

    Used to show a preview of a missing anchor without dumping the
    whole multi-line string at the user.
    """
    lines = text.splitlines()
    line = lines[0] if lines else text
    line = line.rstrip()
    return line if len(line) <= n else line[: n - 1] + "\u2026"


def _backup(path: Path) -> Tuple[Path, Path]:
    """Write `<path>.orig` (only if absent) and `<path>.bak` (always).

    Returns (orig, bak). The .orig is the pristine pre-first-run copy;
    the .bak is the state immediately before this run.
    """
    orig = path.with_suffix(path.suffix + ".orig")
    bak = path.with_suffix(path.suffix + ".bak")
    if not orig.exists():
        shutil.copy2(path, orig)
    shutil.copy2(path, bak)
    return orig, bak


def _verify_python(src: str, filename: str) -> Optional[str]:
    """Return a message if `src` does not parse as Python, else None.

    Non-Python files are not checked — the caller decides whether to
    invoke this.
    """
    try:
        ast.parse(src, filename=filename)
    except SyntaxError as e:
        return f"{e.__class__.__name__}: {e}"
    return None


def _diff(old: str, new: str, filename: str) -> str:
    """Unified diff between two strings, a/ b/ prefixed."""
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=2,
    ))


# ══════════════════════════════════════════════════════════════════════
# Per-file application
# ══════════════════════════════════════════════════════════════════════

def apply_file(relpath: str,
               patches: Sequence[Patch],
               root: Path,
               *,
               dry_run: bool,
               check_only: bool,
               verbose: bool) -> Tuple[int, int, int, int]:
    """Apply every patch in `patches` to one file.

    Returns (applied, already, missing, ambiguous).

    Precondition policy: if any patch is MISSING or AMBIGUOUS, the
    file is left unchanged. The preconditions are checked before any
    writes, so a half-applied file cannot result.
    """
    patches = list(patches)
    path = root / relpath

    print(f"\n\u2500\u2500 {relpath}")
    if not path.exists():
        print(f"  \u2717 file not found — skipped")
        return 0, 0, 0, 0

    src = path.read_text(encoding="utf-8")
    original = src

    statuses: List[Tuple[Patch, str]] = [(p, classify(src, p)) for p in patches]
    fatal = [s for _, s in statuses if s in (S_MISSING, S_AMBIG)]

    # Precondition failure: report every patch and exit without touching
    # the file.
    if fatal:
        for p, s in statuses:
            if s == S_APPLY:
                print(f"  \u2713 {p.name}")
            elif s == S_ALREADY:
                print(f"  - {p.name}: already applied")
            elif s == S_MISSING:
                print(f"  \u2717 {p.name}: anchor not found")
                print(f"       looked for: {_first_line(p.old)!r}")
            elif s == S_AMBIG:
                print(f"  \u2717 {p.name}: anchor appears "
                      f"{src.count(p.old)}x (expected {p.count})")
                print(f"       looked for: {_first_line(p.old)!r}")
        print(f"  \u2192 file left UNCHANGED (preconditions failed)")
        return (0, 0,
                sum(1 for _, s in statuses if s == S_MISSING),
                sum(1 for _, s in statuses if s == S_AMBIG))

    # --check mode: stop here, no writes.
    if check_only:
        for p, s in statuses:
            if s == S_APPLY:
                print(f"  \u2713 {p.name}")
            else:
                print(f"  - {p.name}: already applied")
        return (sum(1 for _, s in statuses if s == S_APPLY),
                sum(1 for _, s in statuses if s == S_ALREADY),
                0, 0)

    applied = already = 0
    for patch, status in statuses:
        if status == S_ALREADY:
            print(f"  - {patch.name}: already applied")
            already += 1
            continue
        # str.replace without a count replaces every occurrence. That is
        # what we want — classify verified the count.
        src = src.replace(patch.old, patch.new)
        print(f"  \u2713 {patch.name}")
        applied += 1

    if src == original:
        print("  - no change")
        return applied, already, 0, 0

    # Dry run: show the diff, don't write.
    if dry_run:
        diff = _diff(original, src, relpath)
        if verbose:
            print(diff)
        else:
            added = sum(1 for l in diff.splitlines()
                        if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff.splitlines()
                          if l.startswith("-") and not l.startswith("---"))
            print(f"  \u2192 dry-run: +{added} / -{removed} lines")
        return applied, already, 0, 0

    # Verify the patched source parses before we write it.
    if path.suffix == ".py":
        err = _verify_python(src, str(path))
        if err:
            print(f"  \u2717 patched source did not parse: {err}")
            print(f"  \u2192 file left UNCHANGED")
            return 0, already, 0, 0

    # Post-patch duplication guard. If any patch's new block appears
    # more times than its declared count, an earlier run duplicated it.
    # This catches the insert-after bug before any file is written.
    for patch, _status in statuses:
        if patch.new and src.count(patch.new) > patch.count:
            print(f"  \u2717 post-patch '{patch.name}' appears "
                  f"{src.count(patch.new)}x (expected {patch.count})")
            print(f"  \u2192 file left UNCHANGED")
            return 0, already, 0, 0

    orig, bak = _backup(path)
    print(f"       backups: {orig.name}, {bak.name}")
    path.write_text(src, encoding="utf-8")
    print(f"  \u2713 wrote {path}")
    return applied, already, 0, 0


# ══════════════════════════════════════════════════════════════════════
# Root detection
# ══════════════════════════════════════════════════════════════════════

def _detect_root(root_marker: str, cli_root: Optional[str]) -> Optional[Path]:
    """Find the project root containing `root_marker`.

    Explicit `cli_root` wins. Otherwise walk up from the script's
    location, then fall back to the CWD. A candidate is the root only
    if `candidate / root_marker` exists as a file.
    """
    if cli_root:
        p = Path(cli_root).expanduser().resolve()
        return p if (p / root_marker).is_file() else None

    candidates: List[Path] = [Path(sys.argv[0]).resolve().parent]
    candidates.extend(Path(sys.argv[0]).resolve().parents)
    candidates.append(Path.cwd())

    seen = set()
    for c in candidates:
        try:
            cr = c.resolve()
        except OSError:
            continue
        if cr in seen:
            continue
        seen.add(cr)
        if (cr / root_marker).is_file():
            return cr
    return None


# ══════════════════════════════════════════════════════════════════════
# Restore
# ══════════════════════════════════════════════════════════════════════

def restore_all(root: Path,
                patches_by_file: Iterable[Tuple[str, Sequence[Patch]]],
                *,
                dry_run: bool) -> int:
    """Copy every `<file>.orig` back over its file.

    Only files named in the patch table are touched. Returns the count
    of files restored.
    """
    n = 0
    for relpath, _patches in patches_by_file:
        orig = root / (relpath + ".orig")
        if not orig.is_file():
            continue
        target = root / relpath
        if dry_run:
            print(f"  - would restore {target} <- {orig}")
        else:
            shutil.copy2(orig, target)
            print(f"  \u2713 restored {target} <- {orig}")
        n += 1
    if n == 0:
        print("  - no .orig backups found")
    return n


# ══════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════

def run(patches_by_file: Sequence[Tuple[str, Sequence[Patch]]],
        *,
        root_marker: str,
        verify_notes: str = "",
        prog: str = "patcher",
        argv: Optional[List[str]] = None) -> int:
    """Apply a patch table.

    Parameters
    ----------
    patches_by_file : sequence of (relpath, [Patch, ...])
        The patch table. Paths are relative to the project root.

    root_marker : str
        A file path relative to the project root that must exist for
        auto-detection to succeed. Pass any file that is unique to the
        project, e.g. "src/foo/__init__.py" or "package.json".

    verify_notes : str
        Free text printed after a successful apply. Use it to list the
        manual checks the user should run, and how to undo.

    prog : str
        Name shown in --help. Usually the calling script's name.

    argv : list of str or None
        Arguments to parse. Defaults to sys.argv[1:].

    Returns
    -------
    int
        Process exit code: 0 success, 1 precondition failure,
        2 root not found.
    """
    ap = argparse.ArgumentParser(
        prog=prog,
        description="Apply exact-anchor patches. Backs up before "
                    "patching; safe to re-run.",
    )
    ap.add_argument("--root", default=None,
                    help="Project root (auto-detected if omitted).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show the diff, make no changes.")
    ap.add_argument("--check", action="store_true",
                    help="Verify anchors match; make no changes.")
    ap.add_argument("--restore", action="store_true",
                    help="Restore every .orig over its file.")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Full diffs in dry-run mode.")
    args = ap.parse_args(argv)

    root = _detect_root(root_marker, args.root)
    if root is None:
        print(f"\u2717 could not locate the project root.", file=sys.stderr)
        print(f"  expected to find {root_marker} under it.",
              file=sys.stderr)
        print(f"  pass --root <path> to specify it.", file=sys.stderr)
        return 2

    if args.restore:
        print(f"project root: {root}")
        print(f"mode: restore{' (dry-run)' if args.dry_run else ''}\n")
        n = restore_all(root, patches_by_file, dry_run=args.dry_run)
        print(f"\n  restored {n} file(s).")
        return 0

    print(f"project root: {root}")
    mode = ("check" if args.check
            else "dry-run" if args.dry_run
            else "apply")
    print(f"mode: {mode}")

    totals = {"applied": 0, "already": 0, "missing": 0, "ambiguous": 0,
              "files_ok": 0, "files_skipped": 0}

    for relpath, patches in patches_by_file:
        if not patches:
            continue
        applied, already, missing, ambiguous = apply_file(
            relpath, patches, root,
            dry_run=args.dry_run, check_only=args.check,
            verbose=args.verbose,
        )
        totals["applied"] += applied
        totals["already"] += already
        totals["missing"] += missing
        totals["ambiguous"] += ambiguous
        if missing or ambiguous:
            totals["files_skipped"] += 1
        else:
            totals["files_ok"] += 1

    print("\n\u2500\u2500 Summary")
    print(f"  \u2713 files ok:            {totals['files_ok']}")
    print(f"  \u2717 files skipped:       {totals['files_skipped']}")
    print(f"  \u2713 patches applied:     {totals['applied']}")
    print(f"  - patches already:     {totals['already']}")
    print(f"  \u2717 anchors missing:     {totals['missing']}")
    print(f"  \u2717 anchors ambiguous:   {totals['ambiguous']}")

    if args.check:
        if totals["missing"] or totals["ambiguous"]:
            print("\n  \u2192 preconditions NOT satisfied. No writes made.")
            return 1
        print("\n  \u2192 preconditions satisfied.")
        return 0

    if totals["missing"] or totals["ambiguous"]:
        print("\n  \u2192 some files were skipped. Read the output above.")
        return 1

    if args.dry_run:
        print("\n  \u2192 dry run complete. Re-run without --dry-run to apply.")
        return 0

    print("\n  \u2192 patches applied.")
    if verify_notes:
        print(verify_notes)
    return 0