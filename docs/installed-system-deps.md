# System packages installed for the IJON eval (for later cleanup)

These are **apt packages installed system-wide** specifically for this project's
evaluations. None are needed by the project's own code (the harness is
stdlib-only Python); they're build/runtime deps of the third-party fuzz targets.
Listed here so they can be removed when the project is done.

| package | installed for | date |
|---|---|---|
| `libsdl2-dev` | SuperMarioBros-C (Mario game demo) — game links SDL2 (headless still links it) | 2026-06-13 |
| `libtool` | libtpms (vTPM) — autogen/autoreconf needs it | 2026-06-13 |
| `gawk` | libtpms build scripts | 2026-06-13 |

`libtasn1` was investigated and is **not** required (TPM 2.0 build).

## Cleanup when done
```
sudo apt-get remove --purge libsdl2-dev libtool gawk
sudo apt-get autoremove        # drops now-unused transitive deps
```
All three are safe to remove — every artifact they produced is committed
(the Mario GIF `experiments/mario/mario_playthrough.gif`) or regenerable.
Only reinstall `libsdl2-dev` if you ever want to rebuild/re-render the Mario
target locally (the GIF itself needs nothing).

## Also undo (service change, not a package)
During the libsdl2-dev install we **masked packagekit** to free a stuck apt lock.
Restore it:
```
sudo systemctl unmask packagekit
```

> Update this table if any further system packages get installed for the eval.
