"""Send keyboard and mouse input to the foreground game, and swallow the
mouse events Windows synthesizes from a touch screen so a touch on a button
does not also shove the game cursor."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger("touchplay.input")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LRESULT = ctypes.c_ssize_t

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001

# Marker Windows puts on mouse messages that were promoted from touch.
MOUSEEVENTF_FROMTOUCH = 0xFF515700
LLMHF_INJECTED = 0x00000001
WH_MOUSE_LL = 14

# Our own injected events carry this so the hook never eats them.
EXTRA_INFO = ULONG_PTR(0x54503531)

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x00000002
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
WM_MOUSEACTIVATE = 0x0021
WM_POINTERACTIVATE = 0x024B
WM_HOTKEY = 0x0312
WM_NCHITTEST = 0x0084
HTCLIENT = 1
HTTRANSPARENT = -1
MA_NOACTIVATE = 3
MA_NOACTIVATEANDEAT = 4

VK: dict[str, int] = {chr(c): c for c in range(ord("A"), ord("Z") + 1)}
VK.update({str(d): 0x30 + d for d in range(10)})
VK.update(
    {
        "SPACE": 0x20,
        "SHIFT": 0x10,
        "CTRL": 0x11,
        "ALT": 0x12,
        "TAB": 0x09,
        "ESC": 0x1B,
        "ENTER": 0x0D,
        "UP": 0x26,
        "DOWN": 0x28,
        "LEFT": 0x25,
        "RIGHT": 0x27,
        "BACK": 0x08,
        "CAPS": 0x14,
    }
)
VK.update({f"F{i}": 0x6F + i for i in range(1, 13)})

EXTENDED_VK = {0x25, 0x26, 0x27, 0x28, 0x21, 0x22, 0x23, 0x24, 0x2D, 0x2E}

MOUSE_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

SHORT_LABEL = {
    "SPACE": "Space",
    "SHIFT": "Shift",
    "CTRL": "Ctrl",
    "ALT": "Alt",
    "ENTER": "Enter",
    "ESC": "Esc",
    "TAB": "Tab",
    "UP": "↑",
    "DOWN": "↓",
    "LEFT": "←",
    "RIGHT": "→",
    "CAPS": "Caps",
    "BACK": "Back",
}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class GESTURECONFIG(ctypes.Structure):
    _fields_ = [
        ("dwID", wintypes.DWORD),
        ("dwWant", wintypes.DWORD),
        ("dwBlock", wintypes.DWORD),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = ctypes.c_uint
user32.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
user32.MapVirtualKeyW.restype = ctypes.c_uint
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

if ctypes.sizeof(ctypes.c_void_p) == 8:
    _GetWindowLongPtrW = user32.GetWindowLongPtrW
    _SetWindowLongPtrW = user32.SetWindowLongPtrW
else:
    _GetWindowLongPtrW = user32.GetWindowLongW
    _SetWindowLongPtrW = user32.SetWindowLongW
_GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_GetWindowLongPtrW.restype = ctypes.c_ssize_t
_SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_SetWindowLongPtrW.restype = ctypes.c_ssize_t

user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.GetMessageExtraInfo.argtypes = []
user32.GetMessageExtraInfo.restype = ULONG_PTR
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetAncestor.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ULONG_PTR)]
user32.SendMessageTimeoutW.restype = LRESULT
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
user32.SetPropW.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.SetGestureConfig.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_uint,
    ctypes.POINTER(GESTURECONFIG),
    ctypes.c_uint,
]
user32.SetGestureConfig.restype = wintypes.BOOL

# Shared with the low-level hook. The hook must not touch Qt.
hook_state: dict = {"filter": True, "proc": None, "hook": None, "ours": set(), "game": 0}
_send_error: dict = {"admin": False, "reported": False}


def input_struct_ok() -> bool:
    pointer = ctypes.sizeof(ctypes.c_void_p)
    if pointer == 8:
        return ctypes.sizeof(INPUT) == 40 and ctypes.sizeof(MOUSEINPUT) == 32
    return ctypes.sizeof(INPUT) == 28


def note_own_window(hwnd: int) -> None:
    if hwnd:
        hook_state["ours"].add(int(hwnd))


def remember_foreground() -> None:
    """Keep the last real window. A tap can focus the overlay; keys must not go there."""
    hwnd = int(user32.GetForegroundWindow() or 0)
    if hwnd and hwnd not in hook_state["ours"]:
        hook_state["game"] = hwnd


def restore_game_focus() -> None:
    """Recover only when our own window took focus; respect Alt+Tab elsewhere."""
    remember_foreground()
    foreground = int(user32.GetForegroundWindow() or 0)
    game = hook_state["game"]
    # A touch on the non-activating shield can leave the desktop with *no*
    # foreground window. SendInput still reports success in that state, but
    # the game never receives the key. Recover that case as well as our HWNDs.
    if (not foreground or foreground in hook_state["ours"]) and game and user32.IsWindow(game):
        restored = user32.SetForegroundWindow(game)
        if restored:
            # Activation of another input queue is asynchronous. Wait for its
            # pending activation before injecting the first key (WM_NULL).
            result = ULONG_PTR()
            user32.SendMessageTimeoutW(game, 0, 0, 0, 0x0002, 100, ctypes.byref(result))
        log.debug("restore game focus: previous=%s game=%s restored=%s current=%s", foreground, game, restored, user32.GetForegroundWindow())


def _send(inp: INPUT) -> bool:
    restore_game_focus()
    kernel32.SetLastError(0)
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if inp.type == INPUT_KEYBOARD:
        log.debug("SendInput key: scan=%#x flags=%#x sent=%s foreground=%s", inp.u.ki.wScan, inp.u.ki.dwFlags, sent, user32.GetForegroundWindow())
    if sent != 1:
        err = kernel32.GetLastError()
        if err in (0, 5):
            _send_error["admin"] = True
        if not _send_error["reported"]:
            _send_error["reported"] = True
            print(f"SendInput failed (error {err}). If the game runs as admin, run TouchPlay as admin too.")
        return False
    _send_error["admin"] = False
    _send_error["reported"] = False
    return True


def admin_required() -> bool:
    return bool(_send_error["admin"])


class InputSender:
    def __init__(self) -> None:
        self._keys: dict[str, int] = {}
        self._buttons: dict[str, int] = {}

    def key_down(self, name: str) -> None:
        name = name.upper()
        if name not in VK:
            return
        count = self._keys.get(name, 0) + 1
        self._keys[name] = count
        if count == 1:
            self._key(name, up=False)

    def key_up(self, name: str) -> None:
        name = name.upper()
        count = self._keys.get(name, 0)
        if count <= 0:
            return
        count -= 1
        self._keys[name] = count
        if count == 0:
            self._key(name, up=True)

    def _key(self, name: str, up: bool) -> None:
        vk = VK[name]
        scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
        flags = KEYEVENTF_SCANCODE
        if up:
            flags |= KEYEVENTF_KEYUP
        if vk in EXTENDED_VK:
            flags |= KEYEVENTF_EXTENDEDKEY
        ki = KEYBDINPUT(0, scan, flags, 0, EXTRA_INFO)
        _send(INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=ki)))

    def mouse_down(self, button: str) -> None:
        pair = MOUSE_BUTTONS.get(button)
        if pair is None:
            return
        count = self._buttons.get(button, 0) + 1
        self._buttons[button] = count
        if count == 1:
            self._mouse(pair[0])

    def mouse_up(self, button: str) -> None:
        pair = MOUSE_BUTTONS.get(button)
        if pair is None:
            return
        count = self._buttons.get(button, 0)
        if count <= 0:
            return
        count -= 1
        self._buttons[button] = count
        if count == 0:
            self._mouse(pair[1])

    def mouse_move(self, dx: int, dy: int) -> None:
        if dx == 0 and dy == 0:
            return
        self._mouse(MOUSEEVENTF_MOVE, dx=dx, dy=dy)

    def _mouse(self, flags: int, dx: int = 0, dy: int = 0) -> None:
        mi = MOUSEINPUT(int(dx), int(dy), 0, flags, 0, EXTRA_INFO)
        _send(INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi)))

    def release_all(self) -> None:
        for name, count in list(self._keys.items()):
            if count:
                self._keys[name] = 0
                self._key(name, up=True)
        for button, count in list(self._buttons.items()):
            if count:
                self._buttons[button] = 0
                self._mouse(MOUSE_BUTTONS[button][1])


class INPUT_MESSAGE_SOURCE(ctypes.Structure):
    _fields_ = [("deviceType", ctypes.c_int), ("originId", ctypes.c_int)]


user32.GetCurrentInputMessageSource.argtypes = [ctypes.POINTER(INPUT_MESSAGE_SOURCE)]
user32.GetCurrentInputMessageSource.restype = wintypes.BOOL


# INPUT_MESSAGE_DEVICE_TYPE is a bit field, unlike POINTER_INPUT_TYPE.
_TOUCH_DEVICES = {4, 8}


def _from_touch(extra: int) -> bool:
    extra &= 0xFFFFFFFF
    return (extra & MOUSEEVENTF_FROMTOUCH) == MOUSEEVENTF_FROMTOUCH


def _over_shield(pt: wintypes.POINT) -> bool:
    shield = int(hook_state.get("shield") or 0)
    if not shield:
        return False
    hwnd = int(user32.WindowFromPoint(pt) or 0)
    if hwnd == shield:
        return True
    if not hwnd:
        return False
    root = int(user32.GetAncestor(wintypes.HWND(hwnd), 2) or 0)
    return root == shield


def _device_is_touch() -> bool:
    source = INPUT_MESSAGE_SOURCE()
    try:
        if user32.GetCurrentInputMessageSource(ctypes.byref(source)):
            return int(source.deviceType) in _TOUCH_DEVICES
    except Exception:
        return False
    return False


def _hook_proc(n_code: int, w_param: int, l_param: int) -> int:
    try:
        if n_code >= 0 and hook_state["filter"]:
            info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not (info.flags & LLMHF_INJECTED):
                extra = int(info.dwExtraInfo) & 0xFFFFFFFF
                try:
                    extra |= int(user32.GetMessageExtraInfo()) & 0xFFFFFFFF
                except Exception:
                    pass
                # A finger must not click the game. The same message aimed at the
                # input shield is allowed through so the on-screen button can see it.
                touch = _from_touch(extra) or _device_is_touch() or bool(hook_state.get("touching"))
                if w_param == 0x0201:
                    log.debug("mouse hook down: touch=%s extra=%#x flags=%#x point=%s,%s over_shield=%s", touch, extra, info.flags, info.pt.x, info.pt.y, _over_shield(info.pt))
                if touch and not _over_shield(info.pt):
                    return 1
    except Exception:
        pass
    return user32.CallNextHookEx(None, n_code, w_param, l_param)


def install_mouse_hook() -> bool:
    if hook_state["hook"]:
        return True
    proc = HOOKPROC(_hook_proc)
    handle = user32.SetWindowsHookExW(WH_MOUSE_LL, proc, None, 0)
    if not handle:
        print(f"Touch-mouse filter not installed (error {kernel32.GetLastError()}).")
        return False
    hook_state["proc"] = proc
    hook_state["hook"] = handle
    return True


def uninstall_mouse_hook() -> None:
    handle = hook_state.get("hook")
    if handle:
        user32.UnhookWindowsHookEx(handle)
    hook_state["hook"] = None
    hook_state["proc"] = None


def set_touch_filter(enabled: bool) -> None:
    hook_state["filter"] = enabled


def set_touch_active(active: bool) -> None:
    """While a finger is down, drop every real mouse message so it cannot reach the game."""
    hook_state["touching"] = active


def apply_input_shield(hwnd: int) -> None:
    """Kept for callers that already have a window. Prefer show_touch_shield."""
    if hwnd:
        hook_state["shield"] = int(hwnd)
        note_own_window(hwnd)


def make_visual_passthrough(hwnd: int) -> None:
    """The drawn buttons stay visible, but the finger falls through to the shield."""
    if not hwnd:
        return
    handle = wintypes.HWND(hwnd)
    style = _GetWindowLongPtrW(handle, GWL_EXSTYLE)
    style |= WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
    _SetWindowLongPtrW(handle, GWL_EXSTYLE, style)
    note_own_window(hwnd)
    raise_topmost(hwnd, frame=True)


# A normal (not layered) window. Layered windows are skipped by touch hit-testing,
# which is why a finger was still clicking the game.
WS_POPUP = 0x80000000
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
SW_SHOWNOACTIVATE = 4
SWP_SHOWWINDOW = 0x0040
WM_POINTERDOWN = 0x0246
WM_POINTERUP = 0x0247
WM_POINTERUPDATE = 0x0245
WM_POINTERCAPTURECHANGED = 0x024C
WM_TOUCH = 0x0240
WM_CANCELMODE = 0x001F
WM_ERASEBKGND = 0x0014
WM_PAINT = 0x000F
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200

_bridge = {"down": None, "move": None, "up": None, "cancel": None}
_shield = {"hwnd": 0, "proc": None, "class": False, "contacts": set()}


class ACCENTPOLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(ACCENTPOLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class POINTER_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerType", wintypes.DWORD),
        ("pointerId", wintypes.DWORD),
        ("frameId", wintypes.DWORD),
        ("pointerFlags", wintypes.DWORD),
        ("sourceDevice", wintypes.HANDLE),
        ("hwndTarget", wintypes.HWND),
        ("ptPixelLocation", wintypes.POINT),
        ("ptHimetricLocation", wintypes.POINT),
        ("ptPixelLocationRaw", wintypes.POINT),
        ("ptHimetricLocationRaw", wintypes.POINT),
        ("dwTime", wintypes.DWORD),
        ("historyCount", wintypes.UINT),
        ("InputData", wintypes.INT),
        ("dwKeyStates", wintypes.DWORD),
        ("PerformanceCount", ctypes.c_uint64),
        ("ButtonChangeType", ctypes.c_int),
    ]


class TOUCHINPUT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG), ("y", wintypes.LONG),
        ("hSource", wintypes.HANDLE), ("dwID", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("dwMask", wintypes.DWORD),
        ("dwTime", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
        ("cxContact", wintypes.DWORD), ("cyContact", wintypes.DWORD),
    ]


def set_touch_bridge(down, move, up, cancel=None) -> None:
    _bridge["down"] = down
    _bridge["move"] = move
    _bridge["up"] = up
    _bridge["cancel"] = cancel


def shield_hwnd() -> int:
    return int(_shield["hwnd"] or 0)


def _accent_clear(hwnd: int) -> None:
    set_attr = getattr(user32, "SetWindowCompositionAttribute", None)
    if set_attr is None:
        return
    accent = ACCENTPOLICY()
    accent.AccentState = 2
    accent.GradientColor = 0x01000000
    data = WINCOMPATTRDATA()
    data.Attribute = 19
    data.SizeOfData = ctypes.sizeof(accent)
    data.Data = ctypes.pointer(accent)
    set_attr.argtypes = [wintypes.HWND, ctypes.POINTER(WINCOMPATTRDATA)]
    set_attr.restype = wintypes.BOOL
    set_attr(wintypes.HWND(hwnd), ctypes.byref(data))


def _client_xy(hwnd: int, screen_x: int, screen_y: int) -> tuple[int, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return int(screen_x) - int(rect.left), int(screen_y) - int(rect.top)


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Physical pixels, for positioning the native shield over the Qt window."""
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise ctypes.WinError()
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def _pointer_xy(hwnd: int, wparam: int) -> tuple[int, int, int]:
    pid = int(wparam) & 0xFFFF
    info = POINTER_INFO()
    if user32.GetPointerInfo(pid, ctypes.byref(info)):
        point = info.ptPixelLocation
        x, y = _client_xy(hwnd, point.x, point.y)
        return pid, x, y
    return pid, 0, 0


def _call_bridge(name: str, pid, x: float, y: float) -> None:
    fn = _bridge.get(name)
    if fn is None:
        return
    try:
        fn(pid, x, y)
    except Exception:
        traceback_print()


def traceback_print() -> None:
    import traceback

    traceback.print_exc()


@ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
def _shield_wndproc(hwnd, msg, wparam, lparam):
    try:
        if msg in (WM_TOUCH, WM_POINTERDOWN, WM_POINTERUP, WM_LBUTTONDOWN, WM_LBUTTONUP):
            log.debug("shield message: hwnd=%s msg=%#x wparam=%#x", hwnd, msg, wparam)
        if msg in (WM_MOUSEACTIVATE, WM_POINTERACTIVATE):
            remember_foreground()
            return MA_NOACTIVATE
        if msg == WM_NCHITTEST:
            return HTCLIENT
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_PAINT:
            user32.ValidateRect(hwnd, None)
            return 0
        if msg == WM_TOUCH:
            count = int(wparam) & 0xFFFF
            contacts = (TOUCHINPUT * count)()
            try:
                if user32.GetTouchInputInfo(wintypes.HANDLE(lparam), count, contacts, ctypes.sizeof(TOUCHINPUT)):
                    for contact in contacts:
                        pid = int(contact.dwID)
                        x, y = _client_xy(int(hwnd), contact.x / 100, contact.y / 100)
                        if contact.dwFlags & 2:  # TOUCHEVENTF_DOWN
                            _shield["contacts"].add(pid)
                            _call_bridge("down", pid, x, y)
                        elif contact.dwFlags & 4:  # TOUCHEVENTF_UP
                            _shield["contacts"].discard(pid)
                            _call_bridge("up", pid, x, y)
                        elif contact.dwFlags & 1 and pid in _shield["contacts"]:
                            _call_bridge("move", pid, x, y)
                    hook_state["touching"] = bool(_shield["contacts"])
            finally:
                user32.CloseTouchInputHandle(wintypes.HANDLE(lparam))
            return 0
        if msg == WM_CANCELMODE:
            for pid in list(_shield["contacts"]):
                _call_bridge("cancel", pid, 0, 0)
            _shield["contacts"].clear()
            hook_state["touching"] = False
            return 0
        if msg == WM_POINTERDOWN:
            hook_state["touching"] = True
            pid, x, y = _pointer_xy(int(hwnd), int(wparam))
            _shield["contacts"].add(pid)
            _call_bridge("down", pid, x, y)
            return 0
        if msg == WM_POINTERUPDATE:
            pid, x, y = _pointer_xy(int(hwnd), int(wparam))
            if pid in _shield["contacts"]:
                _call_bridge("move", pid, x, y)
            return 0
        if msg == WM_POINTERUP:
            pid, x, y = _pointer_xy(int(hwnd), int(wparam))
            _shield["contacts"].discard(pid)
            _call_bridge("up", pid, x, y)
            if not _shield["contacts"]:
                hook_state["touching"] = False
            return 0
        if msg == WM_POINTERCAPTURECHANGED:
            pid = int(wparam) & 0xFFFF
            if pid in _shield["contacts"]:
                _shield["contacts"].discard(pid)
                _call_bridge("cancel", pid, 0, 0)
            hook_state["touching"] = bool(_shield["contacts"])
            return 0
        # SendInput mouse events can return to this window. Never interpret them
        # as another finger/button press (or a feedback loop results).
        if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE):
            extra = int(user32.GetMessageExtraInfo())
            if extra == EXTRA_INFO.value or _from_touch(extra):
                return 0
        if msg == WM_LBUTTONDOWN and not _shield["contacts"]:
            x = ctypes.c_short(int(lparam) & 0xFFFF).value
            y = ctypes.c_short((int(lparam) >> 16) & 0xFFFF).value
            _shield["mouse"] = True
            hook_state["touching"] = True
            _call_bridge("down", "mouse", x, y)
            return 0
        if msg == WM_MOUSEMOVE and _shield.get("mouse") and not _shield["contacts"]:
            x = ctypes.c_short(int(lparam) & 0xFFFF).value
            y = ctypes.c_short((int(lparam) >> 16) & 0xFFFF).value
            _call_bridge("move", "mouse", x, y)
            return 0
        if msg == WM_LBUTTONUP and _shield.get("mouse") and not _shield["contacts"]:
            x = ctypes.c_short(int(lparam) & 0xFFFF).value
            y = ctypes.c_short((int(lparam) >> 16) & 0xFFFF).value
            _shield["mouse"] = False
            _call_bridge("up", "mouse", x, y)
            hook_state["touching"] = False
            return 0
        if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE):
            return 0
    except Exception:
        traceback_print()
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _ensure_shield_class() -> bool:
    if _shield["class"]:
        return True
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
    user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.LoadCursorW.restype = wintypes.HANDLE
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    cls = WNDCLASSW()
    cls.lpfnWndProc = ctypes.cast(_shield_wndproc, ctypes.c_void_p)
    cls.hInstance = kernel32.GetModuleHandleW(None)
    cls.hCursor = user32.LoadCursorW(None, 32512)
    cls.lpszClassName = "TouchPlayShield"
    _shield["proc"] = _shield_wndproc
    atom = user32.RegisterClassW(ctypes.byref(cls))
    if not atom and kernel32.GetLastError() not in (0, 1410):
        print(f"Touch shield class failed (error {kernel32.GetLastError()}).")
        return False
    _shield["class"] = True
    return True


def _bind_shield_api() -> None:
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.ValidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.ValidateRect.restype = wintypes.BOOL
    user32.GetPointerInfo.argtypes = [wintypes.UINT, ctypes.POINTER(POINTER_INFO)]
    user32.GetPointerInfo.restype = wintypes.BOOL
    user32.RegisterTouchWindow.argtypes = [wintypes.HWND, wintypes.ULONG]
    user32.RegisterTouchWindow.restype = wintypes.BOOL
    user32.GetTouchInputInfo.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.POINTER(TOUCHINPUT), ctypes.c_int]
    user32.GetTouchInputInfo.restype = wintypes.BOOL
    user32.CloseTouchInputHandle.argtypes = [wintypes.HANDLE]
    user32.CloseTouchInputHandle.restype = wintypes.BOOL


def show_touch_shield(x: int, y: int, width: int, height: int) -> int:
    """Invisible normal window that receives the finger instead of the game."""
    if width < 1 or height < 1:
        return 0
    _bind_shield_api()
    if not _ensure_shield_class():
        return 0
    hwnd = int(_shield["hwnd"] or 0)
    if hwnd and not user32.IsWindow(wintypes.HWND(hwnd)):
        hwnd = 0
        _shield["hwnd"] = 0
    if not hwnd:
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        created = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOREDIRECTIONBITMAP | WS_EX_NOACTIVATE,
            "TouchPlayShield",
            "TouchPlay",
            WS_POPUP,
            int(x),
            int(y),
            int(width),
            int(height),
            None,
            None,
            kernel32.GetModuleHandleW(None),
            None,
        )
        hwnd = int(created or 0)
        if not hwnd:
            print(f"Touch shield failed (error {kernel32.GetLastError()}).")
            return 0
        _shield["hwnd"] = hwnd
        # This is a native window, not a Qt widget. Explicitly opt into raw
        # multitouch instead of relying on implicit WM_POINTER compatibility.
        if not user32.RegisterTouchWindow(wintypes.HWND(hwnd), 1):
            print(f"Touch registration failed (error {kernel32.GetLastError()}).")
    hook_state["shield"] = hwnd
    note_own_window(hwnd)
    user32.SetWindowPos(
        wintypes.HWND(hwnd),
        wintypes.HWND(HWND_TOPMOST),
        int(x),
        int(y),
        int(width),
        int(height),
        SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )
    _accent_clear(hwnd)
    user32.ShowWindow(wintypes.HWND(hwnd), SW_SHOWNOACTIVATE)
    log.debug("shield shown: hwnd=%s rect=%s", hwnd, (x, y, width, height))
    return hwnd


def move_touch_shield(x: int, y: int, width: int, height: int) -> None:
    hwnd = shield_hwnd()
    if hwnd and not user32.IsWindowVisible(wintypes.HWND(hwnd)):
        # Reusing a hidden accent window can restore an opaque DWM surface.
        # Recreate it so transparency and touch registration start together,
        # exactly as on the first launch.
        close_touch_shield()
        hwnd = 0
    if not hwnd:
        show_touch_shield(x, y, width, height)
        return
    user32.SetWindowPos(
        wintypes.HWND(hwnd),
        wintypes.HWND(HWND_TOPMOST),
        int(x),
        int(y),
        int(width),
        int(height),
        SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )


def hide_touch_shield() -> None:
    hwnd = shield_hwnd()
    _shield["contacts"].clear()
    _shield["mouse"] = False
    hook_state["touching"] = False
    if hwnd:
        user32.ShowWindow(wintypes.HWND(hwnd), 0)


def close_touch_shield() -> None:
    hwnd = shield_hwnd()
    _shield["contacts"].clear()
    _shield["mouse"] = False
    hook_state["touching"] = False
    hook_state["shield"] = 0
    if hwnd:
        user32.DestroyWindow(wintypes.HWND(hwnd))
        hook_state["ours"].discard(hwnd)
    _shield["hwnd"] = 0


def protect_hwnd(hwnd: int) -> None:
    """Keep the overlay from stealing focus, and disable touch press-and-hold."""
    if not hwnd:
        return
    handle = wintypes.HWND(hwnd)
    style = _GetWindowLongPtrW(handle, GWL_EXSTYLE)
    style &= ~WS_EX_TRANSPARENT
    style |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
    _SetWindowLongPtrW(handle, GWL_EXSTYLE, style)
    note_own_window(hwnd)
    # Do not call RegisterTouchWindow. It replaces WM_POINTER, which is the
    # path Qt uses to deliver a finger to a button.
    tablet = 0x00000001 | 0x00000008 | 0x00000010 | 0x00010000
    user32.SetPropW(handle, "MicrosoftTabletPenServiceProperty", wintypes.HANDLE(tablet))
    cfg = GESTURECONFIG(0, 0, 0x00000001)
    user32.SetGestureConfig(handle, 0, 1, ctypes.byref(cfg), ctypes.sizeof(cfg))
    raise_topmost(hwnd, frame=True)


def raise_topmost(hwnd: int, frame: bool = False) -> None:
    if not hwnd:
        return
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    if frame:
        flags |= SWP_FRAMECHANGED
    user32.SetWindowPos(
        wintypes.HWND(hwnd),
        wintypes.HWND(HWND_TOPMOST),
        0,
        0,
        0,
        0,
        flags,
    )


_touch_target = {"on": False}


def set_touch_target(hwnd: int, enable: bool) -> None:
    """Redirect system touch to this window so the game underneath cannot see the finger."""
    if not hwnd or _touch_target["on"] == enable:
        return
    name = "RegisterPointerInputTarget" if enable else "UnregisterPointerInputTarget"
    fn = getattr(user32, name, None)
    if fn is None:
        return
    fn.argtypes = [wintypes.HWND, ctypes.c_uint]
    fn.restype = wintypes.BOOL
    # PT_TOUCH = 2. UIAccess is required on some builds; failure is non-fatal.
    if bool(fn(wintypes.HWND(hwnd), 2)) or not enable:
        _touch_target["on"] = enable


def cursor_pos() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return int(point.x), int(point.y)


def move_cursor(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def register_hotkey(hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> bool:
    return bool(user32.RegisterHotKey(wintypes.HWND(hwnd), hotkey_id, modifiers, vk))


def unregister_hotkey(hwnd: int, hotkey_id: int) -> None:
    user32.UnregisterHotKey(wintypes.HWND(hwnd), hotkey_id)


_mutex = None


def acquire_single_instance() -> bool:
    """Return False when another TouchPlay window is already running."""
    global _mutex
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.SetLastError(0)
    _mutex = kernel32.CreateMutexW(None, False, "Local\\TouchPlayOverlay")
    if not _mutex:
        raise ctypes.WinError()
    return kernel32.GetLastError() != 183


def release_single_instance() -> None:
    global _mutex
    if _mutex:
        kernel32.CloseHandle(_mutex)
        _mutex = None


def relaunch_as_admin() -> None:
    import sys

    params = subprocess_list(sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)


def subprocess_list(args: list[str]) -> str:
    quoted = []
    for arg in args:
        if not arg or any(ch in arg for ch in ' \t"'):
            quoted.append('"' + arg.replace('"', '\\"') + '"')
        else:
            quoted.append(arg)
    return " ".join(quoted)


def msg_from_address(message) -> wintypes.MSG | None:
    try:
        address = message if isinstance(message, int) else int(message)
        return wintypes.MSG.from_address(address)
    except (TypeError, ValueError, OverflowError):
        return None
