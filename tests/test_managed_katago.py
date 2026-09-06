from pathlib import Path

import pytest

from kiai_second_sight.config import Config
from kiai_second_sight.managed_katago import KataGoSetupError, resolve_katago


def test_external_katago_requires_all_three_paths(tmp_path: Path):
    cfg = Config(katago_path=str(tmp_path / "katago"))
    with pytest.raises(KataGoSetupError, match="partial"):
        resolve_katago(cfg)


def test_external_katago_wins_when_fully_configured(tmp_path: Path):
    exe = tmp_path / "katago"
    model = tmp_path / "model.bin.gz"
    analysis = tmp_path / "analysis.cfg"
    for path in (exe, model, analysis):
        path.write_text("x")
    cfg = Config(
        katago_path=str(exe),
        model_path=str(model),
        analysis_config_path=str(analysis),
    )
    paths = resolve_katago(cfg)
    assert paths.executable == exe
    assert paths.model == model
    assert paths.config == analysis
    assert paths.managed is False


def test_managed_linux_uses_ubuntu_20_compatible_release(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("kiai_second_sight.managed_katago.sys.platform", "linux")
    monkeypatch.setattr("kiai_second_sight.managed_katago.platform.machine", lambda: "x86_64")
    managed = __import__(
        "kiai_second_sight.managed_katago", fromlist=["ManagedKataGo"]
    ).ManagedKataGo(tmp_path, "eigen")
    version = managed._katago_version()
    assert version == "v1.15.3"
    assert managed._asset_name(version) == "katago-v1.15.3-eigen-linux-x64.zip"


def test_managed_runtime_must_be_set_up_before_import(tmp_path: Path):
    cfg = Config(managed_root=tmp_path)
    with pytest.raises(KataGoSetupError, match="kiai setup"):
        resolve_katago(cfg)


def test_setup_surfaces_runtime_loader_errors(monkeypatch, tmp_path: Path):
    import subprocess

    from kiai_second_sight.managed_katago import ManagedKataGo

    executable = tmp_path / "katago"
    executable.write_text("x")

    class Result:
        returncode = 127
        stdout = ""
        stderr = "error while loading shared libraries: libzip.so.5: cannot open shared object file"

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(KataGoSetupError, match="libzip.so.5"):
        ManagedKataGo._validate_executable(executable)
