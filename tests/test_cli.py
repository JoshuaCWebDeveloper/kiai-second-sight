from pathlib import Path

import pytest

from kiai_second_sight.cli import _sgf_inputs, build_parser


def test_import_accepts_sgf_file(tmp_path: Path):
    sgf = tmp_path / "game.sgf"
    sgf.write_text("(;GM[1])")
    assert _sgf_inputs(sgf) == [sgf]


def test_import_accepts_directory_of_sgfs(tmp_path: Path):
    b = tmp_path / "b.sgf"
    a = tmp_path / "A.SGF"
    ignored = tmp_path / "notes.txt"
    for path in (b, a, ignored):
        path.write_text("x")

    assert _sgf_inputs(tmp_path) == [a, b]


def test_import_directory_requires_sgfs(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(SystemExit, match="No SGF files found"):
        _sgf_inputs(tmp_path)


def test_import_help_describes_file_or_directory():
    parser = build_parser()
    args = parser.parse_args(["import", "/games"])
    assert args.sgf == Path("/games")
