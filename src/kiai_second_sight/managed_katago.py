from __future__ import annotations

import os
import platform
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config

KATAGO_VERSION = "v1.18.1"
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


def resolve_katago(config: Config) -> KataGoPaths:
    """Use a fully configured external KataGo, otherwise provision our managed copy."""
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
        return paths

    return ManagedKataGo(config.managed_root, config.managed_backend).ensure()


class ManagedKataGo:
    def __init__(self, root: Path, backend: str = "eigen") -> None:
        self.root = Path(root).expanduser()
        self.backend = backend.lower()

    def ensure(self) -> KataGoPaths:
        if self.backend not in SUPPORTED_BACKENDS:
            choices = ", ".join(sorted(SUPPORTED_BACKENDS))
            raise KataGoSetupError(f"Unsupported managed backend {self.backend!r}; choose one of: {choices}")

        asset = self._asset_name()
        install_dir = self.root / KATAGO_VERSION / self.backend
        executable = install_dir / ("katago.exe" if sys.platform == "win32" else "katago")
        config = install_dir / "analysis_example.cfg"
        model = self.root / "models" / MODEL_NAME

        if not executable.exists() or not config.exists():
            print(f"Downloading managed KataGo {KATAGO_VERSION} ({self.backend})...")
            self._install_katago(asset, install_dir)
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

        return KataGoPaths(executable=executable, model=model, config=config, managed=True)

    def _asset_name(self) -> str:
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
        return f"katago-{KATAGO_VERSION}-{self.backend}-{os_name}-x64.zip"

    def _install_katago(self, asset: str, install_dir: Path) -> None:
        url = f"https://github.com/lightvector/KataGo/releases/download/{KATAGO_VERSION}/{asset}"
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
