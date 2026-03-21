# arch-font-repo (AFR)

Arch Linux binary repository for TTF font packages, backed by PKGBUILDs in this repo.

## Project Layout

```text
packages/                # All package definitions (one folder per package)
  ttf-ibm-plex/
scripts/                 # Build and repo-index automation
repo/
   x86_64/                # Built artifacts (pkg.tar.zst + db/files)
```

## Quick Start

1. Build a single package:

   ```bash
   python scripts/build-package.py ttf-ibm-plex
   ```

2. Build all packages and refresh repo metadata:

   ```bash
   python scripts/build-repo.py
   ```

   To also publish the public key and regenerate the repo landing page:

   ```bash
   python scripts/build-repo.py --sign --key-id B67389CC0D0BDF88 --export-key --generate-index
   ```

   The command writes:
   - `repo/afr.pub.asc`
   - `repo/index.html` (rendered from `index.html.tmpl`)

3. (Optional) Manual metadata refresh only:

   ```bash
   python scripts/update-repo-db.py
   ```

Build artifacts are centralized so package folders stay clean:

- Binary packages: `repo/x86_64/`
- Downloaded source archives: `build/sources/`
- Source package archives: `build/srcpkgs/`
- Build workspace: `build/work/<package-name>/`

## Add the Repository

The repository is published at `https://afr.080609.xyz/x86_64`.

1. Add this block to `/etc/pacman.conf`:

   ```ini
   [arch-font-repo]
   SigLevel = Optional TrustAll
   Server = https://afr.080609.xyz/x86_64
   ```

2. Refresh and install packages:

   ```bash
   sudo pacman -Syy
   sudo pacman -S ttf-ibm-plex
   ```

## Add the Repository (Signed Setup)

If you want proper signature verification (recommended), import the AFR public key and use strict `SigLevel`.:

1. Add the key to pacman and locally sign it:

   ```bash
   curl -fsSL -o /tmp/afr.pub.asc https://afr.080609.xyz/afr.pub.asc
   sudo pacman-key --add /tmp/afr.pub.asc
   sudo pacman-key --lsign-key EEB57FEBBBFC5AC1CC48C353B67389CC0D0BDF88
   ```


2. Configure the repository in `/etc/pacman.conf` with strict verification:

   ```ini
   [arch-font-repo]
   SigLevel = Required
   Server = https://afr.080609.xyz/x86_64
   ```

3. Refresh and install packages:

   ```bash
   sudo pacman -Syy
   sudo pacman -S ttf-ibm-plex
   ```
