from __future__ import annotations

import io
import json
import os
import platform
import queue
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config

KATAGO_VERSION_WINDOWS = "v1.18.1"
KATAGO_VERSION_LINUX = "v1.15.3"
MODEL_VERSION = "v1.12.4"
MODEL_NAME = "b18c384nbt-uec.bin.gz"
SUPPORTED_BACKENDS = {"auto", "eigen", "eigenavx2", "opencl"}
AUTO_BACKENDS = ("opencl", "eigenavx2", "eigen")
THREAD_LAYOUTS = ((4, 4), (8, 2), (16, 1))
BENCHMARK_TURNS = (0, 1)
BENCHMARK_VISITS = 3
BENCHMARK_TIMEOUT_SECONDS = 12
UBUNTU_FOCAL_LIBZIP_URL = (
    "https://archive.ubuntu.com/ubuntu/pool/universe/libz/libzip/libzip5_1.5.1-0ubuntu1_amd64.deb"
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
        missing = [
            str(path) for path in (paths.executable, paths.model, paths.config) if not path.exists()
        ]
        if missing:
            raise KataGoSetupError("Configured KataGo file(s) do not exist: " + ", ".join(missing))
        if install:
            ManagedKataGo._validate_runtime(paths)
        return paths

    managed = ManagedKataGo(config.managed_root, config.managed_backend)
    return managed.ensure() if install else managed.require_installed()


class ManagedKataGo:
    def __init__(self, root: Path, backend: str = "auto") -> None:
        self.root = Path(root).expanduser()
        self.backend = backend.lower()

    def require_installed(self) -> KataGoPaths:
        backend = self._selected_backend() if self.backend == "auto" else self.backend
        paths = self._paths(backend)
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

    def _selection_path(self) -> Path:
        return self.root / "managed-selection.json"

    def _selected_backend(self) -> str:
        selection = self._selection_path()
        if not selection.exists():
            raise KataGoSetupError("KataGo setup has not been completed. Run 'kiai setup' first.")
        try:
            backend = str(json.loads(selection.read_text(encoding="utf-8"))["backend"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise KataGoSetupError(
                "Managed KataGo selection is invalid. Run 'kiai setup' again."
            ) from exc
        if backend not in AUTO_BACKENDS:
            raise KataGoSetupError("Managed KataGo selection is invalid. Run 'kiai setup' again.")
        return backend

    def _paths(self, backend: str | None = None) -> KataGoPaths:
        backend = backend or self.backend
        version = self._katago_version()
        install_dir = self.root / version / backend
        return KataGoPaths(
            executable=install_dir / ("katago.exe" if sys.platform == "win32" else "katago"),
            model=self.root / "models" / MODEL_NAME,
            config=install_dir / "kiai-analysis.cfg",
            managed=True,
        )

    def ensure(self) -> KataGoPaths:
        if self.backend not in SUPPORTED_BACKENDS:
            choices = ", ".join(sorted(SUPPORTED_BACKENDS))
            raise KataGoSetupError(
                f"Unsupported managed backend {self.backend!r}; choose one of: {choices}"
            )
        if self.backend == "auto":
            return self._auto_setup()
        paths = self._ensure_backend(self.backend)
        layout, seconds = self._choose_thread_layout(paths)
        self._write_optimized_config(paths, *layout)
        self._validate_runtime(paths)
        print(
            f"Selected {self.backend} with {layout[0]} analysis x {layout[1]} search threads ({seconds:.2f}s benchmark)."
        )
        return paths

    def _auto_setup(self) -> KataGoPaths:
        working: list[tuple[float, str, KataGoPaths]] = []
        errors: list[str] = []
        print("Benchmarking managed KataGo backends...")
        for backend in AUTO_BACKENDS:
            try:
                paths = self._ensure_backend(backend)
                # Use a balanced batch-oriented layout for backend comparison. The winner is
                # then tuned across several layouts below.
                self._write_optimized_config(paths, 8, 2)
                if backend == "opencl":
                    print(
                        "  opencl    initializing (first run may spend several minutes tuning kernels)..."
                    )
                self._validate_runtime(paths)
                seconds = self._benchmark(paths)
                print(f"  {backend:<9} {seconds:6.2f}s")
                working.append((seconds, backend, paths))
            except KataGoSetupError as exc:
                print(f"  {backend:<9} unavailable: {str(exc).splitlines()[-1]}")
                errors.append(f"{backend}: {exc}")
        if not working:
            raise KataGoSetupError("No managed KataGo backend could run.\n" + "\n".join(errors))
        _, backend, paths = min(working, key=lambda item: item[0])
        print(f"Fastest backend: {backend}")
        layout, seconds = self._choose_thread_layout(paths)
        self._write_optimized_config(paths, *layout)
        self._validate_runtime(paths)
        self._selection_path().parent.mkdir(parents=True, exist_ok=True)
        self._selection_path().write_text(
            json.dumps(
                {
                    "backend": backend,
                    "analysis_threads": layout[0],
                    "search_threads_per_analysis": layout[1],
                    "benchmark_seconds": round(seconds, 4),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"Selected {backend} with {layout[0]} analysis x {layout[1]} search threads "
            f"({seconds:.2f}s benchmark)."
        )
        return paths

    def _ensure_backend(self, backend: str) -> KataGoPaths:
        version = self._katago_version()
        old_backend = self.backend
        self.backend = backend
        try:
            asset = self._asset_name(version)
        finally:
            self.backend = old_backend
        paths = self._paths(backend)
        install_dir = paths.executable.parent
        source_config = install_dir / "analysis_example.cfg"
        if not paths.executable.exists() or not source_config.exists():
            print(f"Downloading managed KataGo {version} ({backend})...")
            self._install_katago(version, asset, install_dir)
        if not paths.model.exists():
            print(f"Downloading managed KataGo model {MODEL_NAME}...")
            self._download(
                f"https://github.com/lightvector/KataGo/releases/download/{MODEL_VERSION}/{MODEL_NAME}",
                paths.model,
            )
        if sys.platform.startswith("linux"):
            self._ensure_linux_runtime_bundle(install_dir)
        elif sys.platform != "win32":
            paths.executable.chmod(
                paths.executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
        if not source_config.exists() or not paths.model.exists() or not paths.executable.exists():
            raise KataGoSetupError("Managed KataGo installation did not produce all required files")
        if not paths.config.exists():
            self._write_optimized_config(paths, 8, 2)
        return paths

    def _write_optimized_config(
        self, paths: KataGoPaths, analysis_threads: int, search_threads: int
    ) -> None:
        source = paths.executable.parent / "analysis_example.cfg"
        text = source.read_text(encoding="utf-8")
        replacements = {
            "numAnalysisThreads = 2": f"numAnalysisThreads = {analysis_threads}",
            "numSearchThreadsPerAnalysisThread = 16": (
                f"numSearchThreadsPerAnalysisThread = {search_threads}"
            ),
            "maxVisits = 500": "maxVisits = 500",
        }
        for old, new_value in replacements.items():
            if old not in text:
                raise KataGoSetupError(f"Expected KataGo config setting not found: {old}")
            text = text.replace(old, new_value, 1)
        paths.config.write_text(text, encoding="utf-8")

    def _choose_thread_layout(self, paths: KataGoPaths) -> tuple[tuple[int, int], float]:
        print("Benchmarking batch-analysis thread layouts...")
        results: list[tuple[float, tuple[int, int]]] = []
        for layout in THREAD_LAYOUTS:
            self._write_optimized_config(paths, *layout)
            seconds = self._benchmark(paths)
            print(f"  {layout[0]:>2} analysis x {layout[1]:>2} search: {seconds:6.2f}s")
            results.append((seconds, layout))
        seconds, layout = min(results, key=lambda item: item[0])
        return layout, seconds

    @staticmethod
    def _benchmark(paths: KataGoPaths) -> float:
        """Run a deliberately tiny throughput sample suitable for interactive setup."""
        moves = [["B", "D4"], ["W", "Q16"]]
        query = (
            json.dumps(
                {
                    "id": "benchmark",
                    "moves": moves,
                    "rules": "tromp-taylor",
                    "komi": 7.5,
                    "boardXSize": 19,
                    "boardYSize": 19,
                    "analyzeTurns": list(BENCHMARK_TURNS),
                    "includeOwnership": False,
                    "maxVisits": BENCHMARK_VISITS,
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        started = time.perf_counter()
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
                timeout=BENCHMARK_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise KataGoSetupError(
                f"KataGo quick benchmark exceeded {BENCHMARK_TIMEOUT_SECONDS}s"
            ) from exc
        except OSError as exc:
            raise KataGoSetupError(f"KataGo benchmark failed: {exc}") from exc
        elapsed = time.perf_counter() - started
        if result.returncode != 0:
            detail = (
                result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            )
            raise KataGoSetupError(f"KataGo benchmark failed: {detail}")
        responses = [line for line in result.stdout.splitlines() if line.strip()]
        if len(responses) < len(BENCHMARK_TURNS):
            raise KataGoSetupError(
                f"KataGo benchmark returned only {len(responses)} of "
                f"{len(BENCHMARK_TURNS)} position results"
            )
        return elapsed

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
                (
                    m
                    for m in archive.getmembers()
                    if m.isfile() and m.name.endswith("/libzip.so.5.0")
                ),
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
        command = [
            str(paths.executable),
            "analysis",
            "-model",
            str(paths.model),
            "-config",
            str(paths.config),
        ]
        is_opencl = paths.executable.parent.name == "opencl"
        try:
            if is_opencl:
                result = ManagedKataGo._run_opencl_validation_with_progress(command, query)
            else:
                result = subprocess.run(
                    command,
                    input=query,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise KataGoSetupError(f"KataGo runtime validation failed: {exc}") from exc
        except OSError as exc:
            raise KataGoSetupError(f"KataGo runtime validation failed: {exc}") from exc
        if result.returncode != 0:
            detail = (
                result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            )
            raise KataGoSetupError(
                "KataGo setup validation failed while loading the analysis engine, model, or config. "
                f"Runtime error: {detail}"
            )

    @staticmethod
    def _run_opencl_validation_with_progress(
        command: list[str], query: str
    ) -> subprocess.CompletedProcess[str]:
        """Stream KataGo's actual OpenCL tuner stages and config counts during first-run tuning."""
        timeout_seconds = 600
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        proc.stdin.write(query)
        proc.stdin.close()

        stderr_lines: list[str] = []
        stdout_lines: list[str] = []
        events: queue.Queue[str | None] = queue.Queue()

        def drain_stderr() -> None:
            for raw in proc.stderr:
                line = raw.rstrip()
                stderr_lines.append(line)
                events.put(line)
            events.put(None)

        def drain_stdout() -> None:
            stdout_lines.extend(line.rstrip() for line in proc.stdout)

        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        stdout_thread = threading.Thread(target=drain_stdout, daemon=True)
        stderr_thread.start()
        stdout_thread.start()

        deadline = time.monotonic() + timeout_seconds
        stderr_done = False
        try:
            while proc.poll() is None or not stderr_done:
                if time.monotonic() >= deadline:
                    proc.kill()
                    proc.wait()
                    detail = "\n".join(stderr_lines[-20:])
                    raise KataGoSetupError(
                        "OpenCL initialization did not finish within 10 minutes. "
                        f"Last tuner output:\n{detail}"
                    )
                try:
                    line = events.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    stderr_done = True
                    continue
                if line:
                    # KataGo itself reports the device, tuning stage, number of candidate
                    # configs, and progress such as `Tuning 20/183 ...`. KaTrain surfaces
                    # this same engine output; do the same here rather than synthesizing it.
                    print(f"    {line}", flush=True)

            stderr_thread.join(timeout=1)
            stdout_thread.join(timeout=1)
            return subprocess.CompletedProcess(
                command,
                proc.returncode,
                "\n".join(stdout_lines),
                "\n".join(stderr_lines),
            )
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

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
            with (
                urllib.request.urlopen(request, timeout=120) as response,
                partial.open("wb") as out,
            ):
                shutil.copyfileobj(response, out)
            os.replace(partial, destination)
        except Exception as exc:
            partial.unlink(missing_ok=True)
            raise KataGoSetupError(f"Failed to download {url}: {exc}") from exc
