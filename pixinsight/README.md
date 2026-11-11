# wbpp_rebuild_cache

Rebuild the PixInsight WBPP 3.0.x execution cache from a WBPP output directory (its logs plus the files on disk),
so that re-running WBPP with identical settings skips the steps that already completed.

## Why

A WBPP run can take hours. WBPP has an execution cache that lets a rerun skip finished work, but it keeps that cache
in memory and writes `WeightedBatchPreprocessing-NNN-pxi.cache` only when its window is closed normally. If
PixInsight crashes, hangs (for example on an online plate-solving query that never answers) or is force-quit, the
cache is lost and the rerun starts from zero, even though every calibrated, registered and normalized file is still
on disk.

WBPP logs every cache write to `OUTPUT_DIR/logs/*.log`, and that is enough to rebuild the cache.

## How to recover an interrupted run

1. Quit PixInsight (force-quit if it hangs). A closed PixInsight cannot overwrite what you install.
2. Dry run. It shows what can be rebuilt and changes nothing:

       python3 wbpp_rebuild_cache.py /path/to/wbpp/output

   Every step you expect to skip should have a non-zero `data` count. `skipped` rows will be recomputed.
3. Install it (makes a backup first):

       python3 wbpp_rebuild_cache.py /path/to/wbpp/output --install

4. Start PixInsight and open WBPP. When it asks *"A previous running configuration has been found. Do you want to
   reload it?"*, answer **Yes**. That restores the exact frames and settings of the interrupted run.
5. Do not change any setting that affects processing; any change produces new cache keys and recomputes those
   steps. If the run hung on plate solving, it is safe to disable it or switch to a local catalog first, because
   that is not part of any key.
6. Run. The console should show `has cached data for key …` and `File is unmodified`, and finished steps take
   seconds. SubframeSelector always re-measures (a few minutes). Frames that failed originally are retried.

To undo, run the `cp` command printed by `--install`, which restores the backup.

Other options: `--out FILE` (write the JSON anywhere, no install), `--log PATH` (repeatable; default is every
WBPP 3.0.x log in `OUTPUT_DIR/logs`, oldest first, later runs override earlier ones), `--config-dir DIR` (default
`~/Library/PixInsight` on macOS, required elsewhere), `--instance N` (default 1), `--no-settings-patch`,
`--force-version`. Needs Python 3 (standard library only). The `zstd` CLI is needed only for the settings patch.

## How it works

- **Keys:** every cache entry is keyed by a SHA1 of the step's process parameters. They are copied verbatim from
  the `Saved with key` log lines, so the tool never has to recompute them.
- **Values:** each step's input → output mapping and result are rebuilt from the step's process block in the log,
  its output-naming rules and the files on disk. ImageIntegration frame counts are read from the master's XISF
  header.
- **File stamps:** WBPP treats a file as unchanged only if its stored last-modified stamp matches exactly. Stamps
  are derived from current mtimes using PixInsight's own rounding (verified against thousands of logged values).
  - The current mtime is used only when it matches WBPP's newest logged write of that file.
  - Otherwise the logged stamp is kept, so a file changed outside WBPP (a replaced master or raw frame) is detected
    and re-processed.
  - Outputs rewritten after their log are not cached.

## What is rebuilt

Rebuilt:
- ImageCalibration
- Debayer (combined RGB output)
- StarAlignment
- LocalNormalization (reference generation and per-frame)
- ImageIntegration (any group)
- DrizzleIntegration

Not rebuilt, so WBPP recomputes them and the summary reports them as "skipped":
- SubframeSelector (deliberately: the log rounds its measurements)
- CosmeticCorrection
- autocrop
- astrometry
- FastIntegration
- CombineRGB
- LDD/LPS
- interactive LN
- Debayer with separate channels

Anything uncertain (a missing file, an ambiguous name, an unknown frame count) is dropped, never guessed. A dropped
entry only means WBPP redoes that work.

## Safety

- `--install` refuses to run while PixInsight is running, because PixInsight would overwrite the cache on exit.
- Before writing, the existing cache and `core-NNN-pxi.settings` are copied to `<config dir>/backup-YYYYmmdd-HHMMSS/`.
  The cache is written atomically.
- PixInsight stores the interrupted run's configuration, including an empty copy of the cache. Reloading it (step 4)
  would wipe the rebuilt cache. The tool therefore removes `cache_v2` from that stored configuration, but only if the
  configuration belongs to the same output directory. Use `--no-settings-patch` to skip this.

## Limitations

- Tested against WBPP 3.0.1 logs only. The log format and cache layout are internal to WBPP and may change, so
  other versions are refused unless `--force-version` is given.
- macOS is the default target. On other systems, pass `--config-dir`.

Tests: `python3 -m unittest pixinsight/test_wbpp_rebuild_cache.py`.
