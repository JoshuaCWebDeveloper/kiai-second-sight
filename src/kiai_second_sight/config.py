from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    player_names: list[str] = field(default_factory=list)
    katago_path: str | None = None
    model_path: str | None = None
    analysis_config_path: str | None = None
    managed_backend: str = "eigen"
    managed_root: Path = field(
        default_factory=lambda: Path.home() / ".kiai-second-sight" / "katago"
    )
    max_visits: int = 3000
    min_start_winrate: float = 0.50
    min_loss_pp: float = 0.10
    top_moves: int = 5
    image_size: int = 1200
    output_root: Path = Path(".")


def load_config(path: str | Path | None) -> Config:
    cfg = Config()
    path = Path(path or "kiai.toml")
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    player = data.get("player", {})
    analysis = data.get("analysis", {})
    selection = data.get("selection", {})
    render = data.get("render", {})
    output = data.get("output", {})

    cfg.player_names = list(player.get("names", []))
    cfg.katago_path = os.getenv("KIAI_KATAGO_PATH", analysis.get("katago_path"))
    cfg.model_path = os.getenv("KIAI_KATAGO_MODEL", analysis.get("model_path"))
    cfg.analysis_config_path = os.getenv("KIAI_KATAGO_CONFIG", analysis.get("config_path"))
    cfg.managed_backend = str(analysis.get("managed_backend", cfg.managed_backend)).lower()
    cfg.managed_root = Path(
        os.getenv("KIAI_KATAGO_HOME", analysis.get("managed_root", cfg.managed_root))
    ).expanduser()
    cfg.max_visits = int(analysis.get("max_visits", cfg.max_visits))
    cfg.min_start_winrate = float(selection.get("min_start_winrate", cfg.min_start_winrate))
    cfg.min_loss_pp = float(selection.get("min_loss_pp", cfg.min_loss_pp))
    cfg.top_moves = int(render.get("top_moves", cfg.top_moves))
    cfg.image_size = int(render.get("image_size", cfg.image_size))
    cfg.output_root = Path(output.get("root", "."))
    return cfg
