from __future__ import annotations

from dataclasses import dataclass

from sgfmill import boards

from .models import Color, Game, MovePoint


@dataclass
class BoardState:
    size: int
    stones: dict[tuple[int, int], Color]
    last_move: tuple[int, int] | None = None


def board_at_turn(game: Game, turn: int) -> BoardState:
    board = boards.Board(game.board_size)
    for color, point in game.initial_stones:
        row, col = point
        board.set(row, col, color.lower())

    last_move: MovePoint = None
    for move in game.moves:
        if move.number > turn:
            break
        if move.point is not None:
            row, col = move.point
            board.play(row, col, move.color.lower())
            last_move = move.point
        else:
            last_move = None

    stones: dict[tuple[int, int], Color] = {}
    for row in range(game.board_size):
        for col in range(game.board_size):
            color = board.get(row, col)
            if color:
                stones[(row, col)] = color.upper()
    return BoardState(size=game.board_size, stones=stones, last_move=last_move)
