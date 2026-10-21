## Tree

```
(root)/
  patch_imgdesc.py         ← the patch table + 3-line tail
  scripts/
    __init__.py            ← empty marker (module docstring is fine)
    _patcher.py            ← the reusable harness
```

## Usage

```bash
python patch_imgdesc.py --check        # verify anchors match
python patch_imgdesc.py --dry-run -v   # full diff
python patch_imgdesc.py                # apply
python patch_imgdesc.py --restore      # undo
```

## What moved where

| Moved to `_patcher.py` | Stays in `patch_imgdesc.py` |
|---|---|
| `Patch`, `classify`, `S_*` status codes | `_p_desc_builder()`, `_p_main()`, `_p_cache_io()`, `_p_subject()`, `_p_subject_cluster()` |
| `_first_line`, `_backup`, `_verify_python`, `_diff` | `build_patch_table()` |
| `apply_file`, `_detect_root`, `restore_all` | `ROOT_MARKER`, `VERIFY` |
| `run` (renamed from `main`), argparse, summary, exit codes | The `if __name__ == "__main__"` tail |

## Using `_patcher.py` in a different project

Copy `scripts/_patcher.py` and `scripts/__init__.py` into the new project. Write a patch script at its root:

```python
from scripts._patcher import Patch, run

PATCHES = [
    ("src/foo.py", [
        Patch("fix the off-by-one", "range(n)", "range(n + 1)"),
    ]),
]

if __name__ == "__main__":
    import sys
    sys.exit(run(PATCHES, root_marker="src/foo.py"))
```

The module docstring in `_patcher.py` has a fuller example plus the design rationale (why idempotency needed care, how `count` works, what the prefixes mean, and what the exit codes are). Enough to pick the file up cold months later without needing this conversation.

## A note on the `sys.argv[0]` change

`_detect_root` used to look at `Path(__file__).parent` — the location of `_patcher.py` itself, which was fine when the harness lived at the project root. Now that it's under `scripts/`, `Path(__file__)` points to `scripts/`, and walking up from there gets to the project root in one step. I changed it to walk up from `sys.argv[0]` (the *calling script*'s location) so it works regardless of where `_patcher.py` sits relative to the caller. If the caller is invoked by absolute path or via a symlink, `sys.argv[0]` still resolves correctly. If it can't — say, running `python -c "from patch_imgdesc import ..."` — the `--root` fallback covers it.