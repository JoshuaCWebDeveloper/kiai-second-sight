from __future__ import annotations

from pathlib import Path

from sgfmill import sgf

from .models import Color, Game, Move, Point


def _root_text(root: sgf.Tree_node, prop: str) -> str | None:
    try:
        value = root.get(prop)
    except KeyError:
        return None
    if value is None:
        return None
    return str(value)


def _initial_stones(root: sgf.Tree_node) -> list[tuple[Color, Point]]:
    stones: list[tuple[Color, Point]] = []
    for prop, color in (("AB", "B"), ("AW", "W")):
        try:
            points = root.get(prop)
        except KeyError:
            continue
        for point in points or []:
            stones.append((color, point))
    return stones


def load_game(path: str | Path) -> Game:
    path = Path(path)
    game = sgf.Sgf_game.from_bytes(path.read_bytes())
    root = game.get_root()
    board_size = game.get_size()
    komi = float(game.get_komi() if game.get_komi() is not None else 7.5)

    # KataGo accepts common shorthands. SGF RU values are normalized conservatively.
    raw_rules = (_root_text(root, "RU") or "Chinese").strip().lower()
    if "japan" in raw_rules:
        rules = "Japanese"
    elif "aga" in raw_rules:
        rules = "AGA"
    elif "new zealand" in raw_rules or raw_rules == "nz":
        rules = "New Zealand"
    else:
        rules = "Chinese"

    moves: list[Move] = []
    for number, node in enumerate(game.get_main_sequence()[1:], start=1):
        color, point = node.get_move()
        if color is None:
            continue
        moves.append(Move(number=number, color=color.upper(), point=point))

    return Game(
        path=path,
        board_size=board_size,
        komi=komi,
        rules=rules,
        black_name=_root_text(root, "PB"),
        white_name=_root_text(root, "PW"),
        initial_stones=_initial_stones(root),
        moves=moves,
    )


def infer_player_color(game: Game, configured_names: list[str]) -> Color | None:
    wanted = {name.casefold().strip() for name in configured_names if name.strip()}
    black = (game.black_name or "").casefold().strip()
    white = (game.white_name or "").casefold().strip()
    black_match = bool(black and black in wanted)
    white_match = bool(white and white in wanted)
    if black_match == white_match:
        return None
    return "B" if black_match else "W"
