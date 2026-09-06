from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Color = Literal["B", "W"]
Point = tuple[int, int]  # (row from bottom, column from left)
MovePoint = Point | None  # None means pass


@dataclass(frozen=True)
class Move:
    number: int
    color: Color
    point: MovePoint


@dataclass
class Game:
    path: Path
    board_size: int
    komi: float
    rules: str
    black_name: str | None
    white_name: str | None
    initial_stones: list[tuple[Color, Point]]
    moves: list[Move]


@dataclass
class PositionAnalysis:
    turn_number: int
    root_winrate: float
    root_visits: int
    move_infos: list[dict[str, Any]] = field(default_factory=list)
    ownership: list[float] | None = None


@dataclass
class StudyCard:
    card_id: str
    game_id: str
    source_sgf: str
    player_color: Color
    move_number: int
    played_move: str
    winrate_before: float
    winrate_after: float
    loss_pp: float
    analysis_before: PositionAnalysis
    question_image: str | None = None
    answer_image: str | None = None
