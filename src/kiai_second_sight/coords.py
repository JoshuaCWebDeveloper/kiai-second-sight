from __future__ import annotations

from .models import MovePoint, Point

GTP_COLUMNS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"


def point_to_gtp(point: MovePoint, board_size: int) -> str:
    if point is None:
        return "pass"
    row, col = point
    if not (0 <= row < board_size and 0 <= col < board_size):
        raise ValueError(f"Point {point!r} is outside a {board_size}x{board_size} board")
    if board_size > len(GTP_COLUMNS):
        raise ValueError(f"Board size {board_size} exceeds supported GTP columns")
    return f"{GTP_COLUMNS[col]}{row + 1}"


def gtp_to_point(vertex: str, board_size: int) -> MovePoint:
    if vertex.lower() == "pass":
        return None
    vertex = vertex.upper()
    if len(vertex) < 2 or vertex[0] not in GTP_COLUMNS:
        raise ValueError(f"Invalid GTP vertex: {vertex}")
    col = GTP_COLUMNS.index(vertex[0])
    row = int(vertex[1:]) - 1
    point: Point = (row, col)
    if not (0 <= row < board_size and 0 <= col < board_size):
        raise ValueError(f"Vertex {vertex} is outside a {board_size}x{board_size} board")
    return point
