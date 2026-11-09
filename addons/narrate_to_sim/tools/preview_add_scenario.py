"""Show the actual platform Add Scenario dialog from a private source snapshot."""

from pathlib import Path
import argparse
import importlib.util
import json
import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk


def relocate_workspace(module, root):
    root = Path(root).resolve()
    old_root = module.PROJECT_ROOT
    for name, value in list(vars(module).items()):
        if isinstance(value, Path):
            try:
                replacement = root / value.relative_to(old_root)
            except ValueError:
                replacement = root / "unbundled" / name.lower() / value.name
            setattr(module, name, replacement)
    module.PROJECT_ROOT = root
    module.PY37 = Path(sys.executable)
    module.RPC_PORT = 4316
    module.DEFAULT_CARLA_MAP = "Town04"


def load_workspace(root):
    spec = importlib.util.spec_from_file_location("reference_launcher", root / "reference_launcher.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    relocate_workspace(module, root)
    return module


class PreviewOwner:
    """Only the small owner API required by the unchanged authoring dialog."""

    def __init__(self, module):
        self.module = module
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Narrate-to-Sim | Scenario Library")
        self.root.geometry("800x480")
        module.configure_inkspace_style(self.root)
        self.messagebox = messagebox
        self.group_var = tk.StringVar(value="")
        self.aoi_grid_var = tk.StringVar(value=module.DEFAULT_AOI_GRID)
        self.status_var = tk.StringVar(value="Authoring preview | CARLA playback is not started")
        self.scenarios = []
        self.dialog = None
        panel = ttk.Frame(self.root, padding=16)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        ttk.Label(panel, text="Scenarios", style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.tree = ttk.Treeview(panel, columns=("map", "duration"), selectmode="browse")
        self.tree.heading("#0", text="Name")
        self.tree.heading("map", text="Map")
        self.tree.heading("duration", text="Duration (s)")
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _: self.edit_selected())
        buttons = ttk.Frame(panel)
        buttons.grid(row=2, column=0, sticky="ew", pady=10)
        for label, action in (("Add Scenario", self.open_dialog), ("Edit", self.edit_selected),
                              ("Open Folder", self.open_folder), ("Close", self.root.destroy)):
            ttk.Button(buttons, text=label, command=action).pack(side="left", padx=(0, 8))
        ttk.Label(panel, textvariable=self.status_var, wraplength=730).grid(row=3, column=0, sticky="ew")
        self.refresh_scenarios()

    def selected_map(self):
        return "Town04"

    def tor_manual_keys(self):
        return ["SPACE"]

    def emit(self, message):
        self.status_var.set(message)

    def refresh_scenarios(self, select_key=None):
        self.scenarios = self.module.list_scenarios()
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.scenarios):
            key = str(index)
            self.tree.insert("", "end", iid=key, text=item.name, values=(item.map_name, item.duration_s))
            if self.module.scenario_metadata_key(item.path) == select_key:
                self.tree.selection_set(key)

    def open_folder(self):
        self.module.OUTPUT_SCENARIOS.mkdir(parents=True, exist_ok=True)
        os.startfile(str(self.module.OUTPUT_SCENARIOS))

    def edit_selected(self):
        selection = self.tree.selection()
        if selection:
            self.open_dialog(self.scenarios[int(selection[0])])

    def open_dialog(self, item=None):
        self.dialog = self.module.AddScenarioDialog(self, edit_item=item)
        self.dialog.top.title(("Edit Scenario" if item else "Add Scenario") + " | Isolated Preview")
        self.dialog.top.bind("<Destroy>", self._on_dialog_closed, add="+")
        return self.dialog

    def _on_dialog_closed(self, event):
        if self.dialog and event.widget == self.dialog.top:
            self.root.after_idle(self.root.deiconify)


EXAMPLE = ("scenario duration=12s\n"
           "ego: lane=left, speed=30km/h\n"
           "v1: lane=middle, ahead=4s, speed=30km/h\n"
           "t=3s: v1 -> left, duration=3s\n")


def populate_example(dialog):
    dialog.name_var.set("stock_cut_in_preview")
    dialog._replace_prompt_text(EXAMPLE)
    if not dialog.preview():
        raise RuntimeError("Original Add Scenario interface failed to analyze the example.")


def smoke(owner, root):
    errors = []
    owner.root.report_callback_exception = lambda *exc: errors.append(str(exc[1]))
    dialog = owner.open_dialog()
    populate_example(dialog)
    owner.root.update()
    assert dialog.design_preview is not None
    assert dialog.design_preview._has_data
    assert len(dialog.design_preview.scene_canvas.find_all()) > 5
    tabs = [dialog.tabs.tab(tab, "text") for tab in dialog.tabs.tabs()]
    assert {"Review", "JSON", "Design", "History"}.issubset(set(tabs)), tabs
    report = {"tabs": tabs, "design_canvas_items": len(dialog.design_preview.scene_canvas.find_all()),
              "gui_module": str(Path(owner.module.__file__).resolve())}
    dialog.save()
    owner.root.update()
    assert len(owner.scenarios) == 1
    edited = owner.open_dialog(owner.scenarios[0])
    assert EXAMPLE.strip() == edited.prompt_text.get("1.0", "end").strip()
    edited._replace_prompt_text(EXAMPLE.replace("30km/h", "35km/h"))
    edited.save()
    owner.root.update()
    history = owner.module.get_scenario_prompt_history(owner.scenarios[0].path)
    assert len(history) >= 2
    report["history_entries"] = len(history)
    report["module_origins"] = {name: str(module.__file__) for name, module in list(sys.modules.items())
        if (name.startswith("carla_nl") or name in ("reference_launcher", "simple_hf_scenario_gui"))
        and getattr(module, "__file__", None)}
    report["modules_inside_snapshot"] = all(Path(path).resolve().is_relative_to(root)
                                             for path in report["module_origins"].values())
    assert report["modules_inside_snapshot"]
    assert not errors, errors
    report["passed"] = True
    (root / "preview_smoke_result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    owner.root.destroy()
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--example", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    module = load_workspace(root)
    if args.smoke_test:
        relocate_workspace(module, root / "runtime" / "smoke")
    owner = PreviewOwner(module)
    if args.smoke_test:
        smoke(owner, root)
        return
    dialog = owner.open_dialog()
    if args.example:
        populate_example(dialog)
    owner.root.mainloop()


if __name__ == "__main__":
    main()
