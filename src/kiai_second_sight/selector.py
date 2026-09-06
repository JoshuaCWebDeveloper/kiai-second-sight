from __future__ import annotations

import hashlib

from .coords import point_to_gtp
from .models import Color, Game, PositionAnalysis, StudyCard


def winrate_for_player(black_winrate: float, player: Color) -> float:
    return black_winrate if player == "B" else 1.0 - black_winrate


def select_cards(
    game: Game,
    player: Color,
    analyses: dict[int, PositionAnalysis],
    *,
    min_start_winrate: float = 0.50,
    min_loss_pp: float = 0.10,
) -> list[StudyCard]:
    """Select the player's moves that cross the configured loss threshold.

    Turn N means the position after N moves. For move number M, compare turns M-1 and M.
    Analysis config is expected to report winrates as BLACK; this matches KaTrain's bundled
    analysis config and makes normalization deterministic.
    """
    game_id = hashlib.sha256(game.path.read_bytes()).hexdigest()[:16]
    cards: list[StudyCard] = []

    for move in game.moves:
        if move.color != player:
            continue
        before = analyses.get(move.number - 1)
        after = analyses.get(move.number)
        if before is None or after is None:
            continue
        wr_before = winrate_for_player(before.root_winrate, player)
        wr_after = winrate_for_player(after.root_winrate, player)
        loss = wr_before - wr_after
        if wr_before > min_start_winrate and loss >= min_loss_pp:
            cards.append(
                StudyCard(
                    card_id=f"{game_id}-m{move.number:03d}",
                    game_id=game_id,
                    source_sgf=str(game.path),
                    player_color=player,
                    move_number=move.number,
                    played_move=point_to_gtp(move.point, game.board_size),
                    winrate_before=wr_before,
                    winrate_after=wr_after,
                    loss_pp=loss,
                    analysis_before=before,
                )
            )
    return cards
