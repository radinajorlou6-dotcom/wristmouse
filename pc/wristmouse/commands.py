"""Spoken commands. If the whole utterance matches one of these, it runs instead of being typed.
Say "type <words>" to force typing words that would otherwise be a command (e.g. "type copy")."""
import re

COMMANDS = {
    # keys
    "enter": ("key", "enter"), "press enter": ("key", "enter"), "new line": ("key", "enter"),
    "next line": ("key", "enter"),
    "backspace": ("key", "backspace"), "back space": ("key", "backspace"),
    "delete": ("key", "delete"),
    "tab": ("key", "tab"), "escape": ("key", "esc"), "space": ("key", "space"),
    "up": ("key", "up"), "down": ("key", "down"), "left": ("key", "left"), "right": ("key", "right"),
    "page up": ("key", "pageup"), "page down": ("key", "pagedown"),
    "go to top": ("key", "ctrl", "home"), "go to bottom": ("key", "ctrl", "end"),
    "refresh": ("key", "f5"), "reload": ("key", "f5"),
    # editing
    "select all": ("key", "ctrl", "a"), "copy": ("key", "ctrl", "c"), "copy that": ("key", "ctrl", "c"),
    "paste": ("key", "ctrl", "v"), "paste that": ("key", "ctrl", "v"), "cut": ("key", "ctrl", "x"),
    "cut that": ("key", "ctrl", "x"), "undo": ("key", "ctrl", "z"), "redo": ("key", "ctrl", "y"),
    "save": ("key", "ctrl", "s"), "find": ("key", "ctrl", "f"),
    "delete that": ("delete_last",), "scratch that": ("delete_last",), "undo that": ("delete_last",),
    "delete word": ("key", "ctrl", "backspace"),
    # browser / windows
    "new tab": ("key", "ctrl", "t"), "close tab": ("key", "ctrl", "w"), "reopen tab": ("key", "ctrl", "shift", "t"),
    "next tab": ("key", "ctrl", "tab"), "previous tab": ("key", "ctrl", "shift", "tab"),
    "go back": ("key", "alt", "left"), "go forward": ("key", "alt", "right"),
    "switch window": ("key", "alt", "tab"), "close window": ("key", "alt", "f4"),
    "minimize": ("key", "win", "down"), "maximize": ("key", "win", "up"),
    "show desktop": ("key", "win", "d"), "start menu": ("key", "win"),
    # mouse
    "click": ("click", "left"), "left click": ("click", "left"), "right click": ("click", "right"),
    "double click": ("click", "left", 2),
    "grab": ("drag", True), "hold": ("drag", True), "pick up": ("drag", True), "drag": ("drag", True),
    "drop": ("drag", False), "let go": ("drag", False), "release": ("drag", False),
    "drop it": ("drag", False),
    "scroll up": ("scroll", 5), "scroll down": ("scroll", -5),
    "table mode": ("mode", "table"), "desk mode": ("mode", "table"), "mouse mode": ("mode", "table"),
    "air mode": ("mode", "air"), "air mouse": ("mode", "air"), "pointer mode": ("mode", "air"),
    "pause mouse": ("mouse", False), "stop mouse": ("mouse", False),
    "start mouse": ("mouse", True), "resume mouse": ("mouse", True),
    # cancel
    "cancel": ("noop",), "never mind": ("noop",), "nevermind": ("noop",),
}


def normalize(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[.,!?;:\"]", "", t)
    t = t.replace("-", " ")
    return re.sub(r"\s+", " ", t).strip()


def parse(text: str):
    """Returns ("type", text) or a command tuple, or None for empty input."""
    raw = text.strip()
    if not raw:
        return None
    n = normalize(raw)
    if not n:
        return None
    if n.startswith("type "):
        idx = raw.lower().find("type") + 4
        rest = raw[idx:].lstrip(" ,:")
        return ("type", rest)
    if n in COMMANDS:
        return COMMANDS[n]
    return ("type", raw)

