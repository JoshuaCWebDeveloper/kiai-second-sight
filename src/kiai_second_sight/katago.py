from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Self

from .coords import point_to_gtp
from .models import Game, PositionAnalysis


class KataGoError(RuntimeError):
    pass


class KataGoAnalyzer:
    """Small client for KataGo's newline-delimited JSON analysis engine."""

    def __init__(
        self,
        executable: str | Path,
        model: str | Path,
        config: str | Path,
        *,
        max_visits: int = 3000,
    ) -> None:
        self.executable = str(executable)
        self.model = str(model)
        self.config = str(config)
        self.max_visits = max_visits
        self._proc: subprocess.Popen[str] | None = None
        self._stderr: list[str] = []

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(
            [
                self.executable,
                "analysis",
                "-model",
                self.model,
                "-config",
                self.config,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert self._proc.stderr is not None

        def drain_stderr() -> None:
            assert self._proc is not None and self._proc.stderr is not None
            for line in self._proc.stderr:
                self._stderr.append(line.rstrip())
                if len(self._stderr) > 200:
                    del self._stderr[:100]

        threading.Thread(target=drain_stderr, daemon=True).start()

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.stdin:
            proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)
        self._proc = None

    def analyze_game(
        self,
        game: Game,
        turns: Iterable[int],
        *,
        include_ownership: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict[int, PositionAnalysis]:
        turns = sorted({int(turn) for turn in turns})
        if not turns:
            return {}
        self.start()
        assert self._proc is not None
        assert self._proc.stdin is not None and self._proc.stdout is not None

        query: dict[str, Any] = {
            "id": "game",
            "moves": [
                [move.color, point_to_gtp(move.point, game.board_size)] for move in game.moves
            ],
            "initialStones": [
                [color, point_to_gtp(point, game.board_size)]
                for color, point in game.initial_stones
            ],
            "rules": game.rules,
            "komi": game.komi,
            "boardXSize": game.board_size,
            "boardYSize": game.board_size,
            "analyzeTurns": turns,
            "includeOwnership": include_ownership,
            "maxVisits": self.max_visits,
        }
        self._proc.stdin.write(json.dumps(query, separators=(",", ":")) + "\n")
        self._proc.stdin.flush()

        results: dict[int, PositionAnalysis] = {}
        while len(results) < len(turns):
            line = self._proc.stdout.readline()
            if not line:
                rc = self._proc.poll()
                detail = "\n".join(self._stderr[-20:])
                raise KataGoError(f"KataGo exited unexpectedly ({rc}).\n{detail}")
            payload = json.loads(line)
            if payload.get("error"):
                raise KataGoError(str(payload["error"]))
            if payload.get("isDuringSearch"):
                continue
            turn = int(payload["turnNumber"])
            root = payload.get("rootInfo") or {}
            results[turn] = PositionAnalysis(
                turn_number=turn,
                root_winrate=float(root["winrate"]),
                root_visits=int(root.get("visits", 0)),
                move_infos=list(payload.get("moveInfos") or []),
                ownership=payload.get("ownership"),
            )
            if on_progress is not None:
                on_progress(len(results), len(turns))
        return results
