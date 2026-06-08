import os
import shutil
import subprocess
import sys
import logging
import time
import tarfile
import urllib.request

# Bind our execution parser safely to the manifest contract specification
from mb_manifest import MUSICBRAINZ_MANIFEST

logger = logging.getLogger("pipeline_engine")

# Dynamically reverse-engineer dump identities using the schema manifest contract
TABLE_MAPPING = {meta["dump_file_name"]: meta["raw_table_name"] for meta in MUSICBRAINZ_MANIFEST.values()}

def stream_and_load_musicbrainz(con):
    """Executes an isolated streaming extraction pipeline without running out of disk space."""
    is_prod = os.environ.get("GITHUB_ACTIONS") == "true" or "--prod" in sys.argv

    if not is_prod:
        logger.info("Local test mode active. Looking for test_mbdump.tar.bz2")
        if not os.path.exists("test_mbdump.tar.bz2"):
            raise FileNotFoundError("Missing local development target asset: 'test_mbdump.tar.bz2'")
        tar_stream = open("test_mbdump.tar.bz2", "rb")
    else:
        logger.info("Resolving the latest weekly production export directory path...")
        req = urllib.request.Request("https://data.musicbrainz.org/pub/musicbrainz/data/fullexport/LATEST")
        latest_folder = urllib.request.urlopen(req).read().decode('utf-8').strip()
        url = f"https://ftp.musicbrainz.org/pub/musicbrainz/data/fullexport/{latest_folder}/mbdump.tar.bz2"

        logger.info(f"Opening live network streaming pipe directly from: {url}")
        proc = subprocess.Popen(["curl", "-sSLf", url], stdout=subprocess.PIPE)
        tar_stream = proc.stdout

    logger.info("Streaming pipeline active. Extracting and staging tables sequentially...")

    with tarfile.open(fileobj=tar_stream, mode='r|bz2') as tar:
        for member in tar:
            if not member.isfile():
                continue

            name_parts = member.name.split('/')
            if len(name_parts) != 2:
                continue

            internal_name = name_parts[1]
            if internal_name in TABLE_MAPPING:
                target_table = TABLE_MAPPING[internal_name]
                temp_tsv_path = f"temp_stream_{internal_name}.tsv"

                logger.info(f"  -> Extracting and staging archive node: {internal_name}")
                with open(temp_tsv_path, 'wb') as f:
                    shutil.copyfileobj(tar.extractfile(member), f)

                logger.info(f"  -> Bulk parsing {target_table} into DuckDB workspace using contract definition...")

                # Fetch structural schema layout from our manifest specification code file
                manifest_key = next(k for k, v in MUSICBRAINZ_MANIFEST.items() if v["dump_file_name"] == internal_name)
                columns_config = MUSICBRAINZ_MANIFEST[manifest_key]["columns"]

                # Enforce chronological ordering based on physical layout index configuration
                sorted_columns = sorted(columns_config.items(), key=lambda x: x[1]["pos"])
                columns_def = ", ".join([f"'{col_name}': '{col_meta['type']}'" for col_name, col_meta in sorted_columns])

                parse_start = time.time()
                con.execute(f"DROP TABLE IF EXISTS {target_table};")

                # Hardened configuration flags bypass dialect sniffer, preventing string interpolation failures
                con.execute(f"""
                    CREATE TABLE {target_table} AS 
                    SELECT * FROM read_csv(
                        '{temp_tsv_path}', 
                        delim='\\t', 
                        header=False, 
                        nullstr=$$\\N$$, 
                        quote='',
                        all_varchar=False,
                        auto_detect=False,
                        strict_mode=False,
                        null_padding=True,
                        columns={{{columns_def}}}
                    )
                """)

                os.remove(temp_tsv_path)
                row_count = con.execute(f"SELECT COUNT(*) FROM {target_table}").fetchone()[0]
                logger.info(f"  -> {target_table} load finalized ({row_count} rows processed) in {time.time() - parse_start:.2f}s")

    if is_prod:
        proc.wait()

def cleanup_temp_files():
    """Purges intermediate files and temporary directory tracking structures to release disk sectors."""
    logger.info("Running disk buffer cache purges...")
    for f in ['engine_runtime.duckdb', 'engine_runtime.duckdb.wal'] + [f for f in os.listdir('.') if f.startswith('temp_stream_')]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass
    if os.path.exists('duckdb_spill_buffer'):
        try:
            shutil.rmtree('duckdb_spill_buffer')
        except Exception:
            pass