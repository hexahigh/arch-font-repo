#!/usr/bin/env python3
import argparse
import hashlib
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")
LICENSE_FILE_CANDIDATES = ("OFL.txt", "LICENSE.txt", "UFL.txt", "LICENCE.txt")
TOP_LEVEL_LICENSE_DIRS = ("apache", "ofl", "ufl")


@dataclass
class FontFamily:
    license_dir: str
    family_dir: Path
    family_slug: str
    family_name: str
    pkgname: str
    pkgver: str
    pkgdesc: str
    url: str
    license_value: str
    commit: str
    source_entries: list[str]
    sha256sums: list[str]
    license_local_name: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate packages/ttf-google-*/PKGBUILD entries from google/fonts"
    )
    parser.add_argument("--repo-url", default="https://github.com/google/fonts.git",
                        help="Upstream git repository URL")
    parser.add_argument("--upstream-dir", default="build/work/google-fonts-upstream",
                        help="Local checkout path for google/fonts")
    parser.add_argument("--packages-dir", default="packages",
                        help="Target packages directory")
    parser.add_argument("--refresh", action="store_true",
                        help="Fetch latest changes from upstream before generation")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite existing PKGBUILD files (default is skip existing)")
    parser.add_argument("--include-variable", action="store_true",
                        help="Include variable fonts (*.woff2) if present")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of families processed (0 means all)")
    parser.add_argument("--family", action="append", default=[],
                        help="Filter by family slug or package name (repeatable)")
    return parser.parse_args()


def run_git(repo_dir: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def clone_or_update_repo(repo_url: str, upstream_dir: Path, refresh: bool) -> None:
    if not upstream_dir.exists():
        upstream_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", repo_url,
                       str(upstream_dir)], check=True)
        return

    if refresh:
        run_git(upstream_dir, ["fetch", "--all", "--prune"])
        current_branch = run_git(
            upstream_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
        if current_branch != "main":
            run_git(upstream_dir, ["checkout", "main"])
        run_git(upstream_dir, ["pull", "--ff-only", "origin", "main"])


def parse_metadata_value(metadata_text: str, key: str) -> str | None:
    match = re.search(
        rf'^\s*{re.escape(key)}:\s*"([^"]+)"\s*$', metadata_text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def normalize_pkgver(raw_version: str | None) -> str:
    if not raw_version:
        return "1"

    match = re.search(r"([0-9]+(?:\.[0-9]+)+)", raw_version)
    if match:
        return match.group(1)

    version = raw_version.replace("Version", "").replace("version", "").strip()
    version = version.lstrip("vV")
    version = re.sub(r"[^0-9A-Za-z.+_-]", "", version)
    return version or "1"


def normalize_pkgdesc(text: str) -> str:
    cleaned = " ".join(text.split())
    cleaned = cleaned.replace('"', "")
    return cleaned[:200]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def map_license(license_dir: str, metadata_license: str | None) -> str:
    if metadata_license:
        normalized = metadata_license.upper()
        if normalized in {"OFL", "OFL11", "UFL", "APACHE2", "APACHE-2.0"}:
            if normalized.startswith("APACHE"):
                return "Apache"
            if normalized.startswith("OFL"):
                return "OFL"
            return "UFL"

    if license_dir == "apache":
        return "Apache"
    if license_dir == "ofl":
        return "OFL"
    if license_dir == "ufl":
        return "UFL"
    return "custom"


def resolve_font_extensions(include_variable: bool) -> tuple[str, ...]:
    if include_variable:
        return (*FONT_EXTENSIONS, ".woff2")
    return FONT_EXTENSIONS


def get_latest_commit_for_path(upstream_dir: Path, repo_relative_path: str) -> str:
    return run_git(upstream_dir, ["log", "-1", "--format=%H", "--", repo_relative_path])


def get_commit_date_pkgver(upstream_dir: Path, commit: str) -> str:
    value = run_git(
        upstream_dir, ["show", "-s", "--format=%cd", "--date=format:%Y%m%d", commit])
    return value or "1"


def read_sfnt_version_from_offset(blob: bytes, base_offset: int = 0) -> str | None:
    if base_offset + 12 > len(blob):
        return None

    num_tables = struct.unpack_from(">H", blob, base_offset + 4)[0]
    table_dir_offset = base_offset + 12
    name_table_offset = None

    for index in range(num_tables):
        record_offset = table_dir_offset + index * 16
        if record_offset + 16 > len(blob):
            return None

        tag = blob[record_offset:record_offset + 4]
        _, table_offset, _ = struct.unpack_from(
            ">III", blob, record_offset + 4)
        if tag == b"name":
            name_table_offset = base_offset + table_offset
            break

    if name_table_offset is None or name_table_offset + 6 > len(blob):
        return None

    _, count, string_offset = struct.unpack_from(
        ">HHH", blob, name_table_offset)
    records_offset = name_table_offset + 6
    string_base = name_table_offset + string_offset

    candidates: list[str] = []
    for index in range(count):
        rec_offset = records_offset + index * 12
        if rec_offset + 12 > len(blob):
            break

        platform_id, _, _, name_id, length, offset = struct.unpack_from(
            ">HHHHHH", blob, rec_offset)
        if name_id != 5:
            continue

        value_start = string_base + offset
        value_end = value_start + length
        if value_end > len(blob) or value_start < 0:
            continue

        raw = blob[value_start:value_end]
        try:
            if platform_id in (0, 3):
                decoded = raw.decode("utf-16-be", errors="ignore")
            else:
                decoded = raw.decode("latin-1", errors="ignore")
        except Exception:
            continue

        normalized = normalize_pkgver(decoded)
        if normalized != "1":
            candidates.append(normalized)

    if not candidates:
        return None
    return sorted(candidates)[-1]


def read_font_version(path: Path) -> str | None:
    try:
        blob = path.read_bytes()
    except Exception:
        return None

    if blob.startswith(b"ttcf") and len(blob) >= 12:
        num_fonts = struct.unpack_from(">I", blob, 8)[0]
        for index in range(num_fonts):
            offset_pos = 12 + index * 4
            if offset_pos + 4 > len(blob):
                break
            font_offset = struct.unpack_from(">I", blob, offset_pos)[0]
            version = read_sfnt_version_from_offset(blob, font_offset)
            if version:
                return version
        return None

    return read_sfnt_version_from_offset(blob, 0)


def parse_metadata_filenames(metadata_text: str) -> list[str]:
    return re.findall(r'^\s*filename:\s*"([^"]+)"\s*$', metadata_text, flags=re.MULTILINE)


def resolve_font_file_from_metadata(family_dir: Path, metadata_filename: str) -> Path | None:
    direct = family_dir / metadata_filename
    if direct.exists() and direct.is_file():
        return direct

    basename = Path(metadata_filename).name
    matches = [p for p in family_dir.rglob(basename) if p.is_file()]
    if len(matches) == 1:
        return matches[0]
    return None


def collect_font_files(family_dir: Path, metadata_text: str, include_variable: bool) -> list[Path]:
    font_extensions = resolve_font_extensions(include_variable)
    from_metadata: list[Path] = []
    seen: set[str] = set()

    for metadata_filename in parse_metadata_filenames(metadata_text):
        path = resolve_font_file_from_metadata(family_dir, metadata_filename)
        if not path:
            continue
        if path.suffix.lower() not in font_extensions:
            continue

        key = path.as_posix()
        if key in seen:
            continue
        seen.add(key)
        from_metadata.append(path)

    if from_metadata:
        return sorted(from_metadata)

    return sorted(
        path
        for path in family_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in font_extensions
    )


def collect_families(upstream_dir: Path, include_variable: bool) -> list[tuple[str, Path]]:
    font_extensions = resolve_font_extensions(include_variable)
    families: list[tuple[str, Path]] = []

    for license_dir in TOP_LEVEL_LICENSE_DIRS:
        root = upstream_dir / license_dir
        if not root.is_dir():
            continue

        for family_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            has_fonts = any(
                candidate.is_file() and candidate.suffix.lower() in font_extensions
                for candidate in family_dir.rglob("*")
            )
            if has_fonts:
                families.append((license_dir, family_dir))

    return families


def find_license_file(upstream_dir: Path, license_dir: str, family_dir: Path) -> Path | None:
    for candidate_name in LICENSE_FILE_CANDIDATES:
        direct = family_dir / candidate_name
        if direct.is_file():
            return direct

    for candidate_name in LICENSE_FILE_CANDIDATES:
        recursive_matches = sorted(path for path in family_dir.rglob(
            candidate_name) if path.is_file())
        if recursive_matches:
            return recursive_matches[0]

    license_root = upstream_dir / license_dir
    for candidate_name in LICENSE_FILE_CANDIDATES:
        root_matches = sorted(path for path in license_root.rglob(
            candidate_name) if path.is_file())
        if root_matches:
            return root_matches[0]

    repo_root_fallbacks = [upstream_dir /
                           "LICENSE", upstream_dir / "LICENSE.txt"]
    for fallback in repo_root_fallbacks:
        if fallback.is_file():
            return fallback

    return None


def build_family_model(upstream_dir: Path, license_dir: str, family_dir: Path, include_variable: bool) -> FontFamily:
    family_slug = family_dir.name.lower()
    pkgname = f"ttf-google-{family_slug}"
    metadata_file = family_dir / "METADATA.pb"
    metadata_text = metadata_file.read_text(
        encoding="utf-8", errors="ignore") if metadata_file.exists() else ""

    family_name = parse_metadata_value(
        metadata_text, "name") or family_slug.replace("-", " ").title()
    family_version = parse_metadata_value(metadata_text, "version")
    metadata_license = parse_metadata_value(metadata_text, "license")

    pkgdesc = normalize_pkgdesc(f"Google Fonts family: {family_name}")
    specimen_name = family_name.replace(" ", "+")
    url = f"https://fonts.google.com/specimen/{specimen_name}"
    license_value = map_license(license_dir, metadata_license)

    repo_relative_family = f"{license_dir}/{family_dir.name}"
    commit = get_latest_commit_for_path(upstream_dir, repo_relative_family)
    if not commit:
        commit = run_git(upstream_dir, ["rev-parse", "HEAD"])

    font_files = collect_font_files(
        family_dir, metadata_text, include_variable)
    if not font_files:
        raise RuntimeError(f"No font files found in {family_dir}")

    if family_version:
        pkgver = normalize_pkgver(family_version)
    else:
        inferred_versions = [v for v in (
            read_font_version(p) for p in font_files) if v]
        if inferred_versions:
            pkgver = sorted(inferred_versions)[-1]
        else:
            pkgver = get_commit_date_pkgver(upstream_dir, commit)

    license_file = find_license_file(upstream_dir, license_dir, family_dir)
    if license_file is None:
        raise RuntimeError(f"No supported license file found in {family_dir}")

    all_files = [license_file, *font_files]
    source_entries: list[str] = []
    sha256sums: list[str] = []
    download_name_by_repo_path: dict[str, str] = {}

    for path in all_files:
        repo_relative = path.relative_to(upstream_dir).as_posix()
        encoded_repo_relative = quote(repo_relative, safe="/")
        source_entries.append(
            f'"https://github.com/google/fonts/raw/$_commit/{encoded_repo_relative}"')
        download_name_by_repo_path[repo_relative] = path.name

        sha256sums.append(f'"{sha256_file(path)}"')

    license_repo_relative = license_file.relative_to(upstream_dir).as_posix()

    return FontFamily(
        license_dir=license_dir,
        family_dir=family_dir,
        family_slug=family_slug,
        family_name=family_name,
        pkgname=pkgname,
        pkgver=pkgver,
        pkgdesc=pkgdesc,
        url=url,
        license_value=license_value,
        commit=commit,
        source_entries=source_entries,
        sha256sums=sha256sums,
        license_local_name=download_name_by_repo_path[license_repo_relative],
    )


def render_pkgbuild(model: FontFamily) -> str:
    source_lines = "\n".join(f"  {entry}" for entry in model.source_entries)
    checksum_lines = "\n".join(f"  {entry}" for entry in model.sha256sums)

    return f"""pkgname={model.pkgname}
pkgver={model.pkgver}
pkgrel=1
_commit=\"{model.commit}\"  # Latest commit touching {model.license_dir}/{model.family_dir.name}
pkgdesc=\"{model.pkgdesc}\"
arch=('any')
url=\"{model.url}\"
license=('{model.license_value}')
depends=('fontconfig')
provides=('ttf-font')
source=(
{source_lines}
)
sha256sums=(
{checksum_lines}
)

package() {{
  install -dm755 "${{pkgdir}}/usr/share/fonts/TTF/${{pkgname}}"

  shopt -s nullglob
  for pattern in *.ttf *.otf *.ttc *.woff2; do
    files=("${{srcdir}}"/${{pattern}})
    (( ${{#files[@]}} )) || continue
    install -m644 "${{files[@]}}" "${{pkgdir}}/usr/share/fonts/TTF/${{pkgname}}/"
  done
  shopt -u nullglob

  install -Dm644 "${{srcdir}}/{model.license_local_name}" "${{pkgdir}}/usr/share/licenses/${{pkgname}}/LICENSE"
}}
"""


def write_pkgbuild(packages_dir: Path, model: FontFamily, overwrite: bool) -> bool:
    package_dir = packages_dir / model.pkgname
    package_dir.mkdir(parents=True, exist_ok=True)
    pkgbuild_path = package_dir / "PKGBUILD"

    if pkgbuild_path.exists() and not overwrite:
        return False

    pkgbuild_path.write_text(render_pkgbuild(model), encoding="utf-8")
    return True


def main() -> int:
    args = parse_args()
    root_dir = Path(__file__).resolve().parent.parent
    upstream_dir = (root_dir / args.upstream_dir).resolve()
    packages_dir = (root_dir / args.packages_dir).resolve()

    clone_or_update_repo(args.repo_url, upstream_dir, args.refresh)

    families = collect_families(
        upstream_dir, include_variable=args.include_variable)
    if args.family:
        family_filters = {item.lower() for item in args.family}
        filtered: list[tuple[str, Path]] = []
        for license_dir, family_dir in families:
            slug = family_dir.name.lower()
            pkgname = f"ttf-google-{slug}"
            if slug in family_filters or pkgname in family_filters:
                filtered.append((license_dir, family_dir))
        families = filtered

    if args.limit > 0:
        families = families[: args.limit]

    if not families:
        print("No font families discovered.")
        return 1

    generated = 0
    skipped = 0

    for license_dir, family_dir in families:
        try:
            model = build_family_model(
                upstream_dir, license_dir, family_dir, include_variable=args.include_variable)
            did_write = write_pkgbuild(
                packages_dir, model, overwrite=args.overwrite)
        except Exception as exc:
            print(
                f"[error] {license_dir}/{family_dir.name}: {exc}", file=sys.stderr)
            continue

        if did_write:
            generated += 1
            print(f"[write] {model.pkgname}")
        else:
            skipped += 1
            print(f"[skip]  {model.pkgname} (already exists)")

    print(
        f"Finished. generated={generated} skipped={skipped} total={len(families)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
