"""Scanner-style prompt snippet completion for CARLA prompt editors."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

import tkinter as tk


@dataclass(frozen=True)
class PromptCompletionItem:
    label: str
    snippet: str
    aliases: tuple[str, ...] = ()


PROMPT_COMPLETIONS: tuple[PromptCompletionItem, ...] = (
    PromptCompletionItem('takeover alert', 'Takeover alert visual and auditory speech at 10s for 4s targeting <vehicle>', ('/takeover', 'takeover alert', 'auditory', 'visual')),
    PromptCompletionItem('collision takeover alert', 'Takeover alert visual and auditory speech 7s before ego hits <obstacle> for 4s', ('/ttc', 'before collision', 'collision alert', 'ttc')),
    PromptCompletionItem('pedestrian crossing', 'ped1: left -> right, 35m, t=5s, duration=4s', ('/pedestrian', '/ped', 'ped1', 'pedestrian')),
    PromptCompletionItem('beep alert', 'beep at=7s duration=4s', ('/beep', 'beep')),
    PromptCompletionItem('takeover speech', 'speech at=7s duration=4s text="Please take over."', ('/speech', 'speech')),
    PromptCompletionItem('target circle', 'target circle at=7s duration=4s target=v1', ('/circle', 'circle')),
    PromptCompletionItem(
        "/ego",
        "ego: lane=left, speed=50mph",
        ("slash ego", "/av", "/subject", "ego", "av", "subject"),
    ),
    PromptCompletionItem(
        "/vehicle",
        "v1: lane=middle, ahead=7s",
        ("slash vehicle", "/car", "vehicle", "car", "front"),
    ),
    PromptCompletionItem(
        "/v1",
        "v1: lane=middle, ahead=7s",
        ("slash vehicle 1", "/vehicle1", "/veh1", "vehicle1", "vehicle 1", "car1", "car 1"),
    ),
    PromptCompletionItem(
        "/v2",
        "v2: lane=right, behind=5s",
        ("slash vehicle 2", "/vehicle2", "/veh2", "vehicle2", "vehicle 2", "car2", "car 2"),
    ),
    PromptCompletionItem(
        "/v3",
        "v3: lane=left, behind=5s",
        ("slash vehicle 3", "/vehicle3", "/veh3", "vehicle3", "vehicle 3", "car3", "car 3"),
    ),
    PromptCompletionItem(
        "/v4",
        "v4: lane=middle, ahead=10s",
        ("slash vehicle 4", "/vehicle4", "/veh4", "vehicle4", "vehicle 4", "car4", "car 4"),
    ),
    PromptCompletionItem(
        "/v5",
        "v5: lane=left, ahead=12s",
        ("slash vehicle 5", "/vehicle5", "/veh5", "vehicle5", "vehicle 5", "car5", "car 5"),
    ),
    PromptCompletionItem(
        "/v6",
        "v6: lane=right, behind=12s",
        ("slash vehicle 6", "/vehicle6", "/veh6", "vehicle6", "vehicle 6", "car6", "car 6"),
    ),
    PromptCompletionItem(
        "/obstacle",
        "obstacle1: barrier, lane=middle, 100m ahead of ego, t=10s, end=30s",
        ("slash obstacle", "/barrier", "/construction", "obstacle", "barrier", "construction"),
    ),
    PromptCompletionItem(
        "/tor",
        "Takeover alert visual and auditory beep at 7s for 4s targeting <vehicle>",
        ("slash tor", "/alert", "/cue", "tor", "alert", "takeover", "cue"),
    ),
    PromptCompletionItem(
        "/duration",
        "scenario duration=45s",
        ("slash duration", "/time", "/total", "/timeline", "duration", "scenario", "time"),
    ),
    PromptCompletionItem(
        "Common defaults",
        "Common:\nscenario duration=45s\nego: speed=50mph\n",
        ("common", "defaults", "global"),
    ),
    PromptCompletionItem(
        "scenario duration",
        "scenario duration=45s",
        ("duration", "total time", "scenario time", "timeline length"),
    ),
    PromptCompletionItem(
        "Scenario block",
        "Scenario 1:\nscenario duration=45s\nego: lane=left, speed=50mph\nv1: lane=middle, ahead=7s\n",
        ("scenario", "scene", "case", "batch"),
    ),
    PromptCompletionItem(
        "ego lane/speed",
        "ego: lane=left, speed=50mph",
        ("ego", "av", "subject", "speed"),
    ),
    PromptCompletionItem(
        "v1 lane + gap",
        "v1: lane=middle, ahead=7s",
        ("v1", "vehicle", "vehicle1", "car", "gap", "front", "ahead", "behind"),
    ),
    PromptCompletionItem(
        "v2 lane + gap",
        "v2: lane=right, behind=5s",
        ("v2", "vehicle2", "vehicle 2", "car2", "car 2"),
    ),
    PromptCompletionItem(
        "v3 lane + gap",
        "v3: lane=left, behind=5s",
        ("v3", "vehicle3", "vehicle 3", "car3", "car 3"),
    ),
    PromptCompletionItem(
        "v4 lane + gap",
        "v4: lane=middle, ahead=10s",
        ("v4", "vehicle4", "vehicle 4", "car4", "car 4"),
    ),
    PromptCompletionItem(
        "v5 lane + gap",
        "v5: lane=left, ahead=12s",
        ("v5", "vehicle5", "vehicle 5", "car5", "car 5"),
    ),
    PromptCompletionItem(
        "v6 lane + gap",
        "v6: lane=right, behind=12s",
        ("v6", "vehicle6", "vehicle 6", "car6", "car 6"),
    ),
    PromptCompletionItem(
        "timed gap target",
        "t=10s: v1 ahead=3s",
        ("time", "timed", "gap", "target", "front", "back"),
    ),
    PromptCompletionItem(
        "lane change",
        "t=5s: v1 -> left, duration=5s",
        ("lane", "change", "merge", "cutin", "cut in", "lc"),
    ),
    PromptCompletionItem(
        "obstacle barrier",
        "obstacle1: barrier, lane=middle, 100m ahead of ego, t=10s, end=30s",
        ("obstacle", "barrier", "construction", "zone", "obs"),
    ),
    PromptCompletionItem(
        "TOR / alert",
        "TOR at 7s with audio chime and visual alert",
        ("tor", "alert", "cue", "chime", "takeover"),
    ),
    PromptCompletionItem(
        "N-key intervention",
        "N-key intervention: valid from LC+1s to LC+9s, ego slows 10mph, return merge after press+10s",
        ("n", "nkey", "key", "intervention", "slow"),
    ),
    PromptCompletionItem(
        "batch variants",
        (
            "Common:\n"
            "scenario duration=45s\n"
            "ego: speed=50mph\n\n"
            "Scenario 1:\n"
            "ego: lane=left\n"
            "v1: lane=middle, ahead=7s\n\n"
            "Scenario 2:\n"
            "scenario duration=60s\n"
            "ego: lane=right\n"
            "v1: lane=left, behind=5s\n"
        ),
        ("batch", "variants", "many", "scenario"),
    ),
    PromptCompletionItem(
        "/timegap",
        "v1 longitudinal_mode=time_gap\nv1 time_gap_points: 5s=6s, 15s=3s",
        ("time gap", "time_gap_points", "headway", "gap profile"),
    ),
    PromptCompletionItem(
        "/speedprofile",
        "v1 longitudinal_mode=speed\nv1 speed_points: 0s=30km/h, 8.5s=8km/h, 10.5s=0km/h",
        ("speed profile", "speed_points", "brake", "decelerate", "slow"),
    ),
    PromptCompletionItem(
        "/model",
        "v1: lane=right, ahead=3s, speed=30km/h, model=truck",
        ("model", "truck", "suv", "vehicle type"),
    ),
    PromptCompletionItem(
        "/blueprint",
        "v1: lane=right, ahead=3s, speed=30km/h, blueprint=vehicle.mercedes.sprinter",
        ("blueprint", "carla blueprint", "bp"),
    ),
    PromptCompletionItem(
        "/drop",
        "t=8s: v1 drops barrel, offset=0.5m",
        ("drop", "dropped obstacle", "barrel"),
    ),
    PromptCompletionItem(
        "/starter",
        (
            "scenario duration=45s\n"
            "ego: lane=left, speed=50mph\n"
            "v1: lane=middle, ahead=7s\n"
            "t=5s: v1 -> left, duration=5s\n"
        ),
        ("starter", "scanner", "example", "template"),
    ),
)


class ScannerStylePromptCompletionController:
    def __init__(
        self,
        prompt_text: tk.Text,
        *,
        parent: tk.Misc | None = None,
        status_callback: Callable[[str], None] | None = None,
        completions: tuple[PromptCompletionItem, ...] = PROMPT_COMPLETIONS,
    ) -> None:
        self.prompt_text = prompt_text
        self.parent = parent or prompt_text.winfo_toplevel()
        self.status_callback = status_callback
        self.completions = completions
        self.popup: tk.Toplevel | None = None
        self.listbox: tk.Listbox | None = None
        self.matches: list[PromptCompletionItem] = []
        self.replace_range: tuple[str, str] = ("insert", "insert")
        self.render_snapshot: tuple[str, tuple[str, ...]] | None = None
        self.bind()

    def bind(self) -> None:
        self.prompt_text.bind("<Control-space>", self.show)
        self.prompt_text.bind("<Control-Key-space>", self.show)
        self.prompt_text.bind("<KeyRelease>", self._on_key_release, add="+")
        self.prompt_text.bind("<Escape>", self.hide, add="+")
        self.prompt_text.bind("<Tab>", self._accept_or_expand, add="+")
        self.prompt_text.bind("<Return>", self._accept_or_newline, add="+")
        self.prompt_text.bind("<Down>", lambda _event: self.move_selection(1), add="+")
        self.prompt_text.bind("<Up>", lambda _event: self.move_selection(-1), add="+")

    def set_status(self, message: str) -> None:
        if self.status_callback is not None:
            try:
                self.status_callback(message)
            except Exception:
                pass

    def current_token(self) -> tuple[str, str, str]:
        insert_index = self.prompt_text.index(tk.INSERT)
        line_start = self.prompt_text.index(f"{insert_index} linestart")
        prefix = self.prompt_text.get(line_start, insert_index)
        match = re.search(r"(/[A-Za-z0-9_-]*|[A-Za-z_][A-Za-z0-9_-]*|[0-9]+)$", prefix)
        if not match:
            return insert_index, insert_index, ""
        token = match.group(1)
        start = f"{insert_index} - {len(token)} chars"
        return start, insert_index, token

    def completion_matches(self, prefix: str) -> list[PromptCompletionItem]:
        token = str(prefix or "").strip().lower()
        if not token:
            return list(self.completions[:10])
        matches: list[PromptCompletionItem] = []
        for item in self.completions:
            values = [item.label, item.snippet, *item.aliases]
            lowered = [value.lower() for value in values]
            if any(value.startswith(token) for value in lowered):
                matches.append(item)
            elif len(token) >= 2 and any(token in value for value in lowered):
                matches.append(item)
        return matches[:10]

    def show(self, event: tk.Event | None = None, *, automatic: bool = False) -> str | None:
        start, end, token = self.current_token()
        if automatic and not token.startswith("/") and len(token) < 2:
            self.hide()
            return None

        matches = self.completion_matches(token)
        if not matches:
            self.hide()
            return "break" if event is not None and not automatic else None

        self.matches = matches
        self.replace_range = (start, end)
        if self.popup is None or not self.popup.winfo_exists():
            popup = tk.Toplevel(self.parent)
            popup.withdraw()
            popup.overrideredirect(True)
            popup.configure(bg="#d0d7e2")
            listbox = tk.Listbox(
                popup,
                height=min(8, len(matches)),
                width=64,
                activestyle="none",
                exportselection=False,
                borderwidth=0,
                highlightthickness=1,
                highlightbackground="#d0d7e2",
                bg="#101827",
                fg="#eef4ff",
                selectbackground="#2b4c7e",
                selectforeground="#ffffff",
                font=("Consolas", 10),
            )
            listbox.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
            listbox.bind("<Double-Button-1>", self.accept)
            listbox.bind("<Return>", self.accept)
            listbox.bind("<Escape>", self.hide)
            self.popup = popup
            self.listbox = listbox

        assert self.popup is not None
        assert self.listbox is not None
        render_key = (token.lower(), tuple(item.label for item in matches))
        if render_key != self.render_snapshot:
            self.listbox.delete(0, tk.END)
            for item in matches:
                self.listbox.insert(tk.END, f"{item.label}   {item.snippet}")
            self.listbox.selection_set(0)
            self.listbox.activate(0)
            self.listbox.configure(height=min(8, len(matches)))
            self.render_snapshot = render_key

        bbox = self.prompt_text.bbox(tk.INSERT)
        if bbox is None:
            self.prompt_text.see(tk.INSERT)
            self.prompt_text.update_idletasks()
            bbox = self.prompt_text.bbox(tk.INSERT)
        if bbox is None:
            return "break" if event is not None and not automatic else None

        x = self.prompt_text.winfo_rootx() + bbox[0]
        y = self.prompt_text.winfo_rooty() + bbox[1] + bbox[3] + 4
        self.popup.geometry(f"+{x}+{y}")
        self.popup.deiconify()
        self.popup.lift(self.parent)
        return "break" if event is not None and not automatic else None

    def hide(self, event: tk.Event | None = None) -> str | None:
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
        self.popup = None
        self.listbox = None
        self.matches = []
        self.render_snapshot = None
        return "break" if event is not None else None

    def move_selection(self, delta: int) -> str | None:
        if self.listbox is None or not self.matches:
            return None
        selected = self.listbox.curselection()
        current = selected[0] if selected else 0
        next_index = max(0, min(len(self.matches) - 1, current + int(delta)))
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(next_index)
        self.listbox.activate(next_index)
        self.listbox.see(next_index)
        return "break"

    def accept(self, event: tk.Event | None = None) -> str:
        if self.listbox is None or not self.matches:
            return "break"
        selected = self.listbox.curselection()
        index = selected[0] if selected else 0
        item = self.matches[max(0, min(index, len(self.matches) - 1))]
        replace_range = self.replace_range
        self.hide()
        self.insert_snippet(item.snippet, replace_range=replace_range, ensure_newline=True)
        self.set_status(f"Inserted prompt snippet: {item.label}")
        return "break"

    def expand_exact_slash_command(self) -> bool:
        start, end, token = self.current_token()
        token = token.strip().lower()
        if not token.startswith("/"):
            return False
        item = next((entry for entry in self.completions if entry.label.lower() == token), None)
        if item is None:
            return False
        self.hide()
        self.insert_snippet(item.snippet, replace_range=(start, end), ensure_newline=True)
        self.set_status(f"Slash command expanded: {item.label}")
        return True

    def insert_snippet(
        self,
        snippet: str,
        *,
        replace_range: tuple[str, str] | None = None,
        ensure_newline: bool = False,
    ) -> None:
        self.prompt_text.focus_set()
        text = str(snippet).rstrip("\n")
        cursor_offset = text.find("{cursor}")
        if cursor_offset >= 0:
            text = text.replace("{cursor}", "", 1)

        if replace_range is not None:
            self.prompt_text.delete(replace_range[0], replace_range[1])
        elif ensure_newline:
            before_on_line = self.prompt_text.get(f"{tk.INSERT} linestart", tk.INSERT)
            if before_on_line.strip():
                self.prompt_text.insert(tk.INSERT, "\n")

        insert_at = self.prompt_text.index(tk.INSERT)
        self.prompt_text.insert(tk.INSERT, text)
        if ensure_newline:
            self.prompt_text.insert(tk.INSERT, "\n")
        if cursor_offset >= 0:
            self.prompt_text.mark_set(tk.INSERT, f"{insert_at} + {cursor_offset} chars")

    def _accept_or_expand(self, event: tk.Event | None = None) -> str | None:
        if self.listbox is not None:
            return self.accept(event)
        if self.expand_exact_slash_command():
            return "break"
        return None

    def _accept_or_newline(self, event: tk.Event | None = None) -> str | None:
        if self.listbox is not None:
            return self.accept(event)
        if self.expand_exact_slash_command():
            return "break"
        return None

    def _on_key_release(self, event: tk.Event | None = None) -> None:
        keysym = getattr(event, "keysym", "")
        if keysym in {"Escape", "Return", "Tab", "Up", "Down", "Left", "Right"}:
            return
        if keysym in {"Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R"}:
            return
        _, _, token = self.current_token()
        if token.startswith("/") or len(token) >= 2:
            self.show(automatic=True)
