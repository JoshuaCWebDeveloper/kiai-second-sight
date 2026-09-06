from pathlib import Path

from kiai_second_sight.sgf_loader import infer_player_color, load_game


def test_load_and_infer_player(tmp_path: Path):
    sgf = tmp_path / "game.sgf"
    sgf.write_text("(;GM[1]FF[4]SZ[9]KM[7.5]PB[Opponent]PW[Joshua];B[dd];W[ee])")
    game = load_game(sgf)
    assert game.board_size == 9
    assert game.komi == 7.5
    assert game.black_name == "Opponent"
    assert game.white_name == "Joshua"
    assert len(game.moves) == 2
    assert infer_player_color(game, ["joshua"]) == "W"
