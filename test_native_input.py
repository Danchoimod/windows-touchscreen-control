"""Windows integration checks: py -3 -m unittest -v test_native_input.

Temporarily shows small test windows and injects contacts into those windows.
No game needs to be running. The foreground window is restored on exit.
"""

import ctypes
import os
import unittest
from ctypes import wintypes
from unittest.mock import patch

os.environ["TOUCHPLAY_NO_TRAY"] = "1"

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtTest import QTest
from PySide6.QtCore import QRect, Qt

import input_sender as native
from main import enable_dpi
from overlay import Overlay, build_default


class POINTER_TOUCH_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerInfo", native.POINTER_INFO),
        ("touchFlags", wintypes.UINT),
        ("touchMask", wintypes.UINT),
        ("rcContact", wintypes.RECT),
        ("rcContactRaw", wintypes.RECT),
        ("orientation", wintypes.UINT),
        ("pressure", wintypes.UINT),
    ]


class NativeInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        enable_dpi()
        cls.app = QApplication.instance() or QApplication([])
        native._bind_shield_api()
        native.user32.InitializeTouchInjection.argtypes = [wintypes.UINT, wintypes.DWORD]
        native.user32.InitializeTouchInjection.restype = wintypes.BOOL
        native.user32.InjectTouchInput.argtypes = [wintypes.UINT, ctypes.POINTER(POINTER_TOUCH_INFO)]
        native.user32.InjectTouchInput.restype = wintypes.BOOL

    def test_hide_show_preserves_transparency(self):
        foreground = native.user32.GetForegroundWindow()
        background = QWidget()
        background.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        geometry = QRect(100, 100, 640, 480)
        background.setGeometry(geometry)
        background.show()
        overlay = Overlay()
        overlay.keep_timer.stop()
        overlay.controls = build_default(overlay.alloc)
        overlay.toast_until = 0
        try:
            with patch.object(overlay, "_desktop", return_value=geometry), patch("overlay.set_touch_target"):
                for cycle, color in enumerate(("#204060", "#804020", "#206040", "#604080")):
                    background.setStyleSheet(f"background-color: {color};")
                    background.repaint()
                    overlay.show_overlay()
                    QTest.qWait(150)
                    left, top, width, height = native.window_rect(int(overlay.winId()))
                    sample = self.app.primaryScreen().grabWindow(0).toImage()
                    pixel = sample.pixelColor(left + width // 2, top + round(height * .85))
                    expected = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
                    actual = (pixel.red(), pixel.green(), pixel.blue())
                    self.assertTrue(all(abs(a - b) <= 12 for a, b in zip(actual, expected)),
                                    f"cycle {cycle}: background {expected} is covered by {actual}")
                    overlay.hide_overlay()
                    QTest.qWait(40)
        finally:
            overlay._release_pointers()
            native.set_touch_target(native.shield_hwnd(), False)
            native.close_touch_shield()
            self.app.removeNativeEventFilter(overlay._native_filter)
            native.uninstall_mouse_hook()
            overlay.restore.close()
            overlay.close()
            background.close()
            if foreground:
                native.user32.SetForegroundWindow(foreground)

    def test_real_pointer_reaches_scaled_overlay(self):
        if not native.user32.InitializeTouchInjection(10, 3):
            self.skipTest(f"Touch injection unavailable: {native.kernel32.GetLastError()}")
        foreground = native.user32.GetForegroundWindow()
        overlay = Overlay()
        overlay.controls = build_default(overlay.alloc)
        overlay.resize(800, 600)
        overlay.move(100, 100)
        overlay.hidden = True  # Do not request system-wide pointer redirection.
        overlay.show()
        self.app.processEvents()
        left, top, width, height = native.window_rect(int(overlay.winId()))
        native.show_touch_shield(left, top, width, height)
        native.make_visual_passthrough(int(overlay.winId()))
        QTest.qWait(150)  # Let DWM commit the test windows before hit-testing.
        overlay.hidden = False
        events = []
        contacts = {}
        injected = False

        def frame(pid, nx, ny, flags, prepare=False):
            nonlocal injected
            touch = POINTER_TOUCH_INFO()
            touch.pointerInfo.pointerType = 2
            touch.pointerInfo.pointerId = pid
            touch.pointerInfo.pointerFlags = flags
            x, y = round(left + nx * width), round(top + ny * height)
            touch.pointerInfo.ptPixelLocation = wintypes.POINT(x, y)
            touch.touchMask = 1 | 2 | 4
            touch.rcContact = wintypes.RECT(x - 2, y - 2, x + 2, y + 2)
            touch.orientation = 90
            touch.pressure = 32000
            pending = dict(contacts)
            pending[pid] = touch
            if prepare:
                contacts[pid] = touch
                return
            touches = (POINTER_TOUCH_INFO * len(pending))(*pending.values())
            if not native.user32.InjectTouchInput(len(touches), touches):
                error = native.kernel32.GetLastError()
                if error == 5 and not injected:
                    self.skipTest("Windows denied touch injection on this desktop")
                raise OSError(error, f"InjectTouchInput failed: {[(c.pointerInfo.pointerId, hex(c.pointerInfo.pointerFlags), c.pointerInfo.ptPixelLocation.x, c.pointerInfo.ptPixelLocation.y) for c in touches]}")
            contacts[pid] = touch
            injected = True
            QTest.qWait(60)
            if flags & 0x40000:
                contacts.pop(pid)
            else:
                for contact in contacts.values():
                    contact.pointerInfo.pointerFlags = 0x20000 | 2 | 4

        try:
            with patch("input_sender._send", side_effect=lambda inp: events.append(inp) or True), \
                    patch.object(overlay, "_desktop", return_value=overlay.geometry()), \
                    patch("overlay.set_touch_target"):
                for cycle in range(3):
                    if cycle:
                        overlay.hide_overlay()
                        QTest.qWait(40)
                        overlay.show_overlay()
                        QTest.qWait(150)
                    frame(1, 0.05, 0.16, 0x10000 | 2 | 4, prepare=True)
                    frame(2, 0.80, 0.50, 0x10000 | 2 | 4)
                    self.assertEqual(len(overlay.pointers), 2,
                                     f"cycle {cycle}: touch contacts were not delivered")
                    self.assertEqual(events[-1].type, native.INPUT_KEYBOARD)
                    self.assertEqual(events[-1].u.ki.wScan, 1)  # Escape
                    # Keep both fingers down across the production 250 ms
                    # overlay maintenance timer, rather than disabling it.
                    for _ in range(5):
                        frame(2, 0.80, 0.50, 0x20000 | 2 | 4)
                    self.assertEqual(len(overlay.pointers), 2)
                    frame(2, 0.82, 0.52, 0x20000 | 2 | 4)
                    self.assertEqual(events[-1].type, native.INPUT_MOUSE)
                    frame(2, 0.82, 0.52, 0x40000)
                    self.assertEqual(len(overlay.pointers), 1)
                    frame(1, 0.05, 0.16, 0x40000)
                    self.assertFalse(overlay.pointers)
                    self.assertTrue(events[-1].u.ki.dwFlags & native.KEYEVENTF_KEYUP)
                # Restore using an actual touch on "Hien", not a direct call.
                overlay.hide_overlay()
                QTest.qWait(80)
                rx, ry, rw, rh = native.window_rect(int(overlay.restore.winId()))
                nx = (rx + rw // 2 - left) / width
                ny = (ry + rh // 2 - top) / height
                frame(1, nx, ny, 0x10000 | 2 | 4)
                frame(1, nx, ny, 0x40000)
                self.assertFalse(overlay.hidden)
                self.assertTrue(native._GetWindowLongPtrW(native.shield_hwnd(), native.GWL_EXSTYLE) & native.WS_EX_NOACTIVATE)
        finally:
            # Release injected contacts even when an assertion fails.
            try:
                if injected:
                    for pid, contact in list(contacts.items()):
                        x, y = contact.pointerInfo.ptPixelLocation.x, contact.pointerInfo.ptPixelLocation.y
                        frame(pid, (x - left) / width, (y - top) / height, 0x40000)
            finally:
                overlay.keep_timer.stop()
                overlay.stick_timer.stop()
                overlay._release_pointers()
                native.hide_touch_shield()
                native.close_touch_shield()
                self.app.removeNativeEventFilter(overlay._native_filter)
                native.uninstall_mouse_hook()
                overlay.restore.close()
                overlay.close()
                if foreground:
                    native.user32.SetForegroundWindow(foreground)

    def test_sendinput_reaches_foreground_window(self):
        keys = []

        class Receiver(QWidget):
            def keyPressEvent(self, event):
                keys.append((event.text(), "down"))

            def keyReleaseEvent(self, event):
                keys.append((event.text(), "up"))

        foreground = native.user32.GetForegroundWindow()
        game = native.hook_state["game"]
        receiver = Receiver()
        receiver.setWindowTitle("TouchPlay input test")
        receiver.setGeometry(100, 100, 240, 120)
        receiver.show()
        receiver.activateWindow()
        receiver.setFocus()
        native.user32.SetForegroundWindow(int(receiver.winId()))
        QTest.qWait(40)
        sender = native.InputSender()
        try:
            if native.user32.GetForegroundWindow() != int(receiver.winId()):
                self.skipTest("Windows did not allow the test window to take focus")
            sender.key_down("W")
            QTest.qWait(20)
            sender.key_up("W")
            QTest.qWait(20)
            self.assertEqual([(text.lower(), phase) for text, phase in keys], [("w", "down"), ("w", "up")])
        finally:
            sender.release_all()
            receiver.close()
            native.hook_state["game"] = game
            if foreground:
                native.user32.SetForegroundWindow(foreground)

    def test_keyboard_scancodes_and_shared_holds(self):
        events = []
        sender = native.InputSender()
        with patch("input_sender._send", side_effect=lambda inp: events.append(inp) or True):
            sender.key_down("W")
            sender.key_down("W")
            sender.key_up("W")
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].u.ki.wScan, 0x11)
            self.assertTrue(events[0].u.ki.dwFlags & native.KEYEVENTF_SCANCODE)
            sender.release_all()
            self.assertEqual(len(events), 2)
            self.assertTrue(events[-1].u.ki.dwFlags & native.KEYEVENTF_KEYUP)

    def test_restore_game_focus_after_touch_deactivates_game(self):
        with patch.dict(native.hook_state, {"game": 123, "ours": {456}}), \
                patch.object(native.user32, "GetForegroundWindow", return_value=0), \
                patch.object(native.user32, "IsWindow", return_value=True), \
                patch.object(native.user32, "SendMessageTimeoutW", return_value=1) as settle, \
                patch.object(native.user32, "SetForegroundWindow", return_value=True) as restore:
            native.restore_game_focus()
            restore.assert_called_once_with(123)
            settle.assert_called_once()

    def test_restore_game_focus_respects_another_active_app(self):
        with patch.dict(native.hook_state, {"game": 123, "ours": {456}}), \
                patch.object(native.user32, "GetForegroundWindow", return_value=789), \
                patch.object(native.user32, "SetForegroundWindow") as restore:
            native.restore_game_focus()
            restore.assert_not_called()
            self.assertEqual(native.hook_state["game"], 789)

    def test_capture_loss_releases_contact(self):
        calls = []
        native._shield["contacts"].add(72)
        try:
            with patch.dict(native._bridge, {"cancel": lambda *args: calls.append(args)}):
                native._shield_wndproc(None, native.WM_POINTERCAPTURECHANGED, 72, 0)
            self.assertEqual(calls, [(72, 0, 0)])
            self.assertNotIn(72, native._shield["contacts"])
            self.assertFalse(native.hook_state["touching"])
        finally:
            native._shield["contacts"].discard(72)


if __name__ == "__main__":
    unittest.main()
