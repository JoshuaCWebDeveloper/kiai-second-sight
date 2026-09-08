from __future__ import annotations

import argparse
import json
from pathlib import Path

from .board import board_at_turn
from .config import load_config
from .coords import point_to_gtp
from .katago import KataGoAnalyzer
from .managed_katago import KataGoSetupError, resolve_katago
from .manifest import upsert_cards
from .models import Color
from .render import BoardRenderer
from .selector import played_move_analysis, screening_candidates, select_cards
from .sgf_loader import infer_player_color, load_game
from .slideshow import rebuild_slideshow


def _player(value: str) -> Color:
    value = value.strip().upper()
    aliases = {"B": "B", "BLACK": "B", "W": "W", "WHITE": "W"}
    if value not in aliases:
        raise argparse.ArgumentTypeError("player must be black/B or white/W")
    return aliases[value]  # type: ignore[return-value]


def _progress(label: str):
    """Return a callback that renders position-level progress on one terminal line."""

    def report(done: int, total: int) -> None:
        percent = 100 if total == 0 else round(done * 100 / total)
        print(f"\r{label}: {done}/{total} ({percent:3d}%)", end="", flush=True)
        if done >= total:
            print()

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kiai", description="Kiai: Second Sight")
    parser.add_argument("--config", default="kiai.toml", help="Path to TOML config")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser(
        "import", help="Analyze an SGF file or directory and add qualifying study cards"
    )
    imp.add_argument("sgf", type=Path, help="SGF file or directory containing SGF files")
    imp.add_argument(
        "--me", type=_player, help="Your color; inferred from configured names if omitted"
    )
    imp.add_argument(
        "--dry-run", action="store_true", help="Analyze/select only; do not render or update deck"
    )

    sub.add_parser("setup", help="Download/verify the configured KataGo runtime")

    deck = sub.add_parser("rebuild-deck", help="Rebuild PPTX from the current manifest")
    deck.add_argument("--manifest", type=Path)
    deck.add_argument("--output", type=Path)
    return parser


def _sgf_inputs(path: Path) -> list[Path]:
    """Return one SGF file or the SGF files directly contained in a directory."""
    if path.is_file():
        if path.suffix.lower() != ".sgf":
            raise SystemExit(f"Input file is not an SGF: {path}")
        return [path]
    if path.is_dir():
        files = sorted(
            (
                entry
                for entry in path.iterdir()
                if entry.is_file() and entry.suffix.lower() == ".sgf"
            ),
            key=lambda entry: entry.name.lower(),
        )
        if not files:
            raise SystemExit(f"No SGF files found in directory: {path}")
        return files
    raise SystemExit(f"SGF input does not exist: {path}")


def _analyze_game(sgf_path: Path, args: argparse.Namespace, cfg, katago):
    game = load_game(sgf_path)
    player = args.me or infer_player_color(game, cfg.player_names)
    if player is None:
        raise SystemExit(
            f"Could not infer your color for {game.path.name} "
            f"(PB={game.black_name!r}, PW={game.white_name!r}). "
            "Use --me black/white or configure [player].names."
        )

    player_moves = [move for move in game.moves if move.color == player]
    screening_before_turns = {move.number - 1 for move in player_moves}
    print(
        f"Loaded {game.path.name}: {game.black_name or '?'} vs {game.white_name or '?'}; "
        f"you are {'Black' if player == 'B' else 'White'}."
    )
    print(
        f"Screening {len(player_moves)} of your moves at {cfg.screening_visits} visits "
        f"({len(screening_before_turns)} pre-move positions)..."
    )

    with KataGoAnalyzer(
        katago.executable,
        katago.model,
        katago.config,
        max_visits=cfg.screening_visits,
    ) as analyzer:
        screening = analyzer.analyze_game(
            game,
            screening_before_turns,
            include_ownership=False,
            on_progress=_progress("Screening pre-move positions"),
        )

        fallback_turns: set[int] = set()
        for move in player_moves:
            before = screening.get(move.number - 1)
            if before is None:
                fallback_turns.add(move.number)
                continue
            estimate = played_move_analysis(
                before, point_to_gtp(move.point, game.board_size), move.number
            )
            if estimate is None:
                fallback_turns.add(move.number)
            else:
                screening[move.number] = estimate

        if fallback_turns:
            print(
                f"Screening {len(fallback_turns)} post-move position(s) whose played move "
                "was not searched from the previous position..."
            )
            screening.update(
                analyzer.analyze_game(
                    game,
                    fallback_turns,
                    include_ownership=False,
                    on_progress=_progress("Screening fallback post-move positions"),
                )
            )

    candidate_moves = screening_candidates(
        game,
        player,
        screening,
        min_start_winrate=cfg.min_start_winrate,
        min_loss_pp=cfg.min_loss_pp,
        start_margin=cfg.screening_start_margin,
        loss_margin=cfg.screening_loss_margin,
    )
    print(f"Screening retained {len(candidate_moves)} move(s) for full analysis.")

    if candidate_moves:
        deep_turns = {
            turn for move_number in candidate_moves for turn in (move_number - 1, move_number)
        }
        print(f"Analyzing {len(deep_turns)} candidate positions at {cfg.max_visits} visits...")
        with KataGoAnalyzer(
            katago.executable,
            katago.model,
            katago.config,
            max_visits=cfg.max_visits,
        ) as analyzer:
            analyses = analyzer.analyze_game(
                game,
                deep_turns,
                on_progress=_progress("Full candidate analysis"),
            )
    else:
        analyses = {}

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
            f"{card.winrate_before * 100:5.1f}% → {card.winrate_after * 100:5.1f}% "
            f"(-{card.loss_pp * 100:.1f} pp)"
        )
    return game, cards


def import_game(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    sgf_files = _sgf_inputs(args.sgf)

    try:
        katago = resolve_katago(cfg)
    except KataGoSetupError as exc:
        raise SystemExit(str(exc)) from exc
    source = "managed" if katago.managed else "configured external"
    print(f"Using {source} KataGo: {katago.executable}")
    if len(sgf_files) > 1 or args.sgf.is_dir():
        print(f"Importing {len(sgf_files)} SGF file(s) from {args.sgf}.")

    root = cfg.output_root
    images_dir = root / "cards" / "images"
    renderer = BoardRenderer(cfg.image_size)
    all_cards = []

    for index, sgf_path in enumerate(sgf_files, start=1):
        if len(sgf_files) > 1:
            print(f"\n[{index}/{len(sgf_files)}] {sgf_path.name}")
        game, cards = _analyze_game(sgf_path, args, cfg, katago)
        all_cards.extend(cards)

        if args.dry_run:
            continue

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

    if args.dry_run:
        print(
            json.dumps(
                [{"move": card.move_number, "loss_pp": card.loss_pp} for card in all_cards],
                indent=2,
            )
        )
        return 0

    manifest_path = root / "cards" / "manifest.json"
    manifest = upsert_cards(manifest_path, all_cards)
    deck_path = rebuild_slideshow(manifest, root / "deck" / "second-sight.pptx")
    print(f"Updated {manifest_path}")
    print(f"Updated {deck_path}")
    return 0


def setup(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    try:
        katago = resolve_katago(cfg, install=True)
    except KataGoSetupError as exc:
        raise SystemExit(str(exc)) from exc
    source = "managed" if katago.managed else "configured external"
    print(f"KataGo ready ({source}).")
    print(f"  executable: {katago.executable}")
    print(f"  model:      {katago.model}")
    print(f"  config:     {katago.config}")
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
    if args.command == "setup":
        return setup(args)
    if args.command == "rebuild-deck":
        return rebuild_deck(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
