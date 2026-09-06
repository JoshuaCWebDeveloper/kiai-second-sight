from pathlib import Path

from PIL import Image

from kiai_second_sight.board import BoardState
from kiai_second_sight.models import PositionAnalysis
from kiai_second_sight.render import BoardRenderer


def test_render_question_and_answer(tmp_path: Path):
    board = BoardState(size=9, stones={(4, 4): "B", (3, 4): "W"}, last_move=(3, 4))
    analysis = PositionAnalysis(
        turn_number=2,
        root_winrate=0.6,
        root_visits=300,
        move_infos=[
            {"move": "C3", "winrate": 0.63, "visits": 200, "order": 0},
            {"move": "D3", "winrate": 0.51, "visits": 80, "order": 1},
        ],
        ownership=[0.0] * 81,
    )
    renderer = BoardRenderer(600)
    q = renderer.render_question(board, tmp_path / "q.png")
    a = renderer.render_answer(board, analysis, "D3", tmp_path / "a.png")
    assert Image.open(q).size == (600, 600)
    assert Image.open(a).size == (600, 600)
