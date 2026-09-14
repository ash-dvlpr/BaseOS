#!/usr/bin/env python3
"""Reject prepared archives that predate required harvest-list additions."""

import argparse
from pathlib import Path, PurePosixPath
import tarfile

from prepare_stock import load_harvest_entries, load_profile, omission_allowed


def verify_harvest(archive: Path, manifest: Path, profile: dict) -> None:
    with tarfile.open(archive) as source:
        members = {str(PurePosixPath(member.name)) for member in source}
    missing = [
        path for path, category in load_harvest_entries(manifest)
        if path.lstrip("/") not in members
        and not omission_allowed(path, category, profile)
    ]
    if missing:
        raise ValueError(
            "prepared harvest is missing required paths from the current manifest: "
            + ", ".join(missing)
            + f". Run ./prepare-stock.sh {profile['id']} IMAGE to refresh it; "
            "the published prepared bundle may also need refreshing."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("profiles", type=Path)
    parser.add_argument("target")
    args = parser.parse_args()
    verify_harvest(args.archive, args.manifest, load_profile(args.profiles, args.target))
    print(f"harvest paths OK: {args.target}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, tarfile.TarError) as error:
        raise SystemExit(f"verify-harvest: {error}")
