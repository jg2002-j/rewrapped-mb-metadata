import os
import tarfile

def extract_keys_and_slice(source_tar, output_tar):
    print(f"Analyzing {source_tar} to build a relationally sound test set...")
    if not os.path.exists(source_tar):
        print(f"Error: {source_tar} not found locally.")
        return

    # Track sets to maintain relational integrity
    track_ids = set()
    medium_ids = set()
    release_ids = set()
    release_group_ids = set()
    artist_credit_ids = set()
    artist_ids = set()
    recording_ids = set()
    url_ids = set()

    # Step 1: Read tracks first to establish our baseline sample
    print("  -> Phase 1: Sampling base tracks...")
    with tarfile.open(source_tar, "r:bz2") as src:
        track_member = src.getmember("mbdump/track")
        with src.extractfile(track_member) as f:
            for _ in range(300):  # Sample 300 tracks
                line = f.readline()
                if not line:
                    break
                parts = line.decode('utf-8', errors='ignore').split('\t')
                if len(parts) > 3:
                    track_ids.add(parts[0])      # id
                    recording_ids.add(parts[2])  # recording
                    medium_ids.add(parts[3])     # medium

    # Step 2: Read bridge tables to gather dependent entity keys
    print("  -> Phase 2: Gathering related keys across tables (Pass 1)...")
    with tarfile.open(source_tar, "r:bz2") as src:
        for member in src:
            if member.name == "mbdump/medium":
                with src.extractfile(member) as f:
                    for line in f:
                        parts = line.decode('utf-8', errors='ignore').split('\t')
                        if parts[0] in medium_ids:
                            release_ids.add(parts[1])

            elif member.name == "mbdump/release":
                with src.extractfile(member) as f:
                    for line in f:
                        parts = line.decode('utf-8', errors='ignore').split('\t')
                        if parts[0] in release_ids:
                            release_group_ids.add(parts[1])

            elif member.name == "mbdump/l_recording_url":
                with src.extractfile(member) as f:
                    for line in f:
                        parts = line.decode('utf-8', errors='ignore').split('\t')
                        if parts[2] in recording_ids:  # entity0 is recording
                            url_ids.add(parts[3])      # entity1 is url

    # Step 3: Gather artist and secondary layout keys
    print("  -> Phase 3: Gathering related keys across tables (Pass 2)...")
    with tarfile.open(source_tar, "r:bz2") as src:
        for member in src:
            if member.name == "mbdump/release_group":
                with src.extractfile(member) as f:
                    for line in f:
                        parts = line.decode('utf-8', errors='ignore').split('\t')
                        if parts[0] in release_group_ids:
                            artist_credit_ids.add(parts[3])

            elif member.name == "mbdump/artist_credit_name":
                with src.extractfile(member) as f:
                    for line in f:
                        parts = line.decode('utf-8', errors='ignore').split('\t')
                        if parts[0] in artist_credit_ids:
                            artist_ids.add(parts[1])

    # Step 4: Final dependency collection for Artist URLs
    print("  -> Phase 4: Finalizing URL and secondary mappings...")
    with tarfile.open(source_tar, "r:bz2") as src:
        for member in src:
            if member.name == "mbdump/l_artist_url":
                with src.extractfile(member) as f:
                    for line in f:
                        parts = line.decode('utf-8', errors='ignore').split('\t')
                        if parts[2] in artist_ids:  # entity0 is artist
                            url_ids.add(parts[3])   # entity1 is url

    # Step 5: Filter original archive and write matches to test tarball
    print(f"Writing relationally filtered records out to {output_tar}...")
    temp_filename = "temp_slice.tsv"

    with tarfile.open(source_tar, "r:bz2") as src, tarfile.open(output_tar, "w:bz2") as dst:
        for member in src:
            name = member.name
            # Maps table name to its identification key constraint index
            filter_rules = {
                "mbdump/track": (0, track_ids),
                "mbdump/recording": (0, recording_ids),
                "mbdump/medium": (0, medium_ids),
                "mbdump/release": (0, release_ids),
                "mbdump/release_group": (0, release_group_ids),
                "mbdump/artist_credit_name": (0, artist_credit_ids),
                "mbdump/artist": (0, artist_ids),
                "mbdump/url": (0, url_ids),
                "mbdump/l_recording_url": (2, recording_ids),
                "mbdump/l_artist_url": (2, artist_ids),
                "mbdump/release_group_primary_type": (None, None), # Keep all types for mapping
                "mbdump/rg_type": (None, None)
            }

            if name in filter_rules:
                idx, lookup_set = filter_rules[name]
                matched_lines = []

                with src.extractfile(member) as f:
                    for line in f:
                        if idx is None:
                            matched_lines.append(line) # pass through boilerplate definitions
                        else:
                            parts = line.decode('utf-8', errors='ignore').split('\t')
                            if len(parts) > idx and parts[idx] in lookup_set:
                                matched_lines.append(line)

                if matched_lines:
                    print(f"  -> Packaging {name}: ({len(matched_lines)} matched rows)")
                    with open(temp_filename, "wb") as tmp:
                        tmp.writelines(matched_lines)
                    dst.add(temp_filename, arcname=name)
                    os.remove(temp_filename)

    print("\nDone! Relational mini test dataset is completely ready.")

if __name__ == "__main__":
    extract_keys_and_slice("mbdump.tar.bz2", "test_mbdump.tar.bz2")