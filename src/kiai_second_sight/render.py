from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .board import BoardState
from .coords import GTP_COLUMNS, gtp_to_point
from .models import PositionAnalysis


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold
        else ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


class BoardRenderer:
    def __init__(self, image_size: int = 1200) -> None:
        self.image_size = image_size

    def render_question(self, board: BoardState, output: str | Path) -> Path:
        image, geom = self._base_board(board)
        if board.last_move is not None:
            self._draw_last_move(image, geom, board.last_move, board)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output)
        return output

    def render_answer(
        self,
        board: BoardState,
        analysis: PositionAnalysis,
        played_move: str,
        output: str | Path,
        *,
        top_moves: int = 5,
    ) -> Path:
        image, geom = self._base_board(board, analysis.ownership)
        self._draw_candidates(image, geom, board, analysis, top_moves)
        if played_move.lower() != "pass":
            point = gtp_to_point(played_move, board.size)
            assert point is not None
            self._draw_played_move(image, geom, point)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output)
        return output

    def _base_board(
        self,
        board: BoardState,
        ownership: list[float] | None = None,
    ) -> tuple[Image.Image, dict[str, float]]:
        size = self.image_size
        margin = size * 0.135
        grid_end = size - margin * 0.72
        span = grid_end - margin
        step = span / (board.size - 1)

        # Warm wood-like base with a tiny deterministic vertical stripe pattern.
        image = Image.new("RGB", (size, size), (222, 169, 78))
        draw = ImageDraw.Draw(image, "RGBA")
        for x in range(size):
            shade = int(6 * math.sin(x / 14.0) + 3 * math.sin(x / 41.0))
            draw.line((x, 0, x, size), fill=(255, 225, 140, max(0, 18 + shade)))

        geom = {"margin": margin, "grid_end": grid_end, "span": span, "step": step}

        if ownership and len(ownership) == board.size * board.size:
            self._draw_ownership(draw, geom, board.size, ownership)

        line_width = max(1, round(size / 900))
        for idx in range(board.size):
            p = margin + idx * step
            draw.line((margin, p, grid_end, p), fill=(24, 24, 20, 210), width=line_width)
            draw.line((p, margin, p, grid_end), fill=(24, 24, 20, 210), width=line_width)

        self._draw_star_points(draw, geom, board.size)
        self._draw_coordinates(draw, geom, board.size)
        self._draw_stones(image, geom, board)
        return image, geom

    def _xy(self, geom: dict[str, float], board_size: int, row: int, col: int) -> tuple[float, float]:
        # Internal rows count from the bottom; pixels count from the top.
        x = geom["margin"] + col * geom["step"]
        y = geom["margin"] + (board_size - 1 - row) * geom["step"]
        return x, y

    def _draw_coordinates(self, draw: ImageDraw.ImageDraw, geom: dict[str, float], n: int) -> None:
        font = _font(max(18, round(self.image_size * 0.037)), bold=True)
        fill = (55, 57, 62, 230)
        for col in range(n):
            x, _ = self._xy(geom, n, 0, col)
            text = GTP_COLUMNS[col]
            bbox = draw.textbbox((0, 0), text, font=font)
            draw.text((x - (bbox[2] - bbox[0]) / 2, geom["grid_end"] + geom["step"] * 0.48), text, font=font, fill=fill)
        for row in range(n):
            _, y = self._xy(geom, n, row, 0)
            text = str(row + 1)
            bbox = draw.textbbox((0, 0), text, font=font)
            draw.text((geom["margin"] * 0.42 - (bbox[2] - bbox[0]) / 2, y - (bbox[3] - bbox[1]) / 2), text, font=font, fill=fill)

    def _draw_star_points(self, draw: ImageDraw.ImageDraw, geom: dict[str, float], n: int) -> None:
        if n == 9:
            coords = [2, 4, 6]
            points = {(2, 2), (2, 6), (6, 2), (6, 6), (4, 4)}
        elif n == 13:
            coords = [3, 6, 9]
            points = {(r, c) for r in coords for c in coords}
        elif n == 19:
            coords = [3, 9, 15]
            points = {(r, c) for r in coords for c in coords}
        else:
            return
        radius = max(3, self.image_size * 0.007)
        for row, col in points:
            x, y = self._xy(geom, n, row, col)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(8, 8, 8, 255))

    def _draw_stones(self, image: Image.Image, geom: dict[str, float], board: BoardState) -> None:
        draw = ImageDraw.Draw(image, "RGBA")
        r = geom["step"] * 0.46
        for (row, col), color in board.stones.items():
            x, y = self._xy(geom, board.size, row, col)
            shadow = r * 0.08
            draw.ellipse((x-r+shadow, y-r+shadow, x+r+shadow, y+r+shadow), fill=(0, 0, 0, 75))
            if color == "B":
                draw.ellipse((x-r, y-r, x+r, y+r), fill=(25, 25, 25, 255), outline=(0, 0, 0, 255), width=2)
                draw.ellipse((x-r*0.55, y-r*0.62, x+r*0.15, y+r*0.08), fill=(95, 95, 95, 95))
            else:
                draw.ellipse((x-r, y-r, x+r, y+r), fill=(244, 244, 244, 255), outline=(120, 120, 120, 255), width=2)
                draw.ellipse((x-r*0.52, y-r*0.62, x+r*0.20, y+r*0.06), fill=(255, 255, 255, 180))

    def _draw_last_move(self, image: Image.Image, geom: dict[str, float], point: tuple[int, int], board: BoardState) -> None:
        row, col = point
        x, y = self._xy(geom, board.size, row, col)
        r = geom["step"] * 0.22
        stone = board.stones.get(point)
        fill = (20, 20, 20, 255) if stone == "W" else (240, 240, 240, 255)
        draw = ImageDraw.Draw(image, "RGBA")
        draw.ellipse((x-r, y-r, x+r, y+r), outline=fill, width=max(3, round(self.image_size / 200)))

    def _draw_ownership(self, draw: ImageDraw.ImageDraw, geom: dict[str, float], n: int, ownership: list[float]) -> None:
        radius = geom["step"] * 0.42
        for top_row in range(n):
            row = n - 1 - top_row
            for col in range(n):
                value = float(ownership[top_row * n + col])
                if abs(value) < 0.08:
                    continue
                x, y = self._xy(geom, n, row, col)
                # KataGo ownership is from the configured analysis perspective. Use a warm
                # red/orange for positive, cool violet for negative, with confidence alpha.
                alpha = int(min(125, 25 + abs(value) * 100))
                fill = (220, 68, 30, alpha) if value > 0 else (74, 49, 146, alpha)
                draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=fill)

    def _draw_candidates(
        self,
        image: Image.Image,
        geom: dict[str, float],
        board: BoardState,
        analysis: PositionAnalysis,
        top_moves: int,
    ) -> None:
        infos = sorted(analysis.move_infos, key=lambda item: item.get("order", 9999))[:top_moves]
        if not infos:
            return
        best_wr = float(infos[0].get("winrate", analysis.root_winrate))
        draw = ImageDraw.Draw(image, "RGBA")
        font = _font(max(16, round(self.image_size * 0.027)), bold=True)
        r = geom["step"] * 0.43

        for info in infos:
            vertex = str(info.get("move", "pass"))
            if vertex.lower() == "pass":
                continue
            point = gtp_to_point(vertex, board.size)
            assert point is not None
            row, col = point
            x, y = self._xy(geom, board.size, row, col)
            loss = max(0.0, best_wr - float(info.get("winrate", best_wr)))
            # Green -> yellow -> orange -> red, keyed to win-rate loss.
            if loss < 0.01:
                fill = (105, 190, 35, 225)
            elif loss < 0.03:
                fill = (220, 205, 35, 230)
            elif loss < 0.08:
                fill = (238, 140, 35, 230)
            else:
                fill = (220, 72, 45, 235)
            draw.ellipse((x-r, y-r, x+r, y+r), fill=fill, outline=(15, 15, 15, 220), width=2)
            label = f"-{loss*100:.1f}\n{int(info.get('visits', 0))}"
            if info is infos[0]:
                label = f"+0.0\n{int(info.get('visits', 0))}"
            lines = label.splitlines()
            total_h = sum(draw.textbbox((0, 0), line, font=font)[3] for line in lines)
            yy = y - total_h / 2
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=font)
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
                draw.text((x-w/2, yy), line, font=font, fill=(5, 5, 5, 255))
                yy += h

    def _draw_played_move(self, image: Image.Image, geom: dict[str, float], point: tuple[int, int]) -> None:
        draw = ImageDraw.Draw(image, "RGBA")
        row, col = point
        x, y = self._xy(geom, round(geom["span"] / geom["step"]) + 1, row, col)
        r = geom["step"] * 0.18
        draw.rectangle((x-r, y-r, x+r, y+r), outline=(255, 255, 255, 255), width=max(4, round(self.image_size / 180)))
