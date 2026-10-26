"""Build small native bridges atomically, cached by source and architecture."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path


def bridge_library_path(source: Path, cache: Path) -> Path:
    """Choose an architecture/source-specific path without creating a binary.

    Example: bridge_library_path(Path("bridge.c"), user_cache)
    """
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    return cache / f"{source.stem}-{platform.machine()}-{digest}{suffix}"


def _bridge_command(source: Path, output: Path) -> list[str]:
    if sys.platform != "darwin":
        return ["cc", "-shared", "-fPIC", "-O2", str(source), "-o", str(output)]
    command = ["clang", "-dynamiclib", "-O2"]
    if source.suffix == ".m":
        command += ["-fobjc-arc", "-framework", "Foundation"]
    return command + [str(source), "-o", str(output)]


def build_native_bridge(source: Path, cache: Path) -> Path:
    """Cache a complete bridge; failed builds never replace a usable binary.

    Example: build_native_bridge(Path("bridge.c"), user_cache)
    """
    library = bridge_library_path(source, cache)
    if library.is_file():
        return library
    cache.mkdir(parents=True, exist_ok=True)
    temporary = library.with_name(f"{library.stem}.{os.getpid()}{library.suffix}")
    try:
        subprocess.run(
            _bridge_command(source, temporary), check=True, capture_output=True, timeout=20
        )
        temporary.replace(library)
    finally:
        temporary.unlink(missing_ok=True)
    return library
