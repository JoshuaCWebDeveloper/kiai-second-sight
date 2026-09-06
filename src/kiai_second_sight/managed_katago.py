from __future__ import annotations

import io
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config

KATAGO_VERSION_WINDOWS = "v1.18.1"
KATAGO_VERSION_LINUX = "v1.15.3"
MODEL_VERSION = "v1.12.4"
MODEL_NAME = "b18c384nbt-uec.bin.gz"
SUPPORTED_BACKENDS = {"eigen", "eigenavx2", "opencl"}
UBUNTU_FOCAL_LIBZIP_URL = (
    "https://archive.ubuntu.com/ubuntu/pool/universe/libz/libzip/"
    "libzip5_1.5.1-0ubuntu1_amd64.deb"
)


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
            ManagedKataGo._validate_runtime(paths)
        return paths

    managed = ManagedKataGo(config.managed_root, config.managed_backend)
    return managed.ensure() if install else managed.require_installed()


class ManagedKataGo:
    def __init__(self, root: Path, backend: str = "eigen") -> None:
        self.root = Path(root).expanduser()
        self.backend = backend.lower()

    def require_installed(self) -> KataGoPaths:
        paths = self._paths()
        required = [paths.executable, paths.model, paths.config]
        if sys.platform.startswith("linux"):
            install_dir = paths.executable.parent
            required.extend([install_dir / "katago.real", install_dir / "lib" / "libzip.so.5"])
        missing = [str(path) for path in required if not path.exists()]
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

        if sys.platform.startswith("linux"):
            self._ensure_linux_runtime_bundle(install_dir)
        elif sys.platform != "win32":
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        self._validate_runtime(paths)
        return paths


    def _ensure_linux_runtime_bundle(self, install_dir: Path) -> None:
        """Bundle focal's libzip5 beside KataGo so Ubuntu 20.04 needs no system package."""
        launcher = install_dir / "katago"
        real_executable = install_dir / "katago.real"
        lib_dir = install_dir / "lib"
        libzip = lib_dir / "libzip.so.5"

        # Migrate an already-downloaded v1.15.3 install in place.
        if launcher.exists() and not real_executable.exists():
            launcher.rename(real_executable)

        if not real_executable.exists():
            raise KataGoSetupError("Managed KataGo executable is missing after installation")

        if not libzip.exists():
            print("Downloading Ubuntu 20.04-compatible libzip5 runtime...")
            with tempfile.TemporaryDirectory(prefix="kiai-libzip-") as temp:
                package = Path(temp) / "libzip5.deb"
                self._download(UBUNTU_FOCAL_LIBZIP_URL, package)
                self._extract_libzip_from_deb(package, lib_dir)

        launcher.write_text(
            "#!/bin/sh\n"
            'here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
            'LD_LIBRARY_PATH="$here/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
            "export LD_LIBRARY_PATH\n"
            'exec "$here/katago.real" "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o755)
        real_executable.chmod(
            real_executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    @staticmethod
    def _extract_libzip_from_deb(package: Path, lib_dir: Path) -> None:
        data = package.read_bytes()
        if not data.startswith(b"!<arch>\n"):
            raise KataGoSetupError("Downloaded libzip5 package is not a valid Debian archive")

        offset = 8
        payload: bytes | None = None
        payload_name = ""
        while offset + 60 <= len(data):
            header = data[offset : offset + 60]
            name = header[:16].decode("ascii", errors="replace").strip().rstrip("/")
            try:
                size = int(header[48:58].decode("ascii").strip())
            except ValueError as exc:
                raise KataGoSetupError("Invalid Debian archive member size") from exc
            start = offset + 60
            end = start + size
            if name.startswith("data.tar"):
                payload = data[start:end]
                payload_name = name
                break
            offset = end + (size % 2)

        if payload is None:
            raise KataGoSetupError("libzip5 package did not contain a data archive")
        if payload_name.endswith(".zst"):
            raise KataGoSetupError("Unsupported zstd-compressed libzip5 package")

        lib_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
            member = next(
                (m for m in archive.getmembers() if m.isfile() and m.name.endswith("/libzip.so.5.0")),
                None,
            )
            if member is None:
                raise KataGoSetupError("libzip5 package did not contain libzip.so.5.0")
            source = archive.extractfile(member)
            if source is None:
                raise KataGoSetupError("Could not extract libzip.so.5.0")
            contents = source.read()
            (lib_dir / "libzip.so.5.0").write_bytes(contents)
            (lib_dir / "libzip.so.5").write_bytes(contents)

    @staticmethod
    def _validate_runtime(paths: KataGoPaths) -> None:
        """Launch the analysis engine so setup validates the executable, model, and config together."""
        query = '{"id":"setup","action":"query_version"}\n'
        try:
            result = subprocess.run(
                [
                    str(paths.executable),
                    "analysis",
                    "-model",
                    str(paths.model),
                    "-config",
                    str(paths.config),
                ],
                input=query,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise KataGoSetupError(f"KataGo runtime validation failed: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            raise KataGoSetupError(
                "KataGo setup validation failed while loading the analysis engine, model, or config. "
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
