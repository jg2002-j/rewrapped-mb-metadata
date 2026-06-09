import os
import sys
import time
import logging
from collections import OrderedDict
from contextlib import contextmanager

import duckdb
import guardrails
from utils import stream_and_load_musicbrainz, cleanup_temp_files
from schema import initialize_native_sqlite_schema, apply_optimized_indexes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("pipeline_engine")

# Anchor code assets (transformations.sql) to this file's directory so the
# pipeline works regardless of the current working directory (e.g. when the
# workflow runs `python scripts/main.py` from the repo root).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# The output database is written to the CWD so the CI "Compress Output Database"
# step (which runs from the repo root) finds it by the same name.
TARGET_SQLITE_NAME = "metadata.db"

# Ordered record of how long each phase took, printed as a summary at the end
# (and on failure, so a crash still shows where the time went).
_PHASE_TIMES = OrderedDict()


@contextmanager
def phase(name):
    """Time a pipeline phase and log its duration; accumulate for the summary."""
    logger.info(f"[PHASE START] {name}")
    start = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - start
        _PHASE_TIMES[name] = _PHASE_TIMES.get(name, 0.0) + elapsed
        logger.info(f"[PHASE DONE ] {name} -- {elapsed:.2f}s")


def _log_phase_summary(total_elapsed):
    logger.info("=" * 60)
    logger.info("PHASE TIMING SUMMARY")
    logger.info("-" * 60)
    for name, secs in _PHASE_TIMES.items():
        pct = (secs / total_elapsed * 100.0) if total_elapsed > 0 else 0.0
        logger.info(f"  {name:<34} {secs:>9.2f}s  ({pct:4.1f}%)")
    logger.info("-" * 60)
    logger.info(f"  {'TOTAL':<34} {total_elapsed:>9.2f}s")
    logger.info("=" * 60)


def main():
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("INITIATING MUSICBRAINZ ANALYTICS PIPELINE ENGINE")
    logger.info("=" * 60)

    cleanup_temp_files()
    target_sqlite_path = TARGET_SQLITE_NAME

    # Direct SQLite's temp/sort files (used heavily by CREATE INDEX and VACUUM INTO)
    # to the large work volume. On CI the default temp dir is /tmp on the SMALL root
    # filesystem, which maximize-build-space can leave nearly full -- producing
    # "database or disk is full" during the index build even though the work volume
    # has tens of GB free. SQLite honours SQLITE_TMPDIR (then TMPDIR) on Unix; these
    # are harmless on Windows (SQLite uses TMP/TEMP there).
    sqlite_tmp = os.path.join(os.getcwd(), "sqlite_tmp")
    os.makedirs(sqlite_tmp, exist_ok=True)
    os.environ["SQLITE_TMPDIR"] = sqlite_tmp
    os.environ["TMPDIR"] = sqlite_tmp
    # Resource guardrails (RAM / disk / runtime) enforcing the free-tier envelope,
    # locally and on CI alike. Pre-flight disk check fails fast before the long
    # ingest if the volume is too small.
    guard_cfg = guardrails.GuardrailConfig()
    guardrails.preflight_disk_check(guard_cfg)
    monitor = guardrails.GuardrailMonitor(hard_abort_mb=guard_cfg.hard_abort_mb()).start()
    pipeline_ok = False

    # On-disk DuckDB engine file (it spills to disk under the configured limits).
    logger.info("Initializing DuckDB storage engine runtime connection...")
    con = duckdb.connect("engine_runtime.duckdb")

    try:
        with phase("engine_configuration"):
            logger.info("Configuring engine hardware allocation boundaries...")
            # All tunable via env so the budget can be adjusted without code edits.
            # Targets the public 16 GB runner. NOTE: this is the *DuckDB* budget, not
            # the box -- the OS, the GitHub runner agent, Python/DuckDB overhead, and
            # the kernel page cache for the on-disk engine file all need room too.
            # 12 GB peaked ~12.5 GB RSS and starved the 16 GB runner ("lost
            # communication"); 10 GB keeps peak ~10.7 GB, leaving ~5 GB headroom.
            mem_limit = os.environ.get("PIPELINE_MEMORY_LIMIT", "10GB")
            temp_limit = os.environ.get("PIPELINE_TEMP_LIMIT", "30GB")
            # DuckDB's per-operator memory scales with thread count. 4 matches the
            # public runner's vCPUs. For a private 2-core/7GB runner, drop to
            # PIPELINE_THREADS=2 and PIPELINE_MEMORY_LIMIT=5GB.
            default_threads = max(1, min(4, os.cpu_count() or 2))
            threads = int(os.environ.get("PIPELINE_THREADS", str(default_threads)))

            con.execute(f"SET memory_limit='{mem_limit}';")
            con.execute(f"SET max_temp_directory_size='{temp_limit}';")
            con.execute("SET temp_directory='duckdb_spill_buffer';")
            # Lower peak memory on large aggregations/sorts; we never rely on row order.
            con.execute("SET preserve_insertion_order=false;")
            con.execute(f"SET threads={threads};")
            logger.info(f"Engine limits -> memory={mem_limit}, temp_dir_max={temp_limit}, threads={threads}")

            con.execute("INSTALL sqlite;")
            con.execute("LOAD sqlite;")

            # Step 1: Initialize real SQLite database via native sqlite3 library calls
            if os.path.exists(target_sqlite_path):
                os.remove(target_sqlite_path)
            initialize_native_sqlite_schema(target_sqlite_path)

            # Attach the genuine SQLite database to our main streaming engine context
            con.execute(f"ATTACH '{target_sqlite_path}' AS target_sqlite (TYPE SQLITE);")

        # Step 2: Stream, extract, and load raw musicbrainz data tables
        # (download + bz2 decompression + read_csv staging; per-table timings
        # are logged inside stream_and_load_musicbrainz).
        with phase("ingest_stream_and_load"):
            stream_and_load_musicbrainz(con)

        # Step 3: Run transformation workflows
        transform_script_path = os.path.join(BASE_DIR, "transformations.sql")
        if not os.path.exists(transform_script_path):
            raise FileNotFoundError(f"Missing required execution asset: {transform_script_path}")

        logger.info(f"Reading target analytical workflow queries from {transform_script_path}...")
        with open(transform_script_path, "r", encoding="utf-8") as f:
            sql_script = f.read()

        with phase("transformations_sql"):
            logger.info("Executing pure hash pipelines and phased table drop reductions...")
            con.execute(sql_script)

        # Step 4: Detach, release the engine's memory, then build indices natively
        con.execute("DETACH target_sqlite;")
        con.close()  # free DuckDB's memory before the native SQLite index build
        # Reclaim the on-disk engine file (tens of GB after the intermediates)
        # before the index build + VACUUM INTO, which need room for sort temp and
        # the compacted copy.
        for _f in ("engine_runtime.duckdb", "engine_runtime.duckdb.wal"):
            try:
                if os.path.exists(_f):
                    os.remove(_f)
            except Exception:
                pass

        with phase("index_and_compact"):
            apply_optimized_indexes(target_sqlite_path)

        pipeline_ok = True
        logger.info("=" * 60)
        logger.info(f"SUCCESS: Pipeline processing finalized in {time.time() - start_time:.2f}s")
        logger.info("=" * 60)

    except Exception as e:
        logger.critical("=" * 60)
        logger.critical(f"PIPELINE CRASHED DIAGNOSTIC DUMP: {str(e)}")
        logger.critical("=" * 60)
        raise e
    finally:
        try:
            con.close()  # idempotent; safe even if already closed above
        except Exception:
            pass
        cleanup_temp_files()
        total_elapsed = time.time() - start_time
        _log_phase_summary(total_elapsed)
        monitor.stop()
        # Always report; only raise (fail the process) when the pipeline otherwise
        # succeeded, so a real pipeline error isn't masked by a guardrail breach.
        guardrails.report_and_enforce(guard_cfg, monitor, total_elapsed, enforce=pipeline_ok)


if __name__ == "__main__":
    main()
