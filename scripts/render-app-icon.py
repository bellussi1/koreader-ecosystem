from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def _png_frames(svg_path: Path, sizes: tuple[int, ...]) -> list[tuple[int, bytes]]:
    app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(QByteArray(svg_path.read_bytes()))
    if not renderer.isValid():
        raise RuntimeError("The application icon SVG is invalid.")

    frames: list[tuple[int, bytes]] = []
    for size in sizes:
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError("A PNG frame could not be rendered for the application icon.")
        frames.append((size, bytes(buffer.data())))
        buffer.close()

    del app
    return frames


def create_icon(svg_path: Path, output_path: Path) -> None:
    frames = _png_frames(svg_path, (16, 24, 32, 48, 64, 128, 256))
    directory_size = 6 + 16 * len(frames)
    offset = directory_size
    directory = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    image_data = bytearray()

    for size, payload in frames:
        dimension = 0 if size == 256 else size
        directory.extend(
            struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(payload), offset)
        )
        image_data.extend(payload)
        offset += len(payload)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(directory + image_data)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    create_icon(
        root / "assets" / "KOReaderEcosystem.svg",
        root / "assets" / "KOReaderEcosystem.ico",
    )
