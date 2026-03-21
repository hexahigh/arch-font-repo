#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a single package")
    parser.add_argument(
        "package_name", help="Package directory name under packages/")
    parser.add_argument(
        "--sign",
        action="store_true",
        help="Sign the built package with makepkg",
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

    package_name = args.package_name
    root_dir = Path(__file__).resolve().parent.parent
    pkg_dir = root_dir / "packages" / package_name
    out_dir = root_dir / "repo" / "x86_64"
    src_cache_dir = root_dir / "build" / "sources" / package_name
    srcpkg_dir = root_dir / "build" / "srcpkgs" / package_name
    build_dir = root_dir / "build" / "work" / package_name

    if not pkg_dir.is_dir():
        print(f"Package not found: {package_name}")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    src_cache_dir.mkdir(parents=True, exist_ok=True)
    srcpkg_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PKGDEST"] = str(out_dir)
    env["SRCDEST"] = str(src_cache_dir)
    env["SRCPKGDEST"] = str(srcpkg_dir)
    env["BUILDDIR"] = str(build_dir)

    cmd = [
        "makepkg",
        "--syncdeps",
        "--noconfirm",
        "--cleanbuild",
        "--clean",
        "--force",
    ]
    if args.sign:
        cmd.extend(["--sign", "--key", args.key_id])

    subprocess.run(cmd, cwd=str(pkg_dir), env=env, check=True)

    pkg_files = sorted(
        [
            p for p in out_dir.glob(f"{package_name}-*.pkg.tar.*")
            if p.is_file() and not p.name.endswith(".sig")
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not pkg_files:
        print(
            f"No built package matched: {out_dir / f'{package_name}-*.pkg.tar.*'}")
        return 1

    latest_pkg = pkg_files[0]

    # Keep only the freshest package artifact for this package name.
    for old_pkg in pkg_files[1:]:
        old_pkg.unlink(missing_ok=True)

    print(f"Built package: {latest_pkg}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)
