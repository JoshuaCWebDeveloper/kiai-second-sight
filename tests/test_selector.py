from pathlib import Path

import pytest

from kiai_second_sight.models import Game, Move, PositionAnalysis
from kiai_second_sight.selector import (
    played_move_analysis,
    screening_candidates,
    select_cards,
    winrate_for_player,
)


def analysis(turn: int, black_wr: float) -> PositionAnalysis:
    return PositionAnalysis(turn_number=turn, root_winrate=black_wr, root_visits=100)


def test_winrate_normalization():
    assert winrate_for_player(0.7, "B") == 0.7
    assert winrate_for_player(0.7, "W") == pytest.approx(0.3)


def test_selects_only_loss_from_above_half(tmp_path: Path):
    sgf = tmp_path / "x.sgf"
    sgf.write_text("x")
    game = Game(
        path=sgf,
        board_size=9,
        komi=7.5,
        rules="Chinese",
        black_name="me",
        white_name="them",
        initial_stones=[],
        moves=[Move(1, "B", (3, 3)), Move(2, "W", (4, 4)), Move(3, "B", (2, 2))],
    )
    analyses = {
        0: analysis(0, 0.68),
        1: analysis(1, 0.54),  # black loses 14pp => qualifies
        2: analysis(2, 0.48),
        3: analysis(3, 0.30),  # black loses 18pp but starts below 50 => no card
    }
    cards = select_cards(game, "B", analyses)
    assert [card.move_number for card in cards] == [1]
    assert round(cards[0].loss_pp, 3) == 0.14


def test_screening_candidates_use_generous_margins(tmp_path: Path):
    sgf = tmp_path / "screen.sgf"
    sgf.write_text("x")
    game = Game(
        path=sgf,
        board_size=9,
        komi=7.5,
        rules="Chinese",
        black_name="me",
        white_name="them",
        initial_stones=[],
        moves=[Move(1, "B", (3, 3)), Move(2, "W", (4, 4)), Move(3, "B", (2, 2))],
    )
    analyses = {
        0: analysis(0, 0.48),
        1: analysis(1, 0.42),  # 6pp loss; retained by 5pp screening margins
        2: analysis(2, 0.44),
        3: analysis(3, 0.20),  # starts below the 45% screening floor
    }
    assert screening_candidates(game, "B", analyses) == [1]


def test_played_move_analysis_uses_move_info():
    before = PositionAnalysis(
        turn_number=12,
        root_winrate=0.70,
        root_visits=75,
        move_infos=[
            {"move": "Q16", "winrate": 0.64, "visits": 30},
            {"move": "D4", "winrate": 0.55, "visits": 5},
        ],
    )
    after = played_move_analysis(before, "D4", 13)
    assert after is not None
    assert after.turn_number == 13
    assert after.root_winrate == 0.55
    assert after.root_visits == 5
    assert played_move_analysis(before, "C3", 13) is None
