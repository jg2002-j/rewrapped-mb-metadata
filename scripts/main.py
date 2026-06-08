import duckdb
import os
import sys

from schema import initialize_bare_sqlite_schema, apply_optimized_indexes
from utils import get_tar_paths, extract_and_stream_to_duckdb, cleanup_temp_files, TABLE_MAPPING


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sql_path = os.path.join(script_dir, "transformations.sql")
    target_sqlite_name = "metadata.db"

    # Reset working space environment
    if os.path.exists(target_sqlite_name):
        os.remove(target_sqlite_name)
    cleanup_temp_files()

    con = duckdb.connect('engine_runtime.duckdb')

    try:
        con.execute("PRAGMA memory_limit='4GB'")
        con.execute("PRAGMA temp_directory='duckdb_spill_buffer'")

        # Unpack raw entities through imported utilities
        archives = get_tar_paths()
        for archive in archives:
            if not os.path.exists(archive):
                print(f"Critical execution barrier: File target missing -> {archive}")
                sys.exit(1)
            for table in TABLE_MAPPING.keys():
                extract_and_stream_to_duckdb(con, archive, table)

        # Attach SQLite Database plugin layer
        con.execute("INSTALL sqlite;")
        con.execute("LOAD sqlite;")
        con.execute(f"ATTACH '{target_sqlite_name}' AS target_sqlite (TYPE SQLITE);")

        print("Constructing remote destination tables...")
        initialize_bare_sqlite_schema(con)

        print("Initiating analytical transformations...")
        if os.path.exists(sql_path):
            with open(sql_path, 'r') as query_file:
                transformation_queries = query_file.read()
            con.execute(transformation_queries)
            print("Transformations completed successfully.")
        else:
            print(f"Warning: {sql_path} not found. Skipping data load steps.")

        # Sever the live pipeline to allow index attachment locks safely
        print("Generating optimized fast search indexes...")
        con.execute("DETACH target_sqlite;")
        con.close()

        # Call isolated indexing logic
        apply_optimized_indexes(target_sqlite_name)
        print("Pipeline compilation routines terminated with explicit success.")

    finally:
        try:
            con.close()
        except Exception:
            pass
        cleanup_temp_files()


if __name__ == "__main__":
    main()