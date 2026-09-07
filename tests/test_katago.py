from pathlib import Path

from kiai_second_sight.katago import KataGoAnalyzer
from kiai_second_sight.models import Game


def test_analyze_game_reports_completed_positions(monkeypatch, tmp_path: Path):
    game = Game(
        path=tmp_path / "x.sgf",
        board_size=9,
        komi=7.5,
        rules="Chinese",
        black_name="b",
        white_name="w",
        initial_stones=[],
        moves=[],
    )
    analyzer = KataGoAnalyzer("katago", "model", "config", max_visits=1)

    class Input:
        def write(self, _value):
            return None
        def flush(self):
            return None

    class Output:
        def __init__(self):
            self.lines = iter([
                '{"turnNumber":0,"rootInfo":{"winrate":0.5,"visits":1},"moveInfos":[]}\n',
                '{"turnNumber":1,"rootInfo":{"winrate":0.6,"visits":1},"moveInfos":[]}\n',
            ])
        def readline(self):
            return next(self.lines, "")

    class Proc:
        stdin = Input()
        stdout = Output()
        def poll(self):
            return None

    analyzer._proc = Proc()  # type: ignore[assignment]
    monkeypatch.setattr(analyzer, "start", lambda: None)
    progress = []

    result = analyzer.analyze_game(game, [0, 1], on_progress=lambda done, total: progress.append((done, total)))

    assert sorted(result) == [0, 1]
    assert progress == [(1, 2), (2, 2)]
