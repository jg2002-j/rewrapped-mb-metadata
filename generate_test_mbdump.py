import csv
import io
import os
import tarfile
import tempfile

csv.field_size_limit(16 * 1024 * 1024)

SAMPLE_RELEASES = 50


def reader_for(member_file):
    return csv.reader(
        io.TextIOWrapper(member_file, encoding="utf-8", errors="ignore"),
        delimiter="\t",
        quoting=csv.QUOTE_NONE,
        escapechar=None,
    )


def build_test_dump(source_tar, output_tar):
    if not os.path.exists(source_tar):
        raise FileNotFoundError(source_tar)

    release_ids = set()
    medium_ids = set()
    track_ids = set()
    recording_ids = set()
    release_group_ids = set()

    artist_credit_ids = set()
    artist_ids = set()

    url_ids = set()

    print("Phase 1: selecting releases")

    with tarfile.open(source_tar, "r:bz2") as tar:
        release_member = tar.getmember("mbdump/release")

        with tar.extractfile(release_member) as f:
            for i, row in enumerate(reader_for(f)):
                if i >= SAMPLE_RELEASES:
                    break

                release_ids.add(row[0])

                if len(row) > 4 and row[4] != r"\N":
                    release_group_ids.add(row[4])

                if len(row) > 3 and row[3] != r"\N":
                    artist_credit_ids.add(row[3])

    print(
        f"Selected {len(release_ids)} releases "
        f"across {len(release_group_ids)} release groups"
    )

    print("Phase 2: mediums")

    with tarfile.open(source_tar, "r:bz2") as tar:
        member = tar.getmember("mbdump/medium")

        with tar.extractfile(member) as f:
            for row in reader_for(f):
                if row[1] in release_ids:
                    medium_ids.add(row[0])

    print("Phase 3: tracks")

    with tarfile.open(source_tar, "r:bz2") as tar:
        member = tar.getmember("mbdump/track")

        with tar.extractfile(member) as f:
            for row in reader_for(f):
                if row[3] in medium_ids:

                    track_ids.add(row[0])

                    if row[2] != r"\N":
                        recording_ids.add(row[2])

                    if len(row) > 7 and row[7] != r"\N":
                        artist_credit_ids.add(row[7])

    print("Phase 4: recordings")

    with tarfile.open(source_tar, "r:bz2") as tar:
        member = tar.getmember("mbdump/recording")

        with tar.extractfile(member) as f:
            for row in reader_for(f):
                if row[0] in recording_ids:

                    if len(row) > 3 and row[3] != r"\N":
                        artist_credit_ids.add(row[3])

    print("Phase 5: release groups")

    with tarfile.open(source_tar, "r:bz2") as tar:
        member = tar.getmember("mbdump/release_group")

        with tar.extractfile(member) as f:
            for row in reader_for(f):
                if row[0] in release_group_ids:

                    if len(row) > 3 and row[3] != r"\N":
                        artist_credit_ids.add(row[3])

    print("Phase 6: artist credit names")

    with tarfile.open(source_tar, "r:bz2") as tar:
        member = tar.getmember("mbdump/artist_credit_name")

        with tar.extractfile(member) as f:
            for row in reader_for(f):
                if row[0] in artist_credit_ids:
                    artist_ids.add(row[2])

    print("Phase 7: URL relationships")

    with tarfile.open(source_tar, "r:bz2") as tar:

        member = tar.getmember("mbdump/l_recording_url")

        with tar.extractfile(member) as f:
            for row in reader_for(f):
                if row[2] in recording_ids:
                    url_ids.add(row[3])

        member = tar.getmember("mbdump/l_artist_url")

        with tar.extractfile(member) as f:
            for row in reader_for(f):
                if row[2] in artist_ids:
                    url_ids.add(row[3])

    print()
    print("Subset statistics")
    print("-----------------")
    print("release_groups :", len(release_group_ids))
    print("releases       :", len(release_ids))
    print("mediums        :", len(medium_ids))
    print("tracks         :", len(track_ids))
    print("recordings     :", len(recording_ids))
    print("artist_credits :", len(artist_credit_ids))
    print("artists        :", len(artist_ids))
    print("urls           :", len(url_ids))
    print()

    filters = {
        "mbdump/release": (0, release_ids),
        "mbdump/medium": (0, medium_ids),
        "mbdump/track": (0, track_ids),
        "mbdump/recording": (0, recording_ids),
        "mbdump/release_group": (0, release_group_ids),
        "mbdump/artist_credit_name": (0, artist_credit_ids),
        "mbdump/artist": (0, artist_ids),
        "mbdump/url": (0, url_ids),
        "mbdump/l_recording_url": (2, recording_ids),
        "mbdump/l_artist_url": (2, artist_ids),
        "mbdump/release_group_primary_type": (None, None),
    }

    print(f"Writing {output_tar}")

    with tarfile.open(source_tar, "r:bz2") as src, \
            tarfile.open(output_tar, "w:bz2") as dst:

        for member in src:

            if member.name not in filters:
                continue

            idx, lookup = filters[member.name]

            with tempfile.NamedTemporaryFile(
                    mode="w",
                    delete=False,
                    encoding="utf-8",
                    newline=""
            ) as tmp:

                # Fix applied here: Added escapechar to safely format rows
                writer = csv.writer(
                    tmp,
                    delimiter="\t",
                    lineterminator="\n",
                    quoting=csv.QUOTE_NONE,
                    escapechar="\\",
                )

                count = 0

                with src.extractfile(member) as f:
                    for row in reader_for(f):

                        keep = False

                        if idx is None:
                            keep = True

                        elif len(row) > idx and row[idx] in lookup:
                            keep = True

                        if keep:
                            writer.writerow(row)
                            count += 1

            if count > 0:
                info = tarfile.TarInfo(member.name)

                with open(tmp.name, "rb") as fh:
                    data = fh.read()

                info.size = len(data)

                dst.addfile(info, io.BytesIO(data))

                print(f"{member.name}: {count:,}")

            os.unlink(tmp.name)

    print()
    print("Done.")
    print(f"Created: {output_tar}")


if __name__ == "__main__":
    build_test_dump(
        "mbdump.tar.bz2",
        "test_mbdump.tar.bz2"
    )
