from __future__ import annotations

import argparse
import json
from pathlib import Path

from .board import board_at_turn
from .config import load_config
from .katago import KataGoAnalyzer
from .manifest import upsert_cards
from .models import Color
from .render import BoardRenderer
from .selector import select_cards
from .sgf_loader import infer_player_color, load_game
from .slideshow import rebuild_slideshow


def _player(value: str) -> Color:
    value = value.strip().upper()
    aliases = {"B": "B", "BLACK": "B", "W": "W", "WHITE": "W"}
    if value not in aliases:
        raise argparse.ArgumentTypeError("player must be black/B or white/W")
    return aliases[value]  # type: ignore[return-value]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kiai", description="Kiai: Second Sight")
    parser.add_argument("--config", default="kiai.toml", help="Path to TOML config")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="Analyze an SGF and add qualifying study cards")
    imp.add_argument("sgf", type=Path)
    imp.add_argument("--me", type=_player, help="Your color; inferred from configured names if omitted")
    imp.add_argument("--dry-run", action="store_true", help="Analyze/select only; do not render or update deck")

    deck = sub.add_parser("rebuild-deck", help="Rebuild PPTX from the current manifest")
    deck.add_argument("--manifest", type=Path)
    deck.add_argument("--output", type=Path)
    return parser


def _require(value: str | None, name: str) -> str:
    if not value:
        raise SystemExit(f"Missing {name}. Set it in kiai.toml or the matching KIAI_* environment variable.")
    return value


def import_game(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    game = load_game(args.sgf)
    player = args.me or infer_player_color(game, cfg.player_names)
    if player is None:
        raise SystemExit(
            f"Could not infer your color (PB={game.black_name!r}, PW={game.white_name!r}). "
            "Use --me black/white or configure [player].names."
        )

    player_moves = [move for move in game.moves if move.color == player]
    turns = {turn for move in player_moves for turn in (move.number - 1, move.number)}
    print(
        f"Loaded {game.path.name}: {game.black_name or '?'} vs {game.white_name or '?'}; "
        f"you are {'Black' if player == 'B' else 'White'}."
    )
    print(f"Analyzing {len(turns)} positions around {len(player_moves)} of your moves...")

    with KataGoAnalyzer(
        _require(cfg.katago_path, "analysis.katago_path"),
        _require(cfg.model_path, "analysis.model_path"),
        _require(cfg.analysis_config_path, "analysis.config_path"),
        max_visits=cfg.max_visits,
    ) as analyzer:
        analyses = analyzer.analyze_game(game, turns)

    cards = select_cards(
        game,
        player,
        analyses,
        min_start_winrate=cfg.min_start_winrate,
        min_loss_pp=cfg.min_loss_pp,
    )
    print(f"Found {len(cards)} qualifying move(s).")
    for card in cards:
        print(
            f"  Move {card.move_number:>3} {card.played_move:>4}: "
            f"{card.winrate_before*100:5.1f}% → {card.winrate_after*100:5.1f}% "
            f"(-{card.loss_pp*100:.1f} pp)"
        )

    if args.dry_run:
        print(json.dumps([{"move": c.move_number, "loss_pp": c.loss_pp} for c in cards], indent=2))
        return 0

    root = cfg.output_root
    images_dir = root / "cards" / "images"
    renderer = BoardRenderer(cfg.image_size)
    for card in cards:
        board = board_at_turn(game, card.move_number - 1)
        q = images_dir / f"{card.card_id}-question.png"
        a = images_dir / f"{card.card_id}-answer.png"
        renderer.render_question(board, q)
        renderer.render_answer(
            board,
            card.analysis_before,
            card.played_move,
            a,
            top_moves=cfg.top_moves,
        )
        card.question_image = str(q)
        card.answer_image = str(a)

    manifest_path = root / "cards" / "manifest.json"
    manifest = upsert_cards(manifest_path, cards)
    deck_path = rebuild_slideshow(manifest, root / "deck" / "second-sight.pptx")
    print(f"Updated {manifest_path}")
    print(f"Updated {deck_path}")
    return 0


def rebuild_deck(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    manifest_path = args.manifest or cfg.output_root / "cards" / "manifest.json"
    output = args.output or cfg.output_root / "deck" / "second-sight.pptx"
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    rebuild_slideshow(manifest, output)
    print(f"Updated {output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "import":
        return import_game(args)
    if args.command == "rebuild-deck":
        return rebuild_deck(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
