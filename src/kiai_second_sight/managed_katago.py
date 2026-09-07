from __future__ import annotations

import io
import json
import os
import platform
import queue
import re
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
KATAGO_SOURCE_VERSION = "v1.18.1"
MODEL_VERSION = "v1.12.4"
MODEL_NAME = "b18c384nbt-uec.bin.gz"
MODERN_MODEL_VERSION = "v1.17.1"
MODERN_MODEL_NAME = "b10c384h6nbttflrs.bin.gz"
CMAKE_VERSION = "3.31.12"
OPENCL_HEADERS_VERSION = "v2024.10.24"
SUPPORTED_BACKENDS = {"auto", "source-opencl", "eigen", "eigenavx2", "opencl"}
AUTO_BACKENDS = ("source-opencl", "opencl", "eigenavx2", "eigen")
AUTO_BENCHMARK_BACKENDS = ("source-opencl", "opencl", "eigenavx2")
THREAD_LAYOUTS = ((4, 4), (8, 2), (16, 1))
BENCHMARK_TURNS = (0, 1)
BENCHMARK_VISITS = 3
BENCHMARK_TIMEOUT_SECONDS = 30
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
        if sys.platform.startswith("linux") and backend != "source-opencl":
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
        source_build = backend == "source-opencl"
        version = KATAGO_SOURCE_VERSION if source_build else self._katago_version()
        model_name = MODERN_MODEL_NAME if source_build else MODEL_NAME
        install_dir = self.root / version / backend
        return KataGoPaths(
            executable=install_dir / ("katago.exe" if sys.platform == "win32" else "katago"),
            model=self.root / "models" / model_name,
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

        # Prefer a current KataGo source build when the local Linux toolchain can build it,
        # then compare against the compatible prebuilt OpenCL and AVX2 runtimes. Plain
        # Eigen remains a compatibility fallback rather than a useful benchmark target.
        for backend in AUTO_BENCHMARK_BACKENDS:
            try:
                paths = self._ensure_backend(backend)
                self._write_optimized_config(paths, 8, 2)
                if self._is_opencl_backend(backend):
                    print(
                        f"  {backend:<12} initializing "
                        "(first run may spend several minutes tuning kernels)..."
                    )
                    self._validate_runtime(paths)
                seconds = self._benchmark(paths)
                print(f"  {backend:<9} {seconds:6.2f}s")
                working.append((seconds, backend, paths))
            except KataGoSetupError as exc:
                print(f"  {backend:<9} unavailable: {str(exc).splitlines()[-1]}")
                errors.append(f"{backend}: {exc}")

        if working:
            baseline_seconds, backend, paths = min(working, key=lambda item: item[0])
            print(f"Fastest backend: {backend}")
            layout, seconds = self._choose_thread_layout(paths, baseline=((8, 2), baseline_seconds))
        else:
            try:
                backend = "eigen"
                paths = self._ensure_backend(backend)
                self._write_optimized_config(paths, 8, 2)
                baseline_seconds = self._benchmark(paths)
                print(f"  {backend:<9} {baseline_seconds:6.2f}s (fallback)")
                layout, seconds = self._choose_thread_layout(
                    paths, baseline=((8, 2), baseline_seconds)
                )
            except KataGoSetupError as exc:
                errors.append(f"eigen: {exc}")
                raise KataGoSetupError(
                    "No managed KataGo backend could run.\n" + "\n".join(errors)
                ) from exc

        self._write_optimized_config(paths, *layout)
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
        if backend == "source-opencl":
            return self._ensure_source_opencl()

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

    def _ensure_source_opencl(self) -> KataGoPaths:
        """Build current KataGo OpenCL from source against the local Linux runtime."""
        if not sys.platform.startswith("linux"):
            raise KataGoSetupError("source-opencl is currently supported only on Linux")
        if platform.machine().lower() not in {"x86_64", "amd64"}:
            raise KataGoSetupError("source-opencl currently supports x86-64 Linux only")

        paths = self._paths("source-opencl")
        install_dir = paths.executable.parent
        source_root = self.root / "sources" / f"KataGo-{KATAGO_SOURCE_VERSION.lstrip('v')}"
        cpp_root = source_root / "cpp"
        source_config = cpp_root / "configs" / "analysis_example.cfg"

        if not paths.executable.exists() or not source_config.exists():
            print(f"Building managed KataGo {KATAGO_SOURCE_VERSION} (OpenCL) from source...")
            self._ensure_katago_source(source_root)
            cmake = self._ensure_cmake()
            opencl_headers = self._ensure_opencl_headers()
            opencl_library = self._find_opencl_library()
            if opencl_library is None:
                raise KataGoSetupError(
                    "OpenCL runtime library was not found. Install an OpenCL ICD/runtime first."
                )
            if not Path("/usr/include/zlib.h").exists():
                raise KataGoSetupError(
                    "KataGo source build requires zlib development headers (Ubuntu: zlib1g-dev)."
                )

            build_dir = cpp_root / "build-kiai-opencl"
            build_dir.mkdir(parents=True, exist_ok=True)
            configure = [
                str(cmake),
                "-S",
                str(cpp_root),
                "-B",
                str(build_dir),
                "-DUSE_BACKEND=OPENCL",
                "-DNO_GIT_REVISION=1",
                "-DCMAKE_BUILD_TYPE=Release",
                f"-DOpenCL_INCLUDE_DIR={opencl_headers}",
                f"-DOpenCL_LIBRARY={opencl_library}",
            ]
            self._run_streaming(configure, "KataGo source configuration")
            jobs = max(1, min(os.cpu_count() or 1, 8))
            self._run_streaming(
                [str(cmake), "--build", str(build_dir), "--parallel", str(jobs)],
                "KataGo source build",
            )
            built = build_dir / "katago"
            if not built.exists():
                raise KataGoSetupError("KataGo source build completed without producing katago")
            install_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(built, paths.executable)
            paths.executable.chmod(
                paths.executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
            shutil.copy2(source_config, install_dir / "analysis_example.cfg")

        if not paths.model.exists():
            print(f"Downloading managed KataGo model {MODERN_MODEL_NAME}...")
            self._download(
                f"https://github.com/lightvector/KataGo/releases/download/"
                f"{MODERN_MODEL_VERSION}/{MODERN_MODEL_NAME}",
                paths.model,
            )
        if not paths.config.exists():
            self._write_optimized_config(paths, 8, 2)
        return paths

    def _ensure_katago_source(self, source_root: Path) -> None:
        if (source_root / "cpp" / "CMakeLists.txt").exists():
            return
        archive = self.root / "sources" / f"KataGo-{KATAGO_SOURCE_VERSION}.tar.gz"
        if not archive.exists():
            print(f"Downloading KataGo {KATAGO_SOURCE_VERSION} source...")
            self._download(
                f"https://github.com/lightvector/KataGo/archive/refs/tags/"
                f"{KATAGO_SOURCE_VERSION}.tar.gz",
                archive,
            )
        source_root.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(source_root.parent)
        if not (source_root / "cpp" / "CMakeLists.txt").exists():
            raise KataGoSetupError("Downloaded KataGo source archive did not extract as expected")

    def _ensure_cmake(self) -> Path:
        tool_root = self.root / "tools" / f"cmake-{CMAKE_VERSION}-linux-x86_64"
        executable = tool_root / "bin" / "cmake"
        if executable.exists():
            return executable
        archive = self.root / "tools" / f"cmake-{CMAKE_VERSION}-linux-x86_64.tar.gz"
        print(f"Downloading managed CMake {CMAKE_VERSION} for KataGo source builds...")
        self._download(
            f"https://github.com/Kitware/CMake/releases/download/v{CMAKE_VERSION}/"
            f"cmake-{CMAKE_VERSION}-linux-x86_64.tar.gz",
            archive,
        )
        self.root.joinpath("tools").mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(self.root / "tools")
        if not executable.exists():
            raise KataGoSetupError("Managed CMake archive did not extract as expected")
        return executable

    def _ensure_opencl_headers(self) -> Path:
        system_headers = Path("/usr/include/CL/cl.h")
        if system_headers.exists():
            return system_headers.parent.parent
        version = OPENCL_HEADERS_VERSION
        root = self.root / "tools" / f"OpenCL-Headers-{version.lstrip('v')}"
        if not (root / "CL" / "cl.h").exists():
            archive = self.root / "tools" / f"OpenCL-Headers-{version}.tar.gz"
            print("Downloading managed OpenCL headers for KataGo source build...")
            self._download(
                f"https://github.com/KhronosGroup/OpenCL-Headers/archive/refs/tags/{version}.tar.gz",
                archive,
            )
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(self.root / "tools")
        if not (root / "CL" / "cl.h").exists():
            raise KataGoSetupError("Managed OpenCL headers did not extract as expected")
        return root

    @staticmethod
    def _find_opencl_library() -> Path | None:
        candidates = [
            Path("/usr/lib/x86_64-linux-gnu/libOpenCL.so"),
            Path("/usr/lib/x86_64-linux-gnu/libOpenCL.so.1"),
            Path("/usr/lib64/libOpenCL.so"),
            Path("/usr/lib64/libOpenCL.so.1"),
            Path("/usr/lib/libOpenCL.so"),
            Path("/usr/lib/libOpenCL.so.1"),
        ]
        return next((path for path in candidates if path.exists()), None)

    @staticmethod
    def _run_streaming(command: list[str], label: str) -> None:
        print(f"{label}...")
        try:
            result = subprocess.run(command, check=False)
        except OSError as exc:
            raise KataGoSetupError(f"{label} failed to start: {exc}") from exc
        if result.returncode != 0:
            raise KataGoSetupError(f"{label} failed with exit code {result.returncode}")

    @staticmethod
    def _is_opencl_backend(backend: str) -> bool:
        return backend in {"opencl", "source-opencl"}

    def _write_optimized_config(
        self, paths: KataGoPaths, analysis_threads: int, search_threads: int
    ) -> None:
        source = paths.executable.parent / "analysis_example.cfg"
        text = source.read_text(encoding="utf-8")
        settings = {
            "numAnalysisThreads": analysis_threads,
            "numSearchThreadsPerAnalysisThread": search_threads,
            "maxVisits": 500,
        }
        for name, value in settings.items():
            pattern = rf"(?m)^(\s*{re.escape(name)}\s*=\s*)[^#\n]+"
            text, count = re.subn(pattern, rf"\g<1>{value}", text, count=1)
            if count == 0 and name == "numSearchThreadsPerAnalysisThread":
                alias = r"(?m)^(\s*numSearchThreads\s*=\s*)[^#\n]+"
                text, count = re.subn(alias, rf"\g<1>{value}", text, count=1)
            if count == 0:
                raise KataGoSetupError(f"Expected KataGo config setting not found: {name}")
        paths.config.write_text(text, encoding="utf-8")

    def _choose_thread_layout(
        self,
        paths: KataGoPaths,
        *,
        baseline: tuple[tuple[int, int], float] | None = None,
    ) -> tuple[tuple[int, int], float]:
        print("Benchmarking batch-analysis thread layouts...")
        results: list[tuple[float, tuple[int, int]]] = []
        if baseline is not None:
            layout, seconds = baseline
            results.append((seconds, layout))
            print(f"  {layout[0]:>2} analysis x {layout[1]:>2} search: {seconds:6.2f}s (reused)")
        for layout in THREAD_LAYOUTS:
            if baseline is not None and layout == baseline[0]:
                continue
            self._write_optimized_config(paths, *layout)
            seconds = self._benchmark(paths)
            print(f"  {layout[0]:>2} analysis x {layout[1]:>2} search: {seconds:6.2f}s")
            results.append((seconds, layout))
        seconds, layout = min(results, key=lambda item: item[0])
        return layout, seconds

    @staticmethod
    def _benchmark(paths: KataGoPaths) -> float:
        """Measure analysis throughput without counting engine/model startup time."""
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
        command = [
            str(paths.executable),
            "analysis",
            "-model",
            str(paths.model),
            "-config",
            str(paths.config),
        ]
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None

        stderr_lines: list[str] = []
        ready = threading.Event()
        stdout_events: queue.Queue[str | None] = queue.Queue()

        def drain_stderr() -> None:
            for raw in proc.stderr:
                line = raw.rstrip()
                stderr_lines.append(line)
                if "Started, ready to begin handling requests" in line:
                    ready.set()

        def drain_stdout() -> None:
            for raw in proc.stdout:
                stdout_events.put(raw.rstrip())
            stdout_events.put(None)

        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        stdout_thread = threading.Thread(target=drain_stdout, daemon=True)
        stderr_thread.start()
        stdout_thread.start()
        try:
            if not ready.wait(BENCHMARK_TIMEOUT_SECONDS):
                proc.kill()
                proc.wait()
                detail = stderr_lines[-1] if stderr_lines else "engine did not become ready"
                raise KataGoSetupError(
                    f"KataGo benchmark startup exceeded {BENCHMARK_TIMEOUT_SECONDS}s: {detail}"
                )

            # Start the clock only once KataGo has loaded the model/backend and declared itself
            # ready. This keeps OpenCL's larger initialization cost from being mistaken for slow
            # analysis throughput.
            started = time.perf_counter()
            proc.stdin.write(query)
            proc.stdin.flush()

            responses: list[str] = []
            deadline = time.monotonic() + BENCHMARK_TIMEOUT_SECONDS
            while len(responses) < len(BENCHMARK_TURNS):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise KataGoSetupError(
                        f"KataGo quick benchmark analysis exceeded {BENCHMARK_TIMEOUT_SECONDS}s"
                    )
                try:
                    line = stdout_events.get(timeout=remaining)
                except queue.Empty as exc:
                    raise KataGoSetupError(
                        f"KataGo quick benchmark analysis exceeded {BENCHMARK_TIMEOUT_SECONDS}s"
                    ) from exc
                if line is None:
                    detail = stderr_lines[-1] if stderr_lines else f"exit code {proc.returncode}"
                    raise KataGoSetupError(f"KataGo benchmark failed: {detail}")
                responses.append(line)

            return time.perf_counter() - started
        except OSError as exc:
            raise KataGoSetupError(f"KataGo benchmark failed: {exc}") from exc
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            stderr_thread.join(timeout=1)
            stdout_thread.join(timeout=1)

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
        is_opencl = ManagedKataGo._is_opencl_backend(paths.executable.parent.name)
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
