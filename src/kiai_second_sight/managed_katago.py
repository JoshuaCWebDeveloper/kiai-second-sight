from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config

KATAGO_VERSION_WINDOWS = "v1.18.1"
KATAGO_VERSION_LINUX = "v1.15.3"
MODEL_VERSION = "v1.17.1"
MODEL_NAME = "b10c384h6nbttflrs.bin.gz"
SUPPORTED_BACKENDS = {"eigen", "eigenavx2", "opencl"}


class KataGoSetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class KataGoPaths:
    executable: Path
    model: Path
    config: Path
    managed: bool


def resolve_katago(config: Config, *, install: bool = False) -> KataGoPaths:
    """Resolve the configured runtime. Managed downloads happen only during setup."""
    external = (config.katago_path, config.model_path, config.analysis_config_path)
    if any(external):
        if not all(external):
            raise KataGoSetupError(
                "External KataGo configuration is partial. Set analysis.katago_path, "
                "analysis.model_path, and analysis.config_path together, or remove all three "
                "to let Second Sight manage KataGo automatically."
            )
        paths = KataGoPaths(
            executable=Path(config.katago_path or "").expanduser(),
            model=Path(config.model_path or "").expanduser(),
            config=Path(config.analysis_config_path or "").expanduser(),
            managed=False,
        )
        missing = [str(path) for path in (paths.executable, paths.model, paths.config) if not path.exists()]
        if missing:
            raise KataGoSetupError("Configured KataGo file(s) do not exist: " + ", ".join(missing))
        if install:
            ManagedKataGo._validate_executable(paths.executable)
        return paths

    managed = ManagedKataGo(config.managed_root, config.managed_backend)
    return managed.ensure() if install else managed.require_installed()


class ManagedKataGo:
    def __init__(self, root: Path, backend: str = "eigen") -> None:
        self.root = Path(root).expanduser()
        self.backend = backend.lower()

    def require_installed(self) -> KataGoPaths:
        paths = self._paths()
        missing = [str(path) for path in (paths.executable, paths.model, paths.config) if not path.exists()]
        if missing:
            raise KataGoSetupError(
                "KataGo setup has not been completed. Run 'kiai setup' first. Missing: "
                + ", ".join(missing)
            )
        return paths

    def _paths(self) -> KataGoPaths:
        version = self._katago_version()
        install_dir = self.root / version / self.backend
        return KataGoPaths(
            executable=install_dir / ("katago.exe" if sys.platform == "win32" else "katago"),
            model=self.root / "models" / MODEL_NAME,
            config=install_dir / "analysis_example.cfg",
            managed=True,
        )

    def ensure(self) -> KataGoPaths:
        if self.backend not in SUPPORTED_BACKENDS:
            choices = ", ".join(sorted(SUPPORTED_BACKENDS))
            raise KataGoSetupError(f"Unsupported managed backend {self.backend!r}; choose one of: {choices}")

        version = self._katago_version()
        asset = self._asset_name(version)
        paths = self._paths()
        install_dir = paths.executable.parent
        executable, config, model = paths.executable, paths.config, paths.model

        if not executable.exists() or not config.exists():
            print(f"Downloading managed KataGo {version} ({self.backend})...")
            self._install_katago(version, asset, install_dir)
        if not model.exists():
            print(f"Downloading managed KataGo model {MODEL_NAME}...")
            self._download(
                f"https://github.com/lightvector/KataGo/releases/download/{MODEL_VERSION}/{MODEL_NAME}",
                model,
            )

        if not executable.exists() or not config.exists() or not model.exists():
            raise KataGoSetupError("Managed KataGo installation did not produce all required files")

        if sys.platform != "win32":
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        self._validate_executable(executable)
        return paths

    @staticmethod
    def _validate_executable(executable: Path) -> None:
        try:
            result = subprocess.run(
                [str(executable), "version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise KataGoSetupError(f"KataGo runtime validation failed: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            raise KataGoSetupError(
                "KataGo was downloaded but cannot run on this system. "
                f"Runtime error: {detail}"
            )

    def _katago_version(self) -> str:
        if sys.platform.startswith("linux"):
            # v1.15.3 is the newest KataGo release line whose Linux binaries were built
            # on Ubuntu 20.04. Newer Linux releases are built on Ubuntu 22.04 and require
            # newer system libraries such as OpenSSL 3.
            return KATAGO_VERSION_LINUX
        if sys.platform == "win32":
            return KATAGO_VERSION_WINDOWS
        raise KataGoSetupError(
            f"Managed KataGo currently supports Windows and Linux; detected {sys.platform!r}. "
            "Configure an external KataGo installation on this platform."
        )

    def _asset_name(self, version: str) -> str:
        machine = platform.machine().lower()
        if machine not in {"x86_64", "amd64"}:
            raise KataGoSetupError(
                f"Managed KataGo currently supports x86-64 only; detected architecture {machine!r}. "
                "Configure an external KataGo installation for this machine."
            )
        if sys.platform.startswith("linux"):
            os_name = "linux"
        elif sys.platform == "win32":
            os_name = "windows"
        else:
            raise KataGoSetupError(
                f"Managed KataGo currently supports Windows and Linux; detected {sys.platform!r}. "
                "Configure an external KataGo installation on this platform."
            )
        return f"katago-{version}-{self.backend}-{os_name}-x64.zip"

    def _install_katago(self, version: str, asset: str, install_dir: Path) -> None:
        url = f"https://github.com/lightvector/KataGo/releases/download/{version}/{asset}"
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="kiai-katago-") as temp:
            archive = Path(temp) / asset
            self._download(url, archive)
            extracted = Path(temp) / "extracted"
            extracted.mkdir()
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(extracted)
            if install_dir.exists():
                shutil.rmtree(install_dir)
            shutil.copytree(extracted, install_dir)

    @staticmethod
    def _download(url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "kiai-second-sight"})
        partial = destination.with_suffix(destination.suffix + ".part")
        try:
            with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as out:
                shutil.copyfileobj(response, out)
            os.replace(partial, destination)
        except Exception as exc:
            partial.unlink(missing_ok=True)
            raise KataGoSetupError(f"Failed to download {url}: {exc}") from exc
