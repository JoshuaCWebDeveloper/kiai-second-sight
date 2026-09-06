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
