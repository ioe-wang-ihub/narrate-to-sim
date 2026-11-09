"""Stock CARLA client hosting the platform's original Add Scenario workspace."""

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.preview_add_scenario import EXAMPLE, PreviewOwner, load_workspace
from carla_nl.stock_addon import convert_for_stock, inspect_server
from carla_nl.stock_prompt_runtime import PlaybackController, validate_port

NEW_EGO = "New vehicle for each run"


def make_dialog_class(module):
    class StockAddScenarioDialog(module.AddScenarioDialog):
        def __init__(self, owner, edit_item=None):
            self.ego_var = tk.StringVar(master=owner.root, value=NEW_EGO)
            self.blueprint_var = tk.StringVar(master=owner.root, value="vehicle.audi.tt")
            if edit_item:
                data = json.loads(Path(edit_item.path).read_text(encoding="utf-8"))
                self.blueprint_var.set(data.get("vehicles", {}).get("ego", {}).get(
                    "blueprint_filter", "vehicle.audi.tt"))
            self.actors = {}
            self.selection_context = None
            super().__init__(owner, edit_item)
            self.top.title("Narrate-to-Sim | " + ("Edit Scenario" if edit_item else "Add Scenario"))
            self.top.protocol("WM_DELETE_WINDOW", self.close)
            if owner.connection:
                self.apply_connection(owner.connection)

        def _build_ui(self):
            # The original Cancel button captures destroy while building the UI.
            self._destroy_dialog = self.top.destroy
            self.top.destroy = self.close
            super()._build_ui()
            bar = ttk.Frame(self.scenario_bar, padding=(0, 10, 0, 0))
            bar.grid(row=1, column=0, columnspan=11, sticky="ew")
            bar.columnconfigure(4, weight=1)
            ttk.Label(bar, text="RPC port").grid(row=0, column=0, sticky="w")
            self.port_entry = ttk.Entry(bar, textvariable=self.owner.port_var, width=7)
            self.port_entry.grid(row=0, column=1, padx=6)
            self.refresh_button = ttk.Button(bar, text="Refresh CARLA", command=self.owner.refresh_connection)
            self.refresh_button.grid(row=0, column=2, padx=(0, 12))
            ttk.Label(bar, text="Ego").grid(row=0, column=3, sticky="w", padx=(0, 6))
            self.ego_combo = ttk.Combobox(bar, textvariable=self.ego_var, values=(NEW_EGO,),
                                          state="readonly", width=42)
            self.ego_combo.grid(row=0, column=4, sticky="ew")
            self.ego_combo.bind("<<ComboboxSelected>>", self.ego_changed)
            ttk.Label(bar, text="Blueprint").grid(row=1, column=0, sticky="w", pady=7)
            self.blueprint_combo = ttk.Combobox(bar, textvariable=self.blueprint_var,
                values=("vehicle.audi.tt", "vehicle.tesla.model3", "vehicle.lincoln.mkz_2020"),
                state="readonly", width=30)
            self.blueprint_combo.grid(row=1, column=1, columnspan=3, sticky="ew", padx=6)
            self.blueprint_combo.bind("<<ComboboxSelected>>", self.ego_changed)
            actions = ttk.Frame(bar)
            actions.grid(row=1, column=4, sticky="e")
            self.run_button = ttk.Button(actions, text="Run in CARLA", command=self.run_scenario,
                                          style="Accent.TButton")
            self.run_button.pack(side="left", padx=6)
            self.stop_button = ttk.Button(actions, text="Stop", command=self.owner.controller.stop,
                                           state="disabled")
            self.stop_button.pack(side="left", padx=6)
            ttk.Button(actions, text="Run logs", command=self.owner.open_run_logs).pack(side="left")
            self.connection_label = ttk.Label(bar, textvariable=self.owner.runtime_status,
                                               style="Muted.TLabel", wraplength=850)
            self.connection_label.grid(row=2, column=0, columnspan=5, sticky="ew")
            bar.bind("<Configure>", lambda event: self.connection_label.configure(
                wraplength=max(300, event.width - 12)))

        def apply_connection(self, data):
            context = (data["port"], data["world_id"])
            if self.selection_context != context and self.ego_var.get() != NEW_EGO:
                self.ego_var.set("")
            self.selection_context = context
            self.actors = {f'#{a["actor_id"]} | {a["blueprint"]} | {a["role_name"] or "(no role)"}': a
                           for a in data["actors"]}
            self.ego_combo.configure(values=(NEW_EGO, *self.actors))
            self.blueprint_combo.configure(values=data["blueprints"])
            if self.ego_var.get() != NEW_EGO and self.ego_var.get() not in self.actors:
                self.ego_var.set("")
            self.ego_changed()

        def ego_changed(self, *_):
            actor = self.actors.get(self.ego_var.get())
            if actor:
                self.blueprint_var.set(actor["blueprint"])
            self.blueprint_combo.configure(state="disabled" if actor else "readonly")
            self._analysis_snapshot = None

        def selected_binding(self):
            data = self.owner.connection
            if not data or data["port"] != validate_port(self.owner.port_var.get()):
                raise ValueError("Refresh CARLA on this RPC port before running.")
            if data["synchronous"]:
                raise ValueError("CARLA is already synchronous. Stop its other controller first, then Refresh.")
            if data["map_name"] != self.map_var.get():
                raise ValueError(f'Connected map is {data["map_name"]}, not {self.map_var.get()}. '
                                 "Select the matching Map. This add-on never reloads your server.")
            if self.map_var.get() != "Town04":
                raise ValueError("This evaluation build validates Town04 highway scenarios only.")
            if self.ego_var.get() == NEW_EGO:
                if self.blueprint_var.get() not in data["blueprints"]:
                    raise ValueError("Select an available four-wheel blueprint after Refresh.")
                return dict(world_id=data["world_id"], actor_id=None)
            if self.ego_var.get() not in self.actors:
                raise ValueError("Select a live Ego from the list after Refresh.")
            return dict(self.actors[self.ego_var.get()])

        def _convert(self):
            preview, converted = super()._convert()
            result = []
            for item in converted:
                scenario = convert_for_stock(item.scenario, self.blueprint_var.get(), self.owner.port_var.get())
                result.append(replace(item, scenario=scenario,
                    summaries=self.adapter._summarize_carla_scenario(scenario)))
            return preview, result

        def run_scenario(self):
            if self.owner.controller.busy or self.owner.connecting:
                return
            try:
                binding = self.selected_binding()
                if not self.preview():
                    return
                if len(self.converted) != 1:
                    raise ValueError("Run one scenario at a time. Save the generated variants separately.")
                if not self.messagebox.askokcancel("Start scenario playback",
                    "This run temporarily controls the selected ego, simulation clock, weather and spectator.\n\n"
                    "Pause other controllers. Borrow only a physics-enabled vehicle with autopilot OFF. "
                    "Its pose, velocity and control will be restored; autopilot cannot be inferred or restored.\n\n"
                    "Other actors remain in the world and continue to evolve. Continue?", parent=self.top):
                    return
                self.owner.controller.start(self.converted[0].scenario, start_server=False,
                    carla_root="", port=int(self.owner.port_var.get()), ego_id=binding["actor_id"], binding=binding)
                self.owner.runtime_status.set("Running | Preparing scenario...")
                self.owner.was_running = True
                self.set_busy(True)
            except Exception as exc:
                self.messagebox.showerror("Cannot run scenario", str(exc), parent=self.top)

        def set_busy(self, busy):
            if getattr(self, "_busy_state", None) == busy:
                return
            self._busy_state = busy
            self.run_button.configure(state="disabled" if busy else "normal")
            self.stop_button.configure(state="normal" if self.owner.controller.busy else "disabled")
            self.refresh_button.configure(state="disabled" if busy else "normal")
            self.port_entry.configure(state="disabled" if busy else "normal")
            self.ego_combo.configure(state="disabled" if busy else "readonly")
            if busy:
                self.blueprint_combo.configure(state="disabled")
            else:
                self.blueprint_combo.configure(state="disabled" if self.ego_var.get() in self.actors else "readonly")

        def save(self):
            if not self.owner.controller.busy:
                super().save()

        def close(self):
            if self.owner.controller.busy:
                self.messagebox.showinfo("Playback active", "Stop and wait for cleanup before closing.", parent=self.top)
                return
            self._destroy_dialog()

    return StockAddScenarioDialog


class AddonOwner(PreviewOwner):
    def __init__(self, module, root, port=2000):
        super().__init__(module)
        self.package_root = Path(root)
        self.queue = queue.Queue()
        self.connection = None
        self.connecting = False
        self.was_running = False
        self.port_var = tk.StringVar(master=self.root, value=str(port))
        self.runtime_status = tk.StringVar(master=self.root, value="CARLA 0.9.16 | Not connected")
        self.controller = PlaybackController(lambda line: self.queue.put(("log", line)), root=root)
        self.dialog_class = make_dialog_class(module)
        self.status_var.set("Narrate-to-Sim | Stock CARLA scenario library")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._replace_close_command(self.root)
        self.root.after(100, self.poll)

    def _replace_close_command(self, widget):
        for child in widget.winfo_children():
            if isinstance(child, ttk.Button) and child.cget("text") == "Close":
                child.configure(command=self.close)
            self._replace_close_command(child)

    def open_dialog(self, item=None):
        if self.dialog and self.dialog.top.winfo_exists():
            self.dialog.top.lift()
            return self.dialog
        self.dialog = self.dialog_class(self, edit_item=item)
        self.dialog.top.bind("<Destroy>", self._on_dialog_closed, add="+")
        self.root.withdraw()
        return self.dialog

    def refresh_connection(self):
        if self.controller.busy or self.connecting:
            return
        try:
            port = validate_port(self.port_var.get())
        except ValueError as exc:
            messagebox.showerror("RPC port", str(exc), parent=self.dialog.top)
            return
        self.connection = None
        self.connecting = True
        self.runtime_status.set("Connecting to 127.0.0.1:" + str(port) + "...")
        self.dialog.set_busy(True)

        def inspect():
            try:
                self.queue.put(("connection", inspect_server(port, self.package_root)))
            except Exception as exc:
                self.queue.put(("connection_error", str(exc)))
        threading.Thread(target=inspect, daemon=True).start()

    def poll(self):
        alive = self.dialog and self.dialog.top.winfo_exists()
        while True:
            try:
                kind, value = self.queue.get_nowait()
            except queue.Empty:
                break
            if kind == "connection":
                self.connection, self.connecting = value, False
                self.runtime_status.set(f'Connected | CARLA 0.9.16 | {value["map_name"]} | '
                                        f'{len(value["actors"])} existing vehicle(s)')
                if alive:
                    self.dialog.apply_connection(value)
            elif kind == "connection_error":
                self.connecting = False
                self.runtime_status.set("Connection failed | " + value)
                if alive:
                    messagebox.showerror("CARLA connection", value + "\nStart stock CARLA and check the RPC port.",
                                         parent=self.dialog.top)
            elif value.startswith("STATUS:"):
                self.runtime_status.set(value.removeprefix("STATUS:").strip())
        if self.was_running and not self.controller.busy:
            self.was_running = False
            result = self.controller.last_result or {"status": "FAILED"}
            self.runtime_status.set(result["status"] + " | " + str(self.controller.run_dir))
            if result["status"] == "FAILED" and alive:
                messagebox.showerror("Playback failed", result.get("error", "See Run logs."), parent=self.dialog.top)
        if alive:
            self.dialog.set_busy(self.controller.busy or self.connecting)
        self.root.after(150, self.poll)

    def open_run_logs(self):
        directory = self.controller.run_dir or self.package_root / "outputs" / "stock_runs"
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(str(directory))

    def close(self):
        if self.controller.busy:
            self.controller.stop()
            self.runtime_status.set("Stopping | Close again after cleanup completes.")
            return
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--example", action="store_true")
    args = parser.parse_args()
    module = load_workspace(ROOT)
    module.xodr_path_for_map = lambda name: ROOT / "runtime" / "maps" / (str(name) + ".xodr")
    owner = AddonOwner(module, ROOT, args.port)
    dialog = owner.open_dialog()
    if args.example:
        dialog.name_var.set("town04_cut_in")
        dialog._replace_prompt_text(EXAMPLE)
        dialog.preview()
    owner.root.mainloop()


if __name__ == "__main__":
    main()
