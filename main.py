"""TouchPlay — chơi game PC bằng màn hình cảm ứng.

Chạy:
    py -3 main.py
hoặc double-click play.bat

Trên màn hình:
    Sửa              kéo thả nút, thêm chuột / phím / cần, rồi Lưu
    Ẩn               trả chuột cho desktop; nút Hiện để bật lại
    Ctrl+Shift+E     vào / lưu chế độ sửa
    Ctrl+Shift+H     ẩn hoặc hiện overlay
    Ctrl+Shift+Q     thoát

Mở game ở chế độ cửa sổ không viền. Nếu game chạy bằng Administrator
thì mở TouchPlay bằng Administrator, nếu không game sẽ nuốt phím.
"""

from __future__ import annotations

import ctypes
import sys
import traceback
from pathlib import Path


def enable_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def self_test() -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["TOUCHPLAY_NO_TRAY"] = "1"
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QEventPoint, QImage
    from PySide6.QtWidgets import QApplication

    from input_sender import _from_touch, input_struct_ok
    from overlay import Overlay, _phase, build_default, direction_keys

    assert input_struct_ok(), "INPUT struct size does not match Windows"
    assert _from_touch(0xFF515700)
    assert _from_touch(0xFF515701)
    assert not _from_touch(0)
    assert not _from_touch(0x54503531)
    assert direction_keys(0, 0, "W", "S", "A", "D") == set()
    assert direction_keys(0, -0.8, "W", "S", "A", "D") == {"W"}
    assert direction_keys(0.9, 0.9, "W", "S", "A", "D") == {"S", "D"}
    assert direction_keys(-1, -1, "W", "S", "A", "D") == {"W", "A"}
    assert direction_keys(0.1, 0.5, "W", "S", "A", "D") == {"S"}
    assert _phase(QEventPoint.State.Pressed) == "down"
    assert _phase(QEventPoint.State.Updated) == "move"
    assert _phase(QEventPoint.State.Released) == "up"
    assert _phase(QEventPoint.State.Stationary) == "ignore"

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_SynthesizeMouseForUnhandledTouchEvents, False)
    app = QApplication([])
    overlay = Overlay()
    overlay.resize(1000, 800)
    overlay.controls = build_default(overlay.alloc)
    overlay.sensitivity = 1.5
    overlay.block_touch = True

    class FakeSender:
        def __init__(self) -> None:
            self.down: list[str] = []
            self.up: list[str] = []
            self.moves: list[tuple[int, int]] = []

        def key_down(self, name: str) -> None:
            self.down.append(name)

        def key_up(self, name: str) -> None:
            self.up.append(name)

        def mouse_down(self, button: str) -> None:
            self.down.append(button)

        def mouse_up(self, button: str) -> None:
            self.up.append(button)

        def mouse_move(self, dx: int, dy: int) -> None:
            self.moves.append((dx, dy))

        def release_all(self) -> None:
            self.up.append("*")

    overlay.sender = FakeSender()
    overlay._down("t1", 0.05 * 1000, 0.16 * 800)
    assert overlay.sender.down == ["ESC"], overlay.sender.down
    overlay._up("t1", 0.05 * 1000, 0.16 * 800)
    assert overlay.sender.up == ["ESC"]

    overlay.sender = FakeSender()
    cx, cy = 0.15 * 1000, 0.64 * 800
    overlay._down("t2", cx, cy - 80)
    assert overlay.sender.down == ["W"], overlay.sender.down
    overlay._move("t2", cx - 70, cy)
    assert "A" in overlay.sender.down
    overlay._up("t2", cx, cy)
    assert "W" in overlay.sender.up and "A" in overlay.sender.up

    overlay.sender = FakeSender()
    overlay._down("t3", 800, 400)
    overlay._move("t3", 810, 404)
    overlay._up("t3", 810, 404)
    overlay._down("t4", 500, 400)
    overlay._move("t4", 520, 408)
    overlay._up("t4", 520, 408)
    assert overlay.sender.moves == [(15, 6)], overlay.sender.moves
    assert overlay.sender.down == []

    # Movement, look and fire must work at the same time, like a phone game.
    overlay.sender = FakeSender()
    overlay._down("walk", cx, cy - 80)
    overlay._down("look", 800, 400)
    overlay._down("fire", 900, 448)
    overlay._move("look", 820, 410)
    assert "W" in overlay.sender.down and "left" in overlay.sender.down
    assert overlay.sender.moves == [(30, 15)]
    overlay._cancel_pointer("walk")
    assert "W" in overlay.sender.up
    assert "look" in overlay.pointers and "fire" in overlay.pointers
    overlay._up("fire", 900, 448)
    overlay._up("look", 820, 410)
    assert "left" in overlay.sender.up
    assert not overlay.pointers

    # Fractional motion accumulates; invert Y only changes the vertical axis.
    from overlay import ZoneControl

    zone = next(c for c in overlay.controls if isinstance(c, ZoneControl))
    overlay.sender = FakeSender()
    zone.invert_y = True
    overlay._down("look", 800, 400)
    overlay._move("look", 800.4, 400.4)
    assert overlay.sender.moves == []
    overlay._move("look", 800.8, 400.8)
    assert overlay.sender.moves == [(1, -1)], overlay.sender.moves
    overlay._cancel_pointer("look")
    zone.invert_y = False

    # Native touch pixels must map back to Qt coordinates at 150% scale.
    from unittest.mock import patch

    overlay.sender = FakeSender()
    with patch("overlay.window_rect", return_value=(-150, 0, 1500, 1200)):
        overlay._native_down(42, 75, 192)
        assert overlay.sender.down == ["ESC"]
        overlay._native_up(42, 75, 192)
        assert overlay.sender.up == ["ESC"]

    before = overlay.to_layout()
    overlay.sender = FakeSender()
    overlay.enter_edit()
    overlay._down("m", 50, 128)
    overlay._move("m", 90, 128)
    overlay._up("m", 90, 128)
    assert overlay.sender.down == []
    assert overlay.editing
    overlay.cancel_edit()
    assert overlay.to_layout()["controls"] == before["controls"]
    assert not overlay.editing

    image = QImage(overlay.size(), QImage.Format.Format_ARGB32_Premultiplied)
    overlay.render(image)
    overlay.enter_edit()
    overlay.popup = {"kind": "keys", "title": "Chọn phím", "mode": "add_key"}
    overlay.render(image)
    assert not image.isNull()
    app.quit()
    print("self-test ok")


def main() -> None:
    if "--self-test" in sys.argv:
        self_test()
        return
    if sys.platform != "win32":
        print("TouchPlay chỉ chạy trên Windows.")
        sys.exit(1)
    enable_dpi()
    if "--debug" in sys.argv:
        import logging

        logging.basicConfig(
            filename=Path(__file__).resolve().parent / "touchplay-input.log",
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(message)s",
            encoding="utf-8",
        )
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from input_sender import acquire_single_instance, install_mouse_hook, uninstall_mouse_hook
        from overlay import Overlay
    except ImportError:
        print("Thiếu PySide6. Chạy: py -3 -m pip install -r requirements.txt")
        sys.exit(1)

    if not acquire_single_instance():
        print("TouchPlay đang chạy rồi. Dùng nút Hiện hoặc Ctrl+Shift+H.")
        return

    def log_crash(exc_type, value, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, value, tb))
        try:
            (Path(__file__).resolve().parent / "touchplay.log").write_text(text, encoding="utf-8")
        except OSError:
            pass
        uninstall_mouse_hook()
        sys.__excepthook__(exc_type, value, tb)

    sys.excepthook = log_crash
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_SynthesizeMouseForUnhandledTouchEvents, False)
    app = QApplication(sys.argv)
    app.setApplicationName("TouchPlay")
    app.setQuitOnLastWindowClosed(False)
    overlay = Overlay(debug="--debug" in sys.argv)
    overlay.show_overlay()
    install_mouse_hook()
    print("TouchPlay đang bật. Sửa = sắp nút, Ẩn = trả desktop, Ctrl+Shift+Q = thoát.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
