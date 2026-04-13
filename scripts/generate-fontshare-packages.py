#!/usr/bin/env python3
import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


API_BASE = "https://api.fontshare.com/v2"
FONTSHARE_SITE_BASE = "https://www.fontshare.com/fonts"


@dataclass
class FontFamily:
    slug: str
    download_slug: str
    name: str
    pkgname: str
    pkgver: str
    pkgdesc: str
    url: str
    license_value: str
    source_url: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate packages/ttf-fontshare-*/PKGBUILD entries from Fontshare API"
    )
    parser.add_argument("--packages-dir", default="packages",
                        help="Target packages directory")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing PKGBUILD files (default is skip existing)",
    )
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of families processed")
    parser.add_argument(
        "--family",
        action="append",
        default=[],
        help="Filter by family slug or package name (repeatable)",
    )
    return parser.parse_args()


def fetch_json(url: str) -> dict:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "arch-font-repo-fontshare-generator/1.0",
        },
    )
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_pkgver(raw_version: str | None) -> str:
    if not raw_version:
        return "1"
    match = re.search(r"([0-9]+(?:\.[0-9]+)+)", raw_version)
    if match:
        return match.group(1)
    cleaned = raw_version.strip().lstrip("vV")
    cleaned = re.sub(r"[^0-9A-Za-z.+_-]", "", cleaned)
    return cleaned or "1"


def normalize_pkgdesc(text: str) -> str:
    cleaned = " ".join(text.split()).replace('"', "")
    return cleaned[:200]


def map_license(license_type: str | None) -> str:
    if not license_type:
        return "custom"
    normalized = license_type.strip().lower()
    if normalized == "sil_ofl":
        return "OFL"
    if normalized == "itf_ffl":
        return "custom:ITF-FFL"
    return "custom"


def fetch_all_families() -> list[dict]:
    page = 1
    limit = 100
    families: list[dict] = []
    while True:
        payload = fetch_json(f"{API_BASE}/fonts?page={page}&limit={limit}")
        batch = payload.get("fonts", [])
        if not batch:
            break
        families.extend(batch)
        total = payload.get("count_total", len(families))
        if len(families) >= total:
            break
        page += 1
    return sorted(
        (item for item in families if item.get("slug") and item.get("name")),
        key=lambda item: str(item.get("slug", "")).lower(),
    )


def build_family_model(raw: dict) -> FontFamily:
    download_slug = str(raw["slug"]).strip()
    slug = download_slug.lower()
    name = str(raw["name"]).strip()
    pkgname = f"ttf-fontshare-{slug}"
    pkgver = normalize_pkgver(raw.get("version"))
    pkgdesc = normalize_pkgdesc(f"Fontshare family: {name}")
    url = f"{FONTSHARE_SITE_BASE}/{quote(download_slug)}/"
    license_value = map_license(raw.get("license_type"))
    source_url = f"{API_BASE}/fonts/download/{quote(download_slug)}"
    return FontFamily(
        slug=slug,
        download_slug=download_slug,
        name=name,
        pkgname=pkgname,
        pkgver=pkgver,
        pkgdesc=pkgdesc,
        url=url,
        license_value=license_value,
        source_url=source_url,
    )


def render_pkgbuild(model: FontFamily) -> str:
    return f"""pkgname={model.pkgname}
pkgver={model.pkgver}
pkgrel=1
pkgdesc=\"{model.pkgdesc}\"
arch=('any')
url=\"{model.url}\"
license=('{model.license_value}')
depends=('fontconfig')
provides=('ttf-font')
source=(\"${{pkgname}}-${{pkgver}}.zip::{model.source_url}\")
# Fontshare regenerates zip archives with request-time timestamps, so checksums are not stable.
sha256sums=('SKIP')

package() {{
  install -dm755 "${{pkgdir}}/usr/share/fonts/TTF/${{pkgname}}"

  # Install every shipped TTF/OTF/TTC font from the Fontshare bundle.
  find "${{srcdir}}" -type f \\
    '(' -name '*.ttf' -o -name '*.otf' -o -name '*.ttc' ')' \\
    ! -path '*/__MACOSX/*' ! -name '._*' -print0 | while IFS= read -r -d '' font; do
    install -m644 "${{font}}" "${{pkgdir}}/usr/share/fonts/TTF/${{pkgname}}/"
  done

  license_file="$(find "${{srcdir}}" -type f '(' -iname 'OFL.txt' -o -iname 'FFL.txt' -o -iname 'LICENSE*' ')' | head -n 1)"
  if [[ -n "${{license_file}}" ]]; then
    install -Dm644 "${{license_file}}" "${{pkgdir}}/usr/share/licenses/${{pkgname}}/LICENSE"
  fi
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
    packages_dir = (root_dir / args.packages_dir).resolve()

    families = fetch_all_families()
    if args.family:
        family_filters = {item.lower() for item in args.family}
        filtered: list[dict] = []
        for item in families:
            slug = str(item.get("slug", "")).lower()
            pkgname = f"ttf-fontshare-{slug}"
            if slug in family_filters or pkgname in family_filters:
                filtered.append(item)
        families = filtered

    if args.limit > 0:
        families = families[: args.limit]

    if not families:
        print("No font families discovered.")
        return 1

    generated = 0
    skipped = 0
    for family in families:
        slug = str(family.get("slug", "<unknown>"))
        try:
            model = build_family_model(family)
            did_write = write_pkgbuild(
                packages_dir, model, overwrite=args.overwrite)
        except (HTTPError, URLError, TimeoutError) as exc:
            print(
                f"[error] {slug}: download/metadata request failed: {exc}", file=sys.stderr)
            continue
        except Exception as exc:
            print(f"[error] {slug}: {exc}", file=sys.stderr)
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
