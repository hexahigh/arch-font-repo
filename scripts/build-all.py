#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root_dir = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, str(
        root_dir / "scripts" / "build-repo.py"), *sys.argv[1:]]
    result = subprocess.run(cmd, check=False, cwd=str(root_dir))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
