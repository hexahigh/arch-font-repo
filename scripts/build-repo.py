#!/usr/bin/env python3
import argparse
from datetime import datetime, timezone
import html
import os
import re
import subprocess
import sys
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
import time
from urllib.parse import unquote


class Colors:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.reset = "\033[0m" if enabled else ""
        self.bold = "\033[1m" if enabled else ""
        self.dim = "\033[2m" if enabled else ""
        self.red = "\033[31m" if enabled else ""
        self.green = "\033[32m" if enabled else ""
        self.yellow = "\033[33m" if enabled else ""
        self.blue = "\033[34m" if enabled else ""
        self.cyan = "\033[36m" if enabled else ""

    def wrap(self, text: str, color: str) -> str:
        if not self.enabled:
            return text
        return f"{color}{text}{self.reset}"


def detect_color_enabled() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    return sys.stdout.isatty()


def default_jobs() -> int:
    cores = os.cpu_count() or 1
    return max(1, cores // 2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build all packages and update repo DB")
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=default_jobs(),
        help="Number of parallel package builds (default: half of CPU cores)",
    )
    parser.add_argument(
        "--sign",
        action="store_true",
        help="Sign built packages and repository metadata",
    )
    parser.add_argument(
        "--key-id",
        default=os.environ.get("REPO_SIGN_KEY_ID"),
        help="GPG key ID used when --sign is enabled (or set REPO_SIGN_KEY_ID)",
    )
    parser.add_argument(
        "--export-key",
        action="store_true",
        help="Export the public key to repo/afr.pub.asc",
    )
    parser.add_argument(
        "--generate-index",
        action="store_true",
        help="Generate repo/index.html from index.html.tmpl",
    )
    parser.add_argument(
        "--template",
        default="index.html.tmpl",
        help="Template file for index generation (default: index.html.tmpl)",
    )
    parser.add_argument(
        "--build-unchanged",
        action="store_true",
        help="Build packages even when repo already contains matching pkgver/pkgrel",
    )
    return parser.parse_args()


def parse_pkgbuild_version_release(pkgbuild_path: Path) -> tuple[str, str] | None:
    pkgver = None
    pkgrel = None

    for raw_line in pkgbuild_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        match = re.match(r"^(pkgver|pkgrel)\s*=\s*(.+)$", line)
        if not match:
            continue

        key, value = match.group(1), match.group(2).strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        if key == "pkgver":
            pkgver = value
        elif key == "pkgrel":
            pkgrel = value

        if pkgver is not None and pkgrel is not None:
            return (pkgver, pkgrel)

    return None


def repo_version_release_pairs(repo_x86_dir: Path, pkg_name: str) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for pkg in repo_x86_dir.glob(f"{pkg_name}-*.pkg.tar.*"):
        if pkg.name.endswith(".sig"):
            continue
        base = pkg.name.split(".pkg.tar.", 1)[0]
        parts = base.rsplit("-", 3)
        if len(parts) != 4:
            continue
        name, pkgver, pkgrel, _arch = parts
        if name != pkg_name:
            continue
        pairs.add((pkgver, pkgrel))
    return pairs


def export_public_key(key_id: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["gpg", "--armor", "--export", key_id],
        check=True,
        capture_output=True,
        text=True,
    )
    key_data = result.stdout.strip()
    if not key_data:
        raise RuntimeError(
            f"GPG exported no public key data for key ID: {key_id}")
    output_path.write_text(f"{key_data}\n", encoding="utf-8")


def package_names_from_repo(repo_x86_dir: Path) -> list[str]:
    names: set[str] = set()
    for pkg in repo_x86_dir.glob("*.pkg.tar.*"):
        if pkg.name.endswith(".sig"):
            continue
        base = pkg.name.split(".pkg.tar.", 1)[0]
        parts = base.rsplit("-", 3)
        if len(parts) != 4:
            continue
        pkgname = parts[0]
        if pkgname:
            names.add(pkgname)
    return sorted(names)


def latest_repo_packages(repo_x86_dir: Path) -> dict[str, Path]:
    latest: dict[str, Path] = {}
    for pkg in repo_x86_dir.glob("*.pkg.tar.*"):
        if pkg.name.endswith(".sig"):
            continue
        base = pkg.name.split(".pkg.tar.", 1)[0]
        parts = base.rsplit("-", 3)
        if len(parts) != 4:
            continue
        pkgname = parts[0]
        prev = latest.get(pkgname)
        if prev is None or pkg.stat().st_mtime > prev.stat().st_mtime:
            latest[pkgname] = pkg
    return latest


def normalize_font_label(raw_name: str) -> str:
    name = unquote(raw_name).strip()
    if not name:
        return ""
    name = re.sub(r"\.(ttf|otf|ttc|woff2?)$", "", name, flags=re.IGNORECASE)
    name = name.replace("_", " ")
    name = re.sub(r"\[(.*?)\]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def font_labels_from_package_archive(pkg_archive: Path) -> set[str]:
    labels: set[str] = set()
    try:
        result = subprocess.run(
            ["bsdtar", "-tf", str(pkg_archive)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return labels

    for line in result.stdout.splitlines():
        file_path = line.strip()
        if not file_path:
            continue
        if not re.search(r"\.(ttf|otf|ttc|woff2?)$", file_path, flags=re.IGNORECASE):
            continue
        label = normalize_font_label(Path(file_path).name)
        if label:
            labels.add(label)
    return labels


def parse_pkgbuild_string_field(pkgbuild_path: Path, field_name: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(field_name)}\s*=\s*(.+)$")
    for raw_line in pkgbuild_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        match = pattern.match(line)
        if not match:
            continue
        value = match.group(1).strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            return value[1:-1].strip()
        return value.strip()
    return None


def font_labels_from_pkgbuild_sources(pkgbuild_path: Path) -> set[str]:
    labels: set[str] = set()
    in_source = False

    for raw_line in pkgbuild_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue

        if not in_source and line.startswith("source=("):
            in_source = True
            if line.endswith(")"):
                in_source = False
            continue

        if not in_source:
            continue

        if line == ")":
            in_source = False
            continue

        if (line.startswith('"') and line.endswith('"')) or (line.startswith("'") and line.endswith("'")):
            source_item = line[1:-1]
        else:
            source_item = line

        source_item = source_item.split("::", 1)[-1]
        filename = source_item.rsplit("/", 1)[-1]
        filename = filename.split("?", 1)[0]
        if not re.search(r"\.(ttf|otf|ttc|woff2?)$", filename, flags=re.IGNORECASE):
            continue

        label = normalize_font_label(filename)
        if label:
            labels.add(label)

    return labels


def derive_font_name(pkg_name: str, pkg_desc: str | None) -> str:
    if pkg_desc:
        google_match = re.match(r"^Google Fonts family:\s*(.+)$", pkg_desc)
        if google_match:
            return google_match.group(1).strip()
        return pkg_desc.strip()

    base = re.sub(r"^(ttf|otf|woff2?)-", "", pkg_name)
    words = [w for w in base.replace("_", "-").split("-") if w]
    if not words:
        return pkg_name
    return " ".join(word.capitalize() for word in words)


def font_index_entries(
    package_names: list[str],
    packages_dir: Path,
    repo_x86_dir: Path,
) -> list[tuple[str, str]]:
    latest_packages = latest_repo_packages(repo_x86_dir)
    entries: list[tuple[str, str]] = []
    for pkg_name in package_names:
        labels: set[str] = set()

        pkg_archive = latest_packages.get(pkg_name)
        if pkg_archive is not None:
            labels = font_labels_from_package_archive(pkg_archive)

        pkgbuild_path = packages_dir / pkg_name / "PKGBUILD"
        if not labels and pkgbuild_path.is_file():
            labels = font_labels_from_pkgbuild_sources(pkgbuild_path)

        if not labels:
            pkg_desc = None
            if pkgbuild_path.is_file():
                pkg_desc = parse_pkgbuild_string_field(
                    pkgbuild_path, "pkgdesc")
            labels = {derive_font_name(pkg_name, pkg_desc)}

        for label in sorted(labels):
            entries.append((label, pkg_name))

    entries.sort(key=lambda item: (item[0].lower(), item[1]))
    return entries


def render_index_from_template(
    template_path: Path,
    output_path: Path,
    package_names: list[str],
    font_entries: list[tuple[str, str]],
) -> None:
    if not template_path.is_file():
        raise FileNotFoundError(f"Template file not found: {template_path}")

    package_items = "\n".join(
        f"      <li>{html.escape(name)}</li>" for name in package_names
    )
    if not package_items:
        package_items = "      <li>No packages found.</li>"

    font_items = "\n".join(
        (
            "      <li data-font-entry "
            f"data-font=\"{html.escape(font_name, quote=True)}\" "
            f"data-package=\"{html.escape(pkg_name, quote=True)}\">"
            f"<span class=\"font-name\">{html.escape(font_name)}</span>"
            f"<span class=\"package-name\">{html.escape(pkg_name)}</span>"
            "</li>"
        )
        for font_name, pkg_name in font_entries
    )
    if not font_items:
        font_items = "      <li>No fonts found.</li>"

    replacements = {
        "{{generated_at}}": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "{{package_count}}": str(len(package_names)),
        "{{package_list_items}}": package_items,
        "{{font_count}}": str(len(font_entries)),
        "{{font_search_items}}": font_items,
    }

    content = template_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        content = content.replace(key, value)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


def build_one_package(
    root_dir: Path,
    build_script: Path,
    pkg_name: str,
    log_file: Path,
    sign: bool,
    key_id: str,
) -> tuple[str, int, Path]:
    cmd = [sys.executable, str(build_script)]
    if sign:
        cmd.extend(["--sign", "--key-id", key_id])
    cmd.append(pkg_name)

    with log_file.open("w", encoding="utf-8") as fh:
        result = subprocess.run(
            cmd,
            stdout=fh,
            stderr=subprocess.STDOUT,
            cwd=str(root_dir),
            check=False,
        )
    return pkg_name, result.returncode, log_file


def render_worker_lines(lines: list[str], interactive: bool, rendered_once: bool) -> bool:
    if not interactive:
        return rendered_once

    if rendered_once:
        print(f"\033[{len(lines)}F", end="")

    for line in lines:
        print(f"\r{line}\033[K")
    print("", end="", flush=True)
    return True


def main() -> int:
    args = parse_args()
    jobs = args.jobs
    if jobs < 1:
        print("--jobs must be >= 1")
        return 1

    if args.sign and not args.key_id:
        print("Signing requested but no key ID provided. Use --key-id or set REPO_SIGN_KEY_ID.")
        return 1

    if args.export_key and not args.key_id:
        print("Key export requested but no key ID provided. Use --key-id or set REPO_SIGN_KEY_ID.")
        return 1

    root_dir = Path(__file__).resolve().parent.parent
    packages_dir = root_dir / "packages"
    log_dir = root_dir / "build" / "logs"
    repo_root_dir = root_dir / "repo"
    repo_x86_dir = repo_root_dir / "x86_64"
    colors = Colors(detect_color_enabled())

    if not packages_dir.is_dir():
        print(f"Packages directory not found: {packages_dir}")
        return 1

    log_dir.mkdir(parents=True, exist_ok=True)

    package_paths = sorted([p for p in packages_dir.iterdir() if p.is_dir()])
    if not package_paths:
        print(f"No package directories found under {packages_dir}")
        return 1

    total_discovered = len(package_paths)
    packages_to_build: list[str] = []
    skipped_unchanged: list[str] = []

    for package_path in package_paths:
        pkg_name = package_path.name
        if args.build_unchanged:
            packages_to_build.append(pkg_name)
            continue

        pkgbuild_path = package_path / "PKGBUILD"
        if not pkgbuild_path.is_file():
            packages_to_build.append(pkg_name)
            continue

        desired = parse_pkgbuild_version_release(pkgbuild_path)
        if desired is None:
            packages_to_build.append(pkg_name)
            continue

        existing_pairs = repo_version_release_pairs(repo_x86_dir, pkg_name)
        if desired in existing_pairs:
            skipped_unchanged.append(pkg_name)
        else:
            packages_to_build.append(pkg_name)

    total_packages = len(packages_to_build)
    built_count = 0
    failed_count = 0
    failed_packages = []

    build_package_script = root_dir / "scripts" / "build-package.py"
    update_repo_db_script = root_dir / "scripts" / "update-repo-db.py"

    print(
        f"{colors.wrap('Starting build', colors.bold)}: "
        f"discovered={total_discovered}, to_build={total_packages}, skipped_unchanged={len(skipped_unchanged)}, "
        f"jobs={jobs}, sign={'on' if args.sign else 'off'}, logs={log_dir}"
    )

    completed = 0
    interactive = sys.stdout.isatty()
    active_slots = min(jobs, total_packages)
    worker_lines = [
        f"[{colors.wrap('IDLE', colors.dim)}] slot {i + 1}/{active_slots}"
        for i in range(active_slots)
    ]
    rendered_once = False
    package_queue = list(packages_to_build)
    running: dict[Future, tuple[int, str, Path]] = {}

    def building_line(pkg_name: str, slot_idx: int) -> str:
        return (
            f"[{colors.wrap('BUILDING', colors.yellow)}] "
            f"{pkg_name:<40} slot {slot_idx + 1}/{active_slots}"
        )

    def result_line(pkg_name: str, ok: bool) -> str:
        status = colors.wrap("OK", colors.green) if ok else colors.wrap(
            "FAIL", colors.red)
        return (
            f"[{status}] {pkg_name:<40} "
            f"(done {completed}/{total_packages}, ok={built_count}, fail={failed_count})"
        )

    def submit_next(executor: ThreadPoolExecutor, slot_idx: int) -> None:
        if not package_queue:
            worker_lines[slot_idx] = f"[{colors.wrap('IDLE', colors.dim)}] slot {slot_idx + 1}/{active_slots}"
            return

        pkg_name = package_queue.pop(0)
        log_file = log_dir / f"{pkg_name}.log"
        worker_lines[slot_idx] = building_line(pkg_name, slot_idx)
        future = executor.submit(
            build_one_package,
            root_dir,
            build_package_script,
            pkg_name,
            log_file,
            args.sign,
            args.key_id,
        )
        running[future] = (slot_idx, pkg_name, log_file)

    if total_packages > 0:
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            for slot_idx in range(active_slots):
                submit_next(executor, slot_idx)
            rendered_once = render_worker_lines(
                worker_lines, interactive, rendered_once)

            while running:
                done, _ = wait(running.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    slot_idx, pkg_name, log_file = running.pop(future)
                    _, rc, _ = future.result()
                    completed += 1

                    if rc == 0:
                        built_count += 1
                    else:
                        failed_count += 1
                        failed_packages.append(pkg_name)

                    worker_lines[slot_idx] = result_line(
                        pkg_name, rc == 0)
                    rendered_once = render_worker_lines(
                        worker_lines, interactive, rendered_once)

                    # Slight delay so that the last status update is visible before the next build starts.
                    if rc != 0:
                        time.sleep(0.5)

                    submit_next(executor, slot_idx)
                    rendered_once = render_worker_lines(
                        worker_lines, interactive, rendered_once)
    else:
        print("No package builds required (all unchanged).")

    if interactive:
        print()

    repo_db_log = log_dir / "repo-db.log"
    repo_db_ok = 1
    repo_db_ran = False
    if built_count > 0:
        repo_db_ran = True
        repo_cmd = [sys.executable, str(update_repo_db_script)]
        if args.sign:
            repo_cmd.extend(["--sign", "--key-id", args.key_id])

        with repo_db_log.open("w", encoding="utf-8") as fh:
            result = subprocess.run(
                repo_cmd,
                stdout=fh,
                stderr=subprocess.STDOUT,
                cwd=str(root_dir),
                check=False,
            )
        if result.returncode == 0:
            repo_db_ok = 1
        else:
            repo_db_ok = 0

    print()
    print(colors.wrap("================ Build Summary ================", colors.cyan))
    print(
        f"Discovered     : {colors.wrap(str(total_discovered), colors.bold)}")
    print(f"Scheduled      : {colors.wrap(str(total_packages), colors.bold)}")
    print(
        f"Skipped        : {colors.wrap(str(len(skipped_unchanged)), colors.dim)}")
    print(f"Built          : {colors.wrap(str(built_count), colors.green)}")
    print(
        f"Failed         : {colors.wrap(str(failed_count), colors.red if failed_count else colors.green)}")
    print(f"Logs directory : {colors.wrap(str(log_dir), colors.dim)}")

    if failed_count > 0:
        print()
        print(colors.wrap("Failed packages:", colors.yellow))
        for pkg_name in failed_packages:
            print(f"  - {pkg_name}")

    print()
    if repo_db_ok == 1 and repo_db_ran:
        print(f"Repo metadata  : {colors.wrap('OK', colors.green)}")
    elif repo_db_ok == 1 and not repo_db_ran:
        print(
            f"Repo metadata  : {colors.wrap('SKIPPED', colors.dim)} (no new builds)")
    else:
        print(
            f"Repo metadata  : {colors.wrap('FAILED', colors.red)} (see {repo_db_log})")
    print(
        f"Artifacts      : {colors.wrap(str(root_dir / 'repo' / 'x86_64'), colors.dim)}")
    print(colors.wrap("===============================================", colors.cyan))

    extra_step_failed = False
    if args.export_key:
        key_out = repo_root_dir / "afr.pub.asc"
        try:
            export_public_key(args.key_id, key_out)
            print(
                f"Exported key   : {colors.wrap(str(key_out), colors.green)}")
        except Exception as exc:  # pylint: disable=broad-except
            extra_step_failed = True
            print(
                f"Exported key   : {colors.wrap('FAILED', colors.red)} ({exc})")

    if args.generate_index:
        template_path = root_dir / args.template
        index_out = repo_root_dir / "index.html"
        try:
            package_names = package_names_from_repo(repo_x86_dir)
            entries = font_index_entries(
                package_names, packages_dir, repo_x86_dir)
            render_index_from_template(
                template_path,
                index_out,
                package_names,
                entries,
            )
            print(
                f"Index page     : {colors.wrap(str(index_out), colors.green)}")
        except Exception as exc:  # pylint: disable=broad-except
            extra_step_failed = True
            print(
                f"Index page     : {colors.wrap('FAILED', colors.red)} ({exc})")

    if failed_count > 0 or repo_db_ok != 1 or extra_step_failed:
        return 1

    print("Repository build complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
