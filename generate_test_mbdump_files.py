import tarfile
import os

def create_mini_test_archive(source_tar, output_tar, target_files):
    print(f"Creating mini test archive: {output_tar}")
    if not os.path.exists(source_tar):
        print(f"Skipping {source_tar} (not found locally)")
        return

    with tarfile.open(source_tar, "r:bz2") as src, tarfile.open(output_tar, "w:bz2") as dst:
        for member in src:
            if member.name in target_files:
                print(f"  -> Truncating {member.name} to 500 rows...")
                # Read lines, keep the first 500
                f = src.extractfile(member)
                lines = [f.readline() for _ in range(500)]
                lines = [l for l in lines if l] # Filter out empty lines if file is short

                # Write back into a fresh temporary file
                temp_filename = "temp_test_slice.tsv"
                with open(temp_filename, "wb") as temp_out:
                    temp_out.writelines(lines)

                # Append the truncated file to our new test tarball
                dst.add(temp_filename, arcname=member.name)
                os.remove(temp_filename)

if __name__ == "__main__":
    mbdump_targets = [
        "mbdump/artist", "mbdump/artist_credit_name", "mbdump/release",
        "mbdump/release_group", "mbdump/release_group_primary_type",
        "mbdump/medium", "mbdump/track", "mbdump/recording", "mbdump/url",
        "mbdump/l_release_url", "mbdump/l_recording_url", "mbdump/l_artist_url",
        "l_artist_url"
    ]

    create_mini_test_archive("mbdump.tar.bz2", "test_mbdump.tar.bz2", mbdump_targets)
    print("Done! Mini test dataset is ready.")