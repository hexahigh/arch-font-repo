#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from pathlib import Path


def update_symlink(link_path: Path, target_name: str) -> None:
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()
    link_path.symlink_to(target_name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update repository metadata")
    parser.add_argument(
        "--sign",
        action="store_true",
        help="Sign repository database via repo-add",
    )
    parser.add_argument(
        "--key-id",
        default=os.environ.get("REPO_SIGN_KEY_ID"),
        help="GPG key ID used when --sign is enabled (or set REPO_SIGN_KEY_ID)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sign and not args.key_id:
        print("Signing requested but no key ID provided. Use --key-id or set REPO_SIGN_KEY_ID.")
        return 1

    repo_name = os.environ.get("REPO_NAME", "arch-font-repo")
    root_dir = Path(__file__).resolve().parent.parent
    repo_dir = root_dir / "repo" / "x86_64"
    repo_dir.mkdir(parents=True, exist_ok=True)

    pkgs = sorted(
        [
            p for p in repo_dir.glob("*.pkg.tar.*")
            if p.is_file() and not p.name.endswith(".sig")
        ]
    )
    if not pkgs:
        print(
            f"No packages found in {repo_dir}. Build at least one package first.")
        return 1

    repo_db = repo_dir / f"{repo_name}.db.tar.gz"
    repo_files = repo_dir / f"{repo_name}.files.tar.gz"

    # Rebuild DB metadata from current package files so CSIZE stays in sync
    # even when a package is rebuilt without changing pkgver/pkgrel.
    for p in (
        repo_db,
        repo_files,
        repo_dir / f"{repo_name}.db",
        repo_dir / f"{repo_name}.files",
    ):
        if p.exists() or p.is_symlink():
            p.unlink()

    cmd = ["repo-add"]
    if args.sign:
        cmd.extend(["--sign", "--key", args.key_id])
    cmd.extend([str(repo_db)] + [str(p) for p in pkgs])
    subprocess.run(cmd, check=True)

    update_symlink(repo_dir / f"{repo_name}.db", repo_db.name)
    update_symlink(repo_dir / f"{repo_name}.files", repo_files.name)

    print(f"Updated repository metadata for {repo_name} in {repo_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)
