"""Fullscreen touch overlay. Buttons, a movement stick, and a look area sit
above the game and inject keyboard or mouse input without taking focus."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QPointF, QRect, QRectF, QTimer, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from input_sender import (
    HTCLIENT,
    MA_NOACTIVATE,
    SHORT_LABEL,
    VK,
    WM_HOTKEY,
    WM_MOUSEACTIVATE,
    WM_POINTERACTIVATE,
    WM_NCHITTEST,
    InputSender,
    admin_required,
    close_touch_shield,
    hide_touch_shield,
    install_mouse_hook,
    make_visual_passthrough,
    move_touch_shield,
    msg_from_address,
    protect_hwnd,
    raise_topmost,
    remember_foreground,
    register_hotkey,
    relaunch_as_admin,
    release_single_instance,
    set_touch_active,
    set_touch_bridge,
    set_touch_filter,
    set_touch_target,
    shield_hwnd,
    uninstall_mouse_hook,
    unregister_hotkey,
    window_rect,
    _shield_wndproc,
    WM_POINTERDOWN,
    WM_POINTERUPDATE,
    WM_POINTERUP,
    WM_POINTERCAPTURECHANGED,
    WM_TOUCH,
)

ROOT = Path(__file__).resolve().parent
LAYOUT_PATH = ROOT / "layout.json"

HOT_EDIT = 1
HOT_HIDE = 2
HOT_QUIT = 3
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

KEY_ROWS = [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
    ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
    ["A", "S", "D", "F", "G", "H", "J", "K", "L"],
    ["Z", "X", "C", "V", "B", "N", "M"],
    ["SPACE", "SHIFT", "CTRL", "ALT", "TAB", "ESC", "ENTER"],
    ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8"],
    ["F9", "F10", "F11", "F12", "UP", "DOWN", "LEFT", "RIGHT"],
]

FILL = QColor(16, 18, 24, 168)
BORDER = QColor(255, 255, 255, 150)
CYAN = QColor(0, 194, 224)
TEXT = QColor(255, 255, 255, 235)


def _is_windows_msg(event_type) -> bool:
    if event_type == b"windows_generic_MSG":
        return True
    try:
        return bytes(event_type) == b"windows_generic_MSG"
    except Exception:
        return False


def direction_keys(nx: float, ny: float, up: str, down: str, left: str, right: str, dead: float = 0.28) -> set[str]:
    keys: set[str] = set()
    if ny <= -dead:
        keys.add(up)
    elif ny >= dead:
        keys.add(down)
    if nx <= -dead:
        keys.add(left)
    elif nx >= dead:
        keys.add(right)
    return {key for key in keys if key}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _num(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _phase(state) -> str:
    # PySide6 QEventPoint.State: Pressed=1, Updated=2, Stationary=4, Released=8.
    raw = getattr(state, "value", state)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return "ignore"
    if value & 1:
        return "down"
    if value & 8:
        return "up"
    if value & 2:
        return "move"
    return "ignore"


def _synthesized(event) -> bool:
    source = event.source()
    return source in (
        Qt.MouseEventSource.MouseEventSynthesizedBySystem,
        Qt.MouseEventSource.MouseEventSynthesizedByQt,
    )


class Control:
    kind = ""

    def __init__(self, control_id: str, cx: float, cy: float) -> None:
        self.id = control_id
        self.cx = cx
        self.cy = cy

    def can_take(self) -> bool:
        return True

    def on_down(self, host, x: float, y: float) -> bool:
        return True

    def on_move(self, host, x: float, y: float) -> None:
        return None

    def on_up(self, host) -> None:
        return None

    def release(self, host) -> None:
        return None

    def contains(self, x: float, y: float, w: int, h: int) -> bool:
        return False

    def paint(self, painter: QPainter, w: int, h: int, selected: bool, editing: bool) -> None:
        return None

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "cx": self.cx, "cy": self.cy}

    def clamp_pos(self) -> None:
        self.cx = _clamp(self.cx, 0.04, 0.96)
        self.cy = _clamp(self.cy, 0.06, 0.90)


class KeyControl(Control):
    kind = "key"

    def __init__(self, control_id: str, key: str, cx: float, cy: float, radius: float) -> None:
        super().__init__(control_id, cx, cy)
        self.key = key.upper()
        self.radius = radius
        self.fingers = 0

    def on_down(self, host, x: float, y: float) -> bool:
        self.fingers += 1
        if self.fingers == 1:
            host.sender.key_down(self.key)
        return True

    def on_up(self, host) -> None:
        if self.fingers <= 0:
            return
        self.fingers -= 1
        if self.fingers == 0:
            host.sender.key_up(self.key)

    def release(self, host) -> None:
        if self.fingers:
            self.fingers = 0
            host.sender.key_up(self.key)

    def circle(self, w: int, h: int) -> tuple[float, float, float]:
        return self.cx * w, self.cy * h, self.radius * min(w, h)

    def contains(self, x: float, y: float, w: int, h: int) -> bool:
        cx, cy, radius = self.circle(w, h)
        return (x - cx) ** 2 + (y - cy) ** 2 <= (radius + 8) ** 2

    def paint(self, painter: QPainter, w: int, h: int, selected: bool, editing: bool) -> None:
        cx, cy, radius = self.circle(w, h)
        _paint_disc(painter, cx, cy, radius, SHORT_LABEL.get(self.key, self.key), selected, self.fingers > 0)

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update({"key": self.key, "r": self.radius})
        return data


class MouseButtonControl(Control):
    kind = "mouse"

    def __init__(self, control_id: str, button: str, label: str, cx: float, cy: float, radius: float) -> None:
        super().__init__(control_id, cx, cy)
        self.button = button
        self.label = label
        self.radius = radius
        self.fingers = 0

    def on_down(self, host, x: float, y: float) -> bool:
        self.fingers += 1
        if self.fingers == 1:
            host.sender.mouse_down(self.button)
        return True

    def on_up(self, host) -> None:
        if self.fingers <= 0:
            return
        self.fingers -= 1
        if self.fingers == 0:
            host.sender.mouse_up(self.button)

    def release(self, host) -> None:
        if self.fingers:
            self.fingers = 0
            host.sender.mouse_up(self.button)

    def circle(self, w: int, h: int) -> tuple[float, float, float]:
        return self.cx * w, self.cy * h, self.radius * min(w, h)

    def contains(self, x: float, y: float, w: int, h: int) -> bool:
        cx, cy, radius = self.circle(w, h)
        return (x - cx) ** 2 + (y - cy) ** 2 <= (radius + 8) ** 2

    def paint(self, painter: QPainter, w: int, h: int, selected: bool, editing: bool) -> None:
        cx, cy, radius = self.circle(w, h)
        _paint_disc(painter, cx, cy, radius, self.label, selected, self.fingers > 0)

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update({"button": self.button, "label": self.label, "r": self.radius})
        return data


class StickControl(Control):
    kind = "stick"

    def __init__(
        self,
        control_id: str,
        cx: float,
        cy: float,
        radius: float,
        mode: str = "keys",
        up: str = "W",
        down: str = "S",
        left: str = "A",
        right: str = "D",
        invert_y: bool = False,
    ) -> None:
        super().__init__(control_id, cx, cy)
        self.radius = radius
        self.mode = mode
        self.up = up
        self.down = down
        self.left = left
        self.right = right
        self.invert_y = invert_y
        self.tracking = False
        self.raw_nx = 0.0
        self.raw_ny = 0.0
        self.nx = 0.0
        self.ny = 0.0
        self.fx = 0.0
        self.fy = 0.0
        self.held: set[str] = set()

    def can_take(self) -> bool:
        return not self.tracking

    def on_down(self, host, x: float, y: float) -> bool:
        if self.tracking:
            return False
        self.tracking = True
        self._place(host, x, y)
        return True

    def on_move(self, host, x: float, y: float) -> None:
        if self.tracking:
            self._place(host, x, y)

    def on_up(self, host) -> None:
        self.release(host)

    def release(self, host) -> None:
        self.tracking = False
        self.raw_nx = self.raw_ny = self.nx = self.ny = 0.0
        self.fx = self.fy = 0.0
        for key in list(self.held):
            host.sender.key_up(key)
        self.held.clear()

    def circle(self, w: int, h: int) -> tuple[float, float, float]:
        return self.cx * w, self.cy * h, self.radius * min(w, h)

    def contains(self, x: float, y: float, w: int, h: int) -> bool:
        cx, cy, radius = self.circle(w, h)
        return (x - cx) ** 2 + (y - cy) ** 2 <= (radius + 10) ** 2

    def _place(self, host, x: float, y: float) -> None:
        cx, cy, radius = self.circle(host.width(), host.height())
        if radius <= 1:
            return
        nx = (x - cx) / radius
        ny = (y - cy) / radius
        length = math.hypot(nx, ny)
        if length > 1:
            nx /= length
            ny /= length
        self.raw_nx, self.raw_ny = nx, ny
        self.nx, self.ny = _shape(nx), _shape(ny)
        if self.mode == "keys":
            want = direction_keys(self.raw_nx, self.raw_ny, self.up, self.down, self.left, self.right)
            for key in self.held - want:
                host.sender.key_up(key)
            for key in want - self.held:
                host.sender.key_down(key)
            self.held = want

    def tick(self, host) -> None:
        if not self.tracking or self.mode != "mouse":
            return
        speed = 15.0 * host.sensitivity
        self.fx += self.nx * speed
        self.fy += self.ny * speed * (-1 if self.invert_y else 1)
        ix, iy = int(self.fx), int(self.fy)
        self.fx -= ix
        self.fy -= iy
        if ix or iy:
            host.sender.mouse_move(ix, iy)

    def caption(self) -> str:
        if self.mode == "mouse":
            return "Nhìn"
        if self.up == "UP":
            return "Mũi tên"
        return "WASD"

    def paint(self, painter: QPainter, w: int, h: int, selected: bool, editing: bool) -> None:
        cx, cy, radius = self.circle(w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.drawEllipse(QPointF(cx, cy + 3), radius, radius)
        painter.setBrush(QColor(16, 18, 24, 120))
        painter.setPen(QPen(CYAN if selected else BORDER, 3 if selected else 1.7))
        painter.drawEllipse(QPointF(cx, cy), radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 45), 1))
        painter.drawEllipse(QPointF(cx, cy), radius * 0.28, radius * 0.28)
        knob = radius * 0.38
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(236, 244, 255, 220 if self.tracking else 180))
        painter.drawEllipse(QPointF(cx + self.raw_nx * radius * 0.58, cy + self.raw_ny * radius * 0.58), knob, knob)
        painter.setPen(TEXT)
        painter.setFont(QFont("Segoe UI", max(8, int(radius * 0.16))))
        painter.drawText(QRectF(cx - radius, cy + radius + 2, radius * 2, 22), Qt.AlignmentFlag.AlignCenter, self.caption())

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update(
            {
                "r": self.radius,
                "mode": self.mode,
                "up": self.up,
                "down": self.down,
                "left": self.left,
                "right": self.right,
                "invert_y": self.invert_y,
            }
        )
        return data


class ZoneControl(Control):
    kind = "zone"

    def __init__(self, control_id: str, cx: float, cy: float, rw: float, rh: float, invert_y: bool = False) -> None:
        super().__init__(control_id, cx, cy)
        self.rw = rw
        self.rh = rh
        self.invert_y = invert_y
        self.tracking = False
        self.last: tuple[float, float] | None = None
        self.fx = 0.0
        self.fy = 0.0

    def can_take(self) -> bool:
        return not self.tracking

    def rect(self, w: int, h: int) -> QRectF:
        width = self.rw * w
        height = self.rh * h
        return QRectF(self.cx * w - width / 2, self.cy * h - height / 2, width, height)

    def contains(self, x: float, y: float, w: int, h: int) -> bool:
        return self.rect(w, h).adjusted(-6, -6, 6, 6).contains(x, y)

    def on_down(self, host, x: float, y: float) -> bool:
        if self.tracking:
            return False
        self.tracking = True
        self.last = (x, y)
        self.fx = self.fy = 0.0
        return True

    def on_move(self, host, x: float, y: float) -> None:
        if not self.tracking or self.last is None:
            return
        dx, dy = x - self.last[0], y - self.last[1]
        self.last = (x, y)
        self.fx += dx * host.sensitivity
        self.fy += dy * host.sensitivity * (-1 if self.invert_y else 1)
        ix, iy = int(self.fx), int(self.fy)
        self.fx -= ix
        self.fy -= iy
        if ix or iy:
            host.sender.mouse_move(ix, iy)

    def on_up(self, host) -> None:
        self.release(host)

    def release(self, host) -> None:
        self.tracking = False
        self.last = None
        self.fx = self.fy = 0.0

    def paint(self, painter: QPainter, w: int, h: int, selected: bool, editing: bool) -> None:
        rect = self.rect(w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 42 if editing else 16))
        painter.drawRoundedRect(rect, 22, 22)
        pen = QPen(CYAN if selected or self.tracking else QColor(255, 255, 255, 110), 2 if selected else 1.4)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 22, 22)
        if editing:
            painter.setPen(QColor(255, 255, 255, 220))
            painter.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Vuốt để nhìn")

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update({"rw": self.rw, "rh": self.rh, "invert_y": self.invert_y})
        return data


def _shape(value: float, dead: float = 0.22) -> float:
    if abs(value) < dead:
        return 0.0
    sign = 1.0 if value > 0 else -1.0
    return sign * (abs(value) - dead) / (1 - dead)


def _paint_disc(painter: QPainter, cx: float, cy: float, radius: float, label: str, selected: bool, active: bool) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 70))
    painter.drawEllipse(QPointF(cx, cy + 3), radius, radius)
    painter.setBrush(QColor(0, 176, 208, 200) if active else FILL)
    painter.setPen(QPen(CYAN if selected else BORDER, 3 if selected else 1.6))
    painter.drawEllipse(QPointF(cx, cy), radius, radius)
    painter.setPen(TEXT)
    size = radius * (0.42 if len(label) <= 2 else 0.28 if len(label) <= 5 else 0.22)
    font = QFont("Segoe UI", max(8, int(size)))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QRectF(cx - radius, cy - radius, radius * 2, radius * 2), Qt.AlignmentFlag.AlignCenter, label)


def build_default(alloc) -> list[Control]:
    controls: list[Control] = [
        KeyControl(alloc(), "ESC", 0.05, 0.16, 0.042),
        KeyControl(alloc(), "1", 0.05, 0.28, 0.042),
        KeyControl(alloc(), "2", 0.125, 0.28, 0.042),
        KeyControl(alloc(), "SHIFT", 0.05, 0.42, 0.046),
        KeyControl(alloc(), "SPACE", 0.26, 0.46, 0.052),
        KeyControl(alloc(), "CTRL", 0.26, 0.66, 0.046),
        KeyControl(alloc(), "TAB", 0.40, 0.16, 0.042),
        KeyControl(alloc(), "Q", 0.68, 0.36, 0.046),
        KeyControl(alloc(), "F", 0.68, 0.54, 0.046),
        KeyControl(alloc(), "E", 0.78, 0.44, 0.046),
        KeyControl(alloc(), "R", 0.88, 0.32, 0.046),
        MouseButtonControl(alloc(), "right", "Ngắm", 0.78, 0.68, 0.055),
        MouseButtonControl(alloc(), "left", "Bắn", 0.90, 0.56, 0.064),
        StickControl(alloc(), 0.15, 0.64, 0.145, "keys", "W", "S", "A", "D"),
        ZoneControl(alloc(), 0.80, 0.50, 0.36, 0.46),
    ]
    return controls


def control_from_dict(data: dict) -> Control | None:
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    control_id = str(data.get("id") or "")
    cx = _clamp(_num(data.get("cx"), 0.5), 0.04, 0.96)
    cy = _clamp(_num(data.get("cy"), 0.5), 0.06, 0.90)
    if not control_id:
        return None
    if kind == "key" and str(data.get("key", "")).upper() in VK:
        return KeyControl(control_id, str(data["key"]), cx, cy, _clamp(_num(data.get("r"), 0.046), 0.03, 0.22))
    if kind == "mouse" and data.get("button") in ("left", "right", "middle"):
        return MouseButtonControl(
            control_id,
            data["button"],
            str(data.get("label") or data["button"]),
            cx,
            cy,
            _clamp(_num(data.get("r"), 0.055), 0.03, 0.22),
        )
    if kind == "stick":
        mode = data.get("mode") if data.get("mode") in ("keys", "mouse") else "keys"
        return StickControl(
            control_id,
            cx,
            cy,
            _clamp(_num(data.get("r"), 0.14), 0.06, 0.24),
            mode,
            str(data.get("up") or "W").upper(),
            str(data.get("down") or "S").upper(),
            str(data.get("left") or "A").upper(),
            str(data.get("right") or "D").upper(),
            bool(data.get("invert_y")),
        )
    if kind == "zone":
        return ZoneControl(
            control_id,
            cx,
            cy,
            _clamp(_num(data.get("rw"), 0.34), 0.12, 0.7),
            _clamp(_num(data.get("rh"), 0.4), 0.12, 0.75),
            bool(data.get("invert_y")),
        )
    return None


def _rank(control: Control) -> int:
    return {"zone": 0, "stick": 1, "mouse": 2, "key": 3}.get(control.kind, 9)


class RestoreButton(QWidget):
    def __init__(self, overlay: "Overlay") -> None:
        super().__init__()
        self.overlay = overlay
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def place(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        self.setGeometry(geo.x() + (geo.width() - 112) // 2, geo.y() + 10, 112, 36)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        protect_hwnd(int(self.winId()))

    def nativeEvent(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = msg_from_address(message)
            if msg is not None and msg.message in (WM_MOUSEACTIVATE, WM_POINTERACTIVATE):
                return True, MA_NOACTIVATE
        return super().nativeEvent(event_type, message)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not _synthesized(event):
            self.overlay.show_overlay()

    def event(self, event) -> bool:
        if event.type() in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate):
            self.overlay.show_overlay()
            event.accept()
            return True
        return super().event(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(12, 14, 18, 230))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 18, 18)
        painter.setPen(TEXT)
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Hiện")


class ShieldEventFilter(QAbstractNativeEventFilter):
    """Deliver native shield contacts before Qt's pointer translator consumes them."""

    def nativeEventFilter(self, event_type, message):
        msg = msg_from_address(message)
        if msg is not None and msg.message in (WM_POINTERDOWN, WM_POINTERUP, WM_TOUCH):
            logging.getLogger("touchplay.input").debug("Qt native: type=%s target=%s msg=%#x shield=%s", event_type, msg.hWnd, msg.message, shield_hwnd())
        if msg is not None and shield_hwnd() and int(msg.hWnd or 0) == shield_hwnd():
            if msg.message in (WM_POINTERDOWN, WM_POINTERUPDATE, WM_POINTERUP, WM_POINTERCAPTURECHANGED, WM_TOUCH):
                result = _shield_wndproc(msg.hWnd, msg.message, msg.wParam, msg.lParam)
                return True, result
        return False, 0


class Overlay(QWidget):
    def __init__(self, debug: bool = False) -> None:
        super().__init__()
        self.debug = debug
        self.sender = InputSender()
        self.controls: list[Control] = []
        self.pointers: dict = {}
        self.editing = False
        self.hidden = False
        self.selected: str | None = None
        self.popup: dict | None = None
        self.snapshot: dict | None = None
        self.sensitivity = 1.5
        self.opacity = 0.82
        self.block_touch = True
        self._seq = 1
        self._hotkeys = False
        self._look_anchor: tuple[int, int] | None = None
        self.toast_until = time.monotonic() + 8
        self.restore = RestoreButton(self)
        self._native_filter = ShieldEventFilter()
        QApplication.instance().installNativeEventFilter(self._native_filter)
        set_touch_bridge(self._native_down, self._native_move, self._native_up, self._cancel_pointer)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TouchPadAcceptSingleTouchEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setGeometry(self._desktop())
        self.stick_timer = QTimer(self)
        self.stick_timer.timeout.connect(self._tick_sticks)
        self.keep_timer = QTimer(self)
        self.keep_timer.timeout.connect(self._keep)
        self.keep_timer.start(250)
        QTimer.singleShot(8200, self.update)
        self._load()
        self._tray = self._create_tray()

    def alloc(self) -> str:
        control_id = f"c{self._seq}"
        self._seq += 1
        return control_id

    def _note_id(self, control_id: str) -> None:
        if control_id.startswith("c") and control_id[1:].isdigit():
            self._seq = max(self._seq, int(control_id[1:]) + 1)

    def _desktop(self) -> QRect:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return QRect(0, 0, 1280, 720)
        return screen.virtualGeometry()

    def show_overlay(self) -> None:
        remember_foreground()
        self.hidden = False
        self.restore.hide()
        geo = self._desktop()
        self.setGeometry(geo)
        self.show()
        self._stack()
        set_touch_filter(self.block_touch)
        set_touch_target(shield_hwnd(), True)
        self._look_anchor = None
        self.update()

    def _stack(self) -> None:
        """Shield under the drawings. The drawings do not take the finger."""
        if self.hidden:
            return
        move_touch_shield(*window_rect(int(self.winId())))
        make_visual_passthrough(int(self.winId()))

    def hide_overlay(self) -> None:
        self._release_pointers()
        self.sender.release_all()
        self.hidden = True
        self._look_anchor = None
        set_touch_active(False)
        set_touch_filter(False)
        set_touch_target(shield_hwnd(), False)
        hide_touch_shield()
        self.hide()
        self.restore.place()
        self.restore.show()
        raise_topmost(int(self.restore.winId()))

    def toggle_hidden(self) -> None:
        if self.hidden:
            self.show_overlay()
        else:
            self.hide_overlay()

    def shutdown(self) -> None:
        self._release_pointers()
        self.sender.release_all()
        if self._hotkeys:
            hwnd = int(self.winId())
            for hotkey_id in (HOT_EDIT, HOT_HIDE, HOT_QUIT):
                unregister_hotkey(hwnd, hotkey_id)
            self._hotkeys = False
        set_touch_active(False)
        set_touch_target(shield_hwnd(), False)
        uninstall_mouse_hook()
        close_touch_shield()
        QApplication.instance().removeNativeEventFilter(self._native_filter)
        self.restore.close()
        self.close()
        QApplication.quit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        hwnd = int(self.winId())
        make_visual_passthrough(hwnd)
        if not self._hotkeys:
            mods = MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT
            ok_edit = register_hotkey(hwnd, HOT_EDIT, mods, 0x45)
            ok_hide = register_hotkey(hwnd, HOT_HIDE, mods, 0x48)
            ok_quit = register_hotkey(hwnd, HOT_QUIT, mods, 0x51)
            self._hotkeys = True
            if not (ok_edit and ok_hide and ok_quit):
                print("Một phím tắt đã bị ứng dụng khác giữ. Vẫn dùng được nút Sửa / Ẩn trên màn hình.")
        if install_mouse_hook():
            set_touch_filter(self.block_touch and not self.hidden)
        if not self.hidden:
            set_touch_target(shield_hwnd(), True)

    def nativeEvent(self, event_type, message):
        if _is_windows_msg(event_type):
            msg = msg_from_address(message)
            if msg is not None and msg.message in (WM_MOUSEACTIVATE, WM_POINTERACTIVATE):
                return True, MA_NOACTIVATE
            # Drawings are click-through. The shield underneath takes the finger.
            if msg is not None and msg.message == WM_NCHITTEST and not self.hidden:
                return True, HTCLIENT
            if msg is not None and msg.message == WM_HOTKEY:
                self._on_hotkey(int(msg.wParam))
                return True, 0
        return super().nativeEvent(event_type, message)

    def _on_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == HOT_EDIT:
            if self.hidden:
                self.show_overlay()
            if self.editing:
                self.save_edit()
            else:
                self.enter_edit()
        elif hotkey_id == HOT_HIDE:
            self.toggle_hidden()
        elif hotkey_id == HOT_QUIT:
            self.shutdown()

    def enter_edit(self) -> None:
        if self.hidden:
            self.show_overlay()
        if not self.editing:
            self.snapshot = self.to_layout()
            self.editing = True
            self.toast_until = 0
            self._release_pointers()
            self.sender.release_all()
            self.update()

    def cancel_edit(self) -> None:
        self.popup = None
        self.editing = False
        self.selected = None
        if self.snapshot is not None:
            self.apply_layout(self.snapshot)
        self.snapshot = None
        self._release_pointers()
        self.update()

    def save_edit(self) -> None:
        self.popup = None
        self.editing = False
        self.selected = None
        self.snapshot = None
        self._write()
        self._release_pointers()
        self.update()

    def to_layout(self) -> dict:
        return {
            "version": 1,
            "sensitivity": self.sensitivity,
            "opacity": self.opacity,
            "block_touch_mouse": self.block_touch,
            "controls": [control.to_dict() for control in self.controls],
        }

    def apply_layout(self, data: dict) -> None:
        self._release_pointers()
        self.sender.release_all()
        self.sensitivity = _clamp(_num(data.get("sensitivity"), 1.5), 0.3, 4.0)
        self.opacity = _clamp(_num(data.get("opacity"), 0.82), 0.35, 1.0)
        self.block_touch = bool(data.get("block_touch_mouse", True))
        set_touch_filter(self.block_touch and not self.hidden)
        loaded: list[Control] = []
        for item in data.get("controls") or []:
            control = control_from_dict(item)
            if control is None:
                continue
            self._note_id(control.id)
            loaded.append(control)
        self.controls = loaded or build_default(self.alloc)
        for control in self.controls:
            self._note_id(control.id)

    def _load(self) -> None:
        if not LAYOUT_PATH.exists():
            self.controls = build_default(self.alloc)
            return
        try:
            data = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Không đọc được layout.json ({exc}). Dùng mẫu FPS.")
            self.controls = build_default(self.alloc)
            return
        self.apply_layout(data if isinstance(data, dict) else {})

    def _write(self) -> None:
        LAYOUT_PATH.write_text(json.dumps(self.to_layout(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Đã lưu {LAYOUT_PATH}")

    def selected_control(self) -> Control | None:
        for control in self.controls:
            if control.id == self.selected:
                return control
        return None

    def event(self, event) -> bool:
        kind = event.type()
        if kind in (
            QEvent.Type.TouchBegin,
            QEvent.Type.TouchUpdate,
            QEvent.Type.TouchEnd,
            QEvent.Type.TouchCancel,
        ):
            self._on_touch(event)
            if kind == QEvent.Type.TouchCancel:
                self._release_pointers()
            event.accept()
            return True
        return super().event(event)

    def _on_touch(self, event) -> None:
        try:
            points = event.points()
        except Exception as exc:
            if self.debug:
                print("touch error", exc)
            return
        for point in points:
            phase = _phase(point.state())
            pos = point.position()
            if self.debug and phase == "down":
                print("touch", point.id(), phase, pos.x(), pos.y())
            if phase == "down":
                self._down(point.id(), pos.x(), pos.y())
            elif phase == "move":
                self._move(point.id(), pos.x(), pos.y())
            elif phase == "up":
                self._up(point.id(), pos.x(), pos.y())

    def mousePressEvent(self, event) -> None:
        # Touch arrives as a touch event. The mouse message Windows makes from
        # that finger is ignored here so one tap does not press the key twice.
        if event.button() == Qt.MouseButton.LeftButton and not _synthesized(event):
            self._down("mouse", event.position().x(), event.position().y())

    def mouseMoveEvent(self, event) -> None:
        if _synthesized(event) or "mouse" not in self.pointers:
            return
        self._move("mouse", event.position().x(), event.position().y())

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not _synthesized(event):
            self._up("mouse", event.position().x(), event.position().y())

    def wheelEvent(self, event) -> None:
        control = self.selected_control()
        if not self.editing or control is None:
            return
        step = 0.006 if event.angleDelta().y() > 0 else -0.006
        if isinstance(control, ZoneControl):
            control.rw = _clamp(control.rw + step * 4, 0.12, 0.7)
            control.rh = _clamp(control.rh + step * 4, 0.12, 0.75)
        else:
            radius = getattr(control, "radius", None)
            if radius is not None:
                control.radius = _clamp(radius + step, 0.03, 0.22)
        self.update()

    def _down(self, pid, x: float, y: float) -> None:
        logging.getLogger("touchplay.input").debug("overlay down: id=%s xy=%s,%s", pid, x, y)
        if self.debug:
            print("input down", pid, round(x, 1), round(y, 1), flush=True)
        if pid in self.pointers:
            return
        self.toast_until = 0
        width, height = self.width(), self.height()
        if self.popup:
            action, data = self._hit_popup(x, y, width, height)
            self.pointers[pid] = {"kind": "popup", "action": action, "data": data, "moved": False}
            self._sync_touch_flag()
            return
        if self.editing:
            hit = self._hit_edit(x, y, width, height)
        else:
            hit = self._hit_play(x, y, width, height)
        if hit is None:
            return
        logging.getLogger("touchplay.input").debug("hit: kind=%s action=%s control=%s editing=%s size=%sx%s", hit["kind"], hit.get("action"), getattr(hit.get("control"), "id", None), self.editing, width, height)
        self.pointers[pid] = hit
        if hit["kind"] == "control":
            hit["control"].on_down(self, x, y)
            self._sync_stick_timer()
        elif hit["kind"] == "slider":
            self._set_slider(hit["which"], x, hit["rect"])
        elif hit["kind"] == "drag":
            self.selected = hit["id"]
        self._sync_touch_flag()
        self.update()

    def _native_position(self, x: float, y: float) -> tuple[float, float]:
        # WM_POINTER uses physical pixels; QWidget hit-testing uses logical pixels.
        _left, _top, width, height = window_rect(shield_hwnd())
        return x * self.width() / max(1, width), y * self.height() / max(1, height)

    def _native_down(self, pid, x: float, y: float) -> None:
        self._down(pid, *self._native_position(x, y))

    def _native_move(self, pid, x: float, y: float) -> None:
        self._move(pid, *self._native_position(x, y))

    def _native_up(self, pid, x: float, y: float) -> None:
        self._up(pid, *self._native_position(x, y))

    def _cancel_pointer(self, pid, _x=0, _y=0) -> None:
        hit = self.pointers.pop(pid, None)
        if hit and hit.get("kind") == "control":
            hit["control"].on_up(self)
        self._sync_touch_flag()
        self._sync_stick_timer()
        self.update()

    def _move(self, pid, x: float, y: float) -> None:
        hit = self.pointers.get(pid)
        if not hit:
            return
        kind = hit["kind"]
        width, height = max(1, self.width()), max(1, self.height())
        if kind == "control":
            hit["control"].on_move(self, x, y)
            self._sync_stick_timer()
        elif kind == "drag":
            if math.hypot(x - hit["x"], y - hit["y"]) > 7:
                hit["moved"] = True
            control = self._by_id(hit["id"])
            if control is not None:
                control.cx = hit["ox"] + (x - hit["x"]) / width
                control.cy = hit["oy"] + (y - hit["y"]) / height
                control.clamp_pos()
        elif kind == "resize":
            self._resize(hit, x, y, width, height)
        elif kind == "slider":
            self._set_slider(hit["which"], x, hit["rect"])
        elif kind in ("chrome", "chip", "delete"):
            if math.hypot(x - hit["x"], y - hit["y"]) > 12:
                hit["moved"] = True
        self.update()

    def _up(self, pid, x: float, y: float) -> None:
        hit = self.pointers.pop(pid, None)
        logging.getLogger("touchplay.input").debug("overlay up: id=%s xy=%s,%s kind=%s moved=%s", pid, x, y, hit.get("kind") if hit else None, hit.get("moved") if hit else None)
        if not hit:
            return
        kind = hit["kind"]
        if kind == "control":
            hit["control"].on_up(self)
            self._sync_stick_timer()
        elif kind == "popup" and not hit["moved"]:
            if hit["action"] == "choose":
                self._apply_popup(hit["data"])
            elif hit["action"] == "dismiss":
                self.popup = None
        elif kind == "drag" and not hit["moved"] and hit["was_selected"]:
            control = self._by_id(hit["id"])
            if control is not None:
                self._open_editor(control)
        elif kind == "delete" and not hit["moved"]:
            self._delete_selected()
        elif kind == "chrome" and not hit["moved"]:
            self._chrome(hit["action"])
        elif kind == "chip" and not hit["moved"]:
            self._chip(hit["action"])
        self._sync_touch_flag()
        self.update()

    def _release_pointers(self) -> None:
        for pid in list(self.pointers):
            hit = self.pointers.pop(pid)
            if hit.get("kind") == "control":
                hit["control"].on_up(self)
        self._sync_touch_flag()
        self._sync_stick_timer()

    def _sync_touch_flag(self) -> None:
        touching = any(pid != "mouse" for pid in self.pointers)
        set_touch_active(touching)
        if touching:
            self._look_anchor = None

    def _by_id(self, control_id: str) -> Control | None:
        for control in self.controls:
            if control.id == control_id:
                return control
        return None

    def _ordered(self) -> list[Control]:
        return sorted(self.controls, key=_rank)

    def _hit_play(self, x: float, y: float, w: int, h: int):
        edit, hide = self._play_buttons(w, h)
        if edit.contains(x, y):
            return self._press("chrome", "edit", x, y)
        if hide.contains(x, y):
            return self._press("chrome", "hide", x, y)
        for control in reversed(self._ordered()):
            if control.contains(x, y, w, h) and control.can_take():
                return {"kind": "control", "control": control}
        # Only the designated look zone moves the camera; empty space is swallowed.
        return {"kind": "dead"}

    def _hit_edit(self, x: float, y: float, w: int, h: int):
        bar, slots = self._toolbar(w, h)
        if bar.contains(x, y):
            for action, _label, rect in slots:
                if rect.contains(x, y):
                    return self._press("chrome", action, x, y)
            return self._press("chrome", "", x, y)
        for action, _label, rect in self._chip_rects(w, h):
            if rect.contains(x, y):
                return self._press("chip", action, x, y)
        sens, opac = self._sliders(w, h)
        if sens.contains(x, y):
            return {"kind": "slider", "which": "sens", "rect": sens, "x": x, "y": y, "moved": False}
        if opac.contains(x, y):
            return {"kind": "slider", "which": "opac", "rect": opac, "x": x, "y": y, "moved": False}
        control = self.selected_control()
        if control is not None:
            dx, dy = self._delete_point(control, w, h)
            if (x - dx) ** 2 + (y - dy) ** 2 <= 18**2:
                return self._press("delete", control.id, x, y)
            hx, hy = self._resize_point(control, w, h)
            if (x - hx) ** 2 + (y - hy) ** 2 <= 18**2:
                return {"kind": "resize", "id": control.id, "x": x, "y": y, "moved": False}
        for item in reversed(self._ordered()):
            if item.contains(x, y, w, h):
                return {
                    "kind": "drag",
                    "id": item.id,
                    "x": x,
                    "y": y,
                    "ox": item.cx,
                    "oy": item.cy,
                    "moved": False,
                    "was_selected": self.selected == item.id,
                }
        return None

    def _press(self, kind: str, action: str, x: float, y: float) -> dict:
        return {"kind": kind, "action": action, "x": x, "y": y, "moved": False}

    def _chrome(self, action: str) -> None:
        if action == "edit":
            self.enter_edit()
        elif action == "hide":
            self.hide_overlay()
        elif action == "cancel":
            self.cancel_edit()
        elif action == "save":
            self.save_edit()
        elif action == "add_key":
            self.popup = {"kind": "keys", "title": "Chọn phím", "mode": "add_key"}
        elif action == "add_mouse":
            self.popup = {
                "kind": "menu",
                "title": "Thêm chuột",
                "mode": "add_mouse",
                "items": [
                    ("zone", "Vùng nhìn (kéo để xoay)"),
                    ("left", "Nút bắn — click trái"),
                    ("right", "Nút ngắm — click phải"),
                ],
            }
        elif action == "add_stick":
            self.popup = {
                "kind": "menu",
                "title": "Thêm cần",
                "mode": "add_stick",
                "items": [
                    ("keys", "Di chuyển — W A S D"),
                    ("arrows", "Phím mũi tên"),
                    ("mouse", "Nhìn — di chuột"),
                ],
            }

    def _chip(self, action: str) -> None:
        if action == "block":
            self.block_touch = not self.block_touch
            set_touch_filter(self.block_touch and not self.hidden)
        elif action == "invert":
            control = self.selected_control()
            if control is not None and hasattr(control, "invert_y"):
                control.invert_y = not control.invert_y
        elif action == "preset":
            self._release_pointers()
            self.sender.release_all()
            self.controls = build_default(self.alloc)
            self.selected = None

    def _open_editor(self, control: Control) -> None:
        if isinstance(control, KeyControl):
            self.popup = {"kind": "keys", "title": "Đổi phím", "mode": "rebind", "target": control.id}
        elif isinstance(control, MouseButtonControl):
            self.popup = {
                "kind": "menu",
                "title": "Nút chuột",
                "mode": "mouse",
                "target": control.id,
                "items": [("left", "Click trái (bắn)"), ("right", "Click phải (ngắm)"), ("middle", "Click giữa")],
            }
        elif isinstance(control, StickControl):
            self.popup = {
                "kind": "menu",
                "title": "Kiểu cần",
                "mode": "stick",
                "target": control.id,
                "items": [("keys", "Di chuyển (WASD)"), ("arrows", "Phím mũi tên"), ("mouse", "Nhìn (chuột)")],
            }
        elif isinstance(control, ZoneControl):
            control.invert_y = not control.invert_y

    def _apply_popup(self, item_id: str) -> None:
        popup = self.popup
        self.popup = None
        if not popup or item_id in (None, "__close__"):
            return
        mode = popup.get("mode")
        if mode == "add_key" and item_id in VK:
            cx, cy = self._free_spot()
            control = KeyControl(self.alloc(), item_id, cx, cy, 0.046)
            self.controls.append(control)
            self.selected = control.id
        elif mode == "rebind" and item_id in VK:
            control = self._by_id(popup.get("target"))
            if isinstance(control, KeyControl):
                control.release(self)
                control.key = item_id
        elif mode == "add_mouse":
            cx, cy = self._free_spot()
            if item_id == "zone":
                control = ZoneControl(self.alloc(), 0.72, 0.48, 0.32, 0.4)
            elif item_id == "left":
                control = MouseButtonControl(self.alloc(), "left", "Bắn", cx, cy, 0.058)
            elif item_id == "right":
                control = MouseButtonControl(self.alloc(), "right", "Ngắm", cx, cy, 0.052)
            else:
                return
            self.controls.append(control)
            self.selected = control.id
        elif mode == "add_stick":
            cx, cy = self._free_spot()
            if item_id == "arrows":
                control = StickControl(self.alloc(), cx, cy, 0.14, "keys", "UP", "DOWN", "LEFT", "RIGHT")
            elif item_id == "mouse":
                control = StickControl(self.alloc(), cx, cy, 0.14, "mouse")
            else:
                control = StickControl(self.alloc(), cx, cy, 0.14, "keys", "W", "S", "A", "D")
            self.controls.append(control)
            self.selected = control.id
        elif mode == "mouse":
            control = self._by_id(popup.get("target"))
            labels = {"left": "Bắn", "right": "Ngắm", "middle": "Giữa"}
            if isinstance(control, MouseButtonControl) and item_id in labels:
                control.release(self)
                control.button = item_id
                control.label = labels[item_id]
        elif mode == "stick":
            control = self._by_id(popup.get("target"))
            if not isinstance(control, StickControl):
                return
            control.release(self)
            if item_id == "arrows":
                control.mode = "keys"
                control.up, control.down, control.left, control.right = "UP", "DOWN", "LEFT", "RIGHT"
            elif item_id == "mouse":
                control.mode = "mouse"
            else:
                control.mode = "keys"
                control.up, control.down, control.left, control.right = "W", "S", "A", "D"

    def _free_spot(self) -> tuple[float, float]:
        spots = [
            (0.36, 0.32),
            (0.46, 0.32),
            (0.56, 0.32),
            (0.36, 0.46),
            (0.46, 0.46),
            (0.56, 0.46),
            (0.36, 0.60),
            (0.50, 0.60),
        ]
        for cx, cy in spots:
            if all(math.hypot(control.cx - cx, control.cy - cy) > 0.07 for control in self.controls):
                return cx, cy
        return 0.5, 0.5

    def _delete_selected(self) -> None:
        control = self.selected_control()
        if control is None:
            return
        control.release(self)
        self.controls = [item for item in self.controls if item.id != control.id]
        self.selected = None

    def _resize(self, hit: dict, x: float, y: float, w: int, h: int) -> None:
        control = self._by_id(hit["id"])
        if control is None:
            return
        if isinstance(control, ZoneControl):
            control.rw = _clamp(abs(x - control.cx * w) / w * 2, 0.12, 0.7)
            control.rh = _clamp(abs(y - control.cy * h) / h * 2, 0.12, 0.75)
            return
        radius = getattr(control, "radius", None)
        if radius is None:
            return
        cx, cy = control.cx * w, control.cy * h
        control.radius = _clamp(math.hypot(x - cx, y - cy) / min(w, h), 0.03, 0.22)

    def _set_slider(self, which: str, x: float, rect: QRectF) -> None:
        span = max(1.0, rect.width() - 28)
        t = _clamp((x - (rect.left() + 14)) / span, 0.0, 1.0)
        if which == "sens":
            self.sensitivity = 0.3 + t * (4.0 - 0.3)
        else:
            self.opacity = 0.35 + t * (1.0 - 0.35)

    def _hit_popup(self, x: float, y: float, w: int, h: int) -> tuple[str, str | None]:
        cells = self._popup_cells(w, h)
        if cells is None:
            return "dismiss", None
        for item_id, rect in cells["cells"]:
            if rect.contains(x, y):
                return "choose", item_id
        if cells["close"].isValid() and cells["close"].contains(x, y):
            return "choose", "__close__"
        if cells["panel"].contains(x, y):
            return "swallow", None
        return "dismiss", None

    def _popup_cells(self, w: int, h: int) -> dict | None:
        popup = self.popup
        if not popup:
            return None
        pad, gap, title_h = 16, 6, 34
        if popup["kind"] == "keys":
            rows = KEY_ROWS
            cols = max(len(row) for row in rows)
            cell_h = 40 if h >= 760 else 32
            panel_w = min(w - 32, 860)
            cell_w = (panel_w - pad * 2 - gap * (cols - 1)) / cols
            panel_h = pad + title_h + len(rows) * (cell_h + gap) + 52
            panel = QRectF((w - panel_w) / 2, max(12, (h - panel_h) / 2), panel_w, panel_h)
            cells = []
            y = panel.top() + pad + title_h
            for row in rows:
                row_w = len(row) * cell_w + (len(row) - 1) * gap
                x = panel.center().x() - row_w / 2
                for key in row:
                    cells.append((key, QRectF(x, y, cell_w, cell_h)))
                    x += cell_w + gap
                y += cell_h + gap
            close = QRectF(panel.center().x() - 54, panel.bottom() - 42, 108, 30)
            return {"panel": panel, "cells": cells, "close": close}
        items = popup.get("items") or []
        cell_h = 48
        panel_w = min(w - 40, 420)
        panel_h = pad + title_h + len(items) * (cell_h + gap) + 12
        panel = QRectF((w - panel_w) / 2, 96, panel_w, panel_h)
        cells = []
        y = panel.top() + pad + title_h
        for item_id, _label in items:
            cells.append((item_id, QRectF(panel.left() + pad, y, panel_w - pad * 2, cell_h)))
            y += cell_h + gap
        return {"panel": panel, "cells": cells, "close": QRectF()}

    def _play_buttons(self, w: int, h: int) -> tuple[QRectF, QRectF]:
        return QRectF(w / 2 - 98, 14, 90, 36), QRectF(w / 2 + 8, 14, 90, 36)

    def _toolbar(self, w: int, h: int):
        items = [
            ("cancel", "Hủy"),
            ("add_mouse", "Chuột"),
            ("add_key", "Phím"),
            ("add_stick", "Cần"),
            ("save", "Lưu"),
        ]
        tw = min(w - 24, 560)
        th = 76
        bar = QRectF((w - tw) / 2, 14, tw, th)
        gap = 4
        slot = (tw - 16 - gap * (len(items) - 1)) / len(items)
        rects = []
        x = bar.left() + 8
        for action, label in items:
            rects.append((action, label, QRectF(x, bar.top() + 4, slot, th - 8)))
            x += slot + gap
        return bar, rects

    def _sliders(self, w: int, h: int) -> tuple[QRectF, QRectF]:
        width = min(340, (w - 48) / 2)
        y = h - 64
        return QRectF(w / 2 - width - 8, y, width, 40), QRectF(w / 2 + 8, y, width, 40)

    def _chips(self) -> list[tuple[str, str]]:
        chips = [("block", f"Chặn xuyên: {'Bật' if self.block_touch else 'Tắt'}")]
        control = self.selected_control()
        mouse_look = isinstance(control, ZoneControl) or (isinstance(control, StickControl) and control.mode == "mouse")
        if mouse_look:
            chips.append(("invert", f"Đảo Y: {'Bật' if control.invert_y else 'Tắt'}"))
        chips.append(("preset", "Mẫu FPS"))
        return chips

    def _chip_rects(self, w: int, h: int):
        font = QFont("Segoe UI", 10)
        metrics = QFontMetrics(font)
        chips = self._chips()
        gap = 8
        widths = [metrics.horizontalAdvance(label) + 28 for _action, label in chips]
        total = sum(widths) + gap * (len(widths) - 1)
        x = (w - total) / 2
        y = h - 108
        rects = []
        for (action, label), width in zip(chips, widths):
            rects.append((action, label, QRectF(x, y, width, 32)))
            x += width + gap
        return rects

    def _delete_point(self, control: Control, w: int, h: int) -> tuple[float, float]:
        if isinstance(control, ZoneControl):
            rect = control.rect(w, h)
            return rect.right(), rect.top()
        cx, cy, radius = control.circle(w, h)
        return cx + radius * 0.72, cy - radius * 0.72

    def _resize_point(self, control: Control, w: int, h: int) -> tuple[float, float]:
        if isinstance(control, ZoneControl):
            rect = control.rect(w, h)
            return rect.right(), rect.bottom()
        cx, cy, radius = control.circle(w, h)
        return cx + radius, cy

    def _sync_stick_timer(self) -> None:
        need = any(isinstance(control, StickControl) and control.tracking and control.mode == "mouse" for control in self.controls)
        if need and not self.stick_timer.isActive():
            self.stick_timer.start(16)
        elif not need and self.stick_timer.isActive():
            self.stick_timer.stop()

    def _tick_sticks(self) -> None:
        if self.editing or self.hidden:
            return
        for control in self.controls:
            if isinstance(control, StickControl):
                control.tick(self)

    def _keep(self) -> None:
        remember_foreground()
        if self.hidden:
            if self.restore.isVisible():
                raise_topmost(int(self.restore.winId()))
            return
        geo = self._desktop()
        if self.geometry() != geo:
            self.setGeometry(geo)
        self._stack()
        set_touch_target(shield_hwnd(), True)
        if self.toast_until and time.monotonic() > self.toast_until:
            self.toast_until = 0
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        # Alpha must stay above 0. Fully clear pixels are click-through on Windows
        # and the game underneath receives the finger.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 18))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        width, height = self.width(), self.height()
        if width < 10 or height < 10:
            painter.end()
            return
        painter.save()
        painter.setOpacity(self.opacity)
        for control in self._ordered():
            control.paint(painter, width, height, self.editing and control.id == self.selected, self.editing)
        painter.restore()
        if self.editing:
            self._paint_edit(painter, width, height)
        else:
            self._paint_play(painter, width, height)
        if self.popup:
            self._paint_popup(painter, width, height)
        painter.end()

    def _paint_play(self, painter: QPainter, w: int, h: int) -> None:
        edit, hide = self._play_buttons(w, h)
        _pill(painter, edit, "Sửa", TEXT)
        _pill(painter, hide, "Ẩn", TEXT)
        if admin_required():
            warn = QRectF(w / 2 - 220, 58, 440, 32)
            _pill(painter, warn, "Cần chạy TouchPlay bằng Administrator", QColor(255, 150, 140))
        elif self.toast_until and time.monotonic() < self.toast_until:
            rect = QRectF(w / 2 - 280, 58, 560, 52)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(12, 14, 18, 210))
            painter.drawRoundedRect(rect, 14, 14)
            painter.setPen(TEXT)
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignCenter,
                "Chạm nút để chơi  ·  Sửa để sắp layout  ·  Ẩn để dùng desktop\nGame nên ở chế độ cửa sổ không viền",
            )

    def _paint_edit(self, painter: QPainter, w: int, h: int) -> None:
        control = self.selected_control()
        if control is not None:
            dx, dy = self._delete_point(control, w, h)
            _dot(painter, dx, dy, QColor(230, 70, 80), "×")
            hx, hy = self._resize_point(control, w, h)
            _dot(painter, hx, hy, CYAN, None)
        bar, slots = self._toolbar(w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(12, 14, 18, 230))
        painter.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
        for action, label, rect in slots:
            color = QColor(255, 110, 110) if action == "cancel" else QColor(90, 220, 150) if action == "save" else TEXT
            _toolbar_icon(painter, action, rect)
            painter.setPen(color)
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(QRectF(rect.left(), rect.bottom() - 18, rect.width(), 16), Qt.AlignmentFlag.AlignCenter, label)
        hint = QRectF(24, h - 158, w - 48, 36)
        painter.setPen(QColor(255, 255, 255, 200))
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(
            hint,
            Qt.AlignmentFlag.AlignCenter,
            "Kéo nút để di chuyển  ·  chấm xanh đổi cỡ  ·  chạm lần nữa để đổi phím  ·  Ctrl+Shift+H ẩn",
        )
        for _action, label, rect in self._chip_rects(w, h):
            _pill(painter, rect, label, TEXT)
        sens, opac = self._sliders(w, h)
        self._paint_slider(painter, sens, "Độ nhạy", (self.sensitivity - 0.3) / (4.0 - 0.3), f"{self.sensitivity:.1f}")
        self._paint_slider(painter, opac, "Độ đậm", (self.opacity - 0.35) / 0.65, f"{int(self.opacity * 100)}%")

    def _paint_slider(self, painter: QPainter, rect: QRectF, title: str, t: float, value: str) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(12, 14, 18, 210))
        painter.drawRoundedRect(rect, 12, 12)
        track = QRectF(rect.left() + 14, rect.center().y() + 2, rect.width() - 28, 4)
        painter.setBrush(QColor(255, 255, 255, 40))
        painter.drawRoundedRect(track, 2, 2)
        painter.setBrush(CYAN)
        painter.drawRoundedRect(QRectF(track.left(), track.top(), track.width() * _clamp(t, 0, 1), track.height()), 2, 2)
        knob_x = track.left() + track.width() * _clamp(t, 0, 1)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QPointF(knob_x, track.center().y()), 7, 7)
        painter.setPen(TEXT)
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(QRectF(rect.left() + 12, rect.top() + 2, rect.width() - 50, 16), Qt.AlignmentFlag.AlignLeft, title)
        painter.drawText(QRectF(rect.right() - 48, rect.top() + 2, 36, 16), Qt.AlignmentFlag.AlignRight, value)

    def _paint_popup(self, painter: QPainter, w: int, h: int) -> None:
        cells = self._popup_cells(w, h)
        if cells is None:
            return
        painter.fillRect(self.rect(), QColor(0, 0, 0, 90))
        panel = cells["panel"]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(14, 16, 22, 240))
        painter.drawRoundedRect(panel, 18, 18)
        painter.setPen(TEXT)
        painter.setFont(QFont("Segoe UI", 13, QFont.Weight.DemiBold))
        painter.drawText(QRectF(panel.left(), panel.top() + 10, panel.width(), 28), Qt.AlignmentFlag.AlignCenter, self.popup.get("title", ""))
        labels = {key: SHORT_LABEL.get(key, key) for row in KEY_ROWS for key in row}
        for item_id, label in self.popup.get("items") or []:
            labels[item_id] = label
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        for item_id, rect in cells["cells"]:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 22))
            painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 8, 8)
            painter.setPen(TEXT)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, labels.get(item_id, item_id))
        if cells["close"].isValid():
            _pill(painter, cells["close"], "Đóng", TEXT)

    def _create_tray(self) -> QSystemTrayIcon | None:
        try:
            if os.environ.get("TOUCHPLAY_NO_TRAY") or not QSystemTrayIcon.isSystemTrayAvailable():
                return None
            tray = QSystemTrayIcon(self._icon(), self)
            tray.setToolTip("TouchPlay — chơi game bằng cảm ứng")
            menu = QMenu()
            menu.addAction("Sửa layout", self.enter_edit)
            menu.addAction("Ẩn / Hiện", self.toggle_hidden)
            menu.addAction("Chạy lại với quyền Admin", self._relaunch_admin)
            menu.addAction("Thoát", self.shutdown)
            tray.setContextMenu(menu)
            tray.show()
            return tray
        except Exception as exc:
            print("Không tạo được icon khay hệ thống:", exc)
            return None

    def _relaunch_admin(self) -> None:
        release_single_instance()
        relaunch_as_admin()
        QTimer.singleShot(400, self.shutdown)

    def _icon(self) -> QIcon:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(CYAN)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2, 2, 28, 28)
        painter.setPen(QColor(8, 12, 16))
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "T")
        painter.end()
        return QIcon(pixmap)


def _pill(painter: QPainter, rect: QRectF, label: str, color: QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(12, 14, 18, 225))
    painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
    painter.setPen(color)
    painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)


def _dot(painter: QPainter, x: float, y: float, color: QColor, text: str | None) -> None:
    painter.setPen(QPen(QColor(255, 255, 255), 2))
    painter.setBrush(color)
    painter.drawEllipse(QPointF(x, y), 13, 13)
    if text:
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        painter.drawText(QRectF(x - 13, y - 13, 26, 26), Qt.AlignmentFlag.AlignCenter, text)


def _toolbar_icon(painter: QPainter, action: str, rect: QRectF) -> None:
    cx = rect.center().x()
    cy = rect.top() + 20
    if action == "cancel":
        painter.setPen(QPen(QColor(255, 110, 110), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(cx - 6, cy - 6), QPointF(cx + 6, cy + 6))
        painter.drawLine(QPointF(cx + 6, cy - 6), QPointF(cx - 6, cy + 6))
        return
    if action == "save":
        painter.setPen(QPen(QColor(90, 220, 150), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(cx - 7, cy), QPointF(cx - 2, cy + 6))
        painter.drawLine(QPointF(cx - 2, cy + 6), QPointF(cx + 8, cy - 6))
        return
    painter.setPen(QPen(QColor(255, 255, 255), 1.7))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if action == "add_mouse":
        painter.drawEllipse(QPointF(cx, cy + 3), 6, 9)
        painter.drawLine(QPointF(cx, cy - 8), QPointF(cx, cy - 1))
    elif action == "add_key":
        for index in range(3):
            painter.drawRoundedRect(QRectF(cx - 16 + index * 12, cy - 5, 9, 8), 2, 2)
    elif action == "add_stick":
        painter.drawEllipse(QPointF(cx, cy + 2), 10, 10)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QPointF(cx + 3, cy + 4), 3.2, 3.2)
