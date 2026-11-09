"""Source distribution and stock-client contracts; no running CARLA required."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from carla_nl import stock_addon


class SourceDistributionTests(unittest.TestCase):
    def test_imports_are_local_to_addon(self):
        self.assertTrue(Path(stock_addon.__file__).resolve().is_relative_to(ROOT))

    def test_manifest_hashes_and_complete_source_inventory(self):
        from tools.update_source_manifest import source_files

        manifest = json.loads((ROOT / "source_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["files"]), set(source_files(ROOT)))
        for name, digest in manifest["files"].items():
            with self.subTest(path=name):
                path = (ROOT / name).resolve()
                self.assertTrue(path.is_relative_to(ROOT))
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_inventory_excludes_runtime_binaries_and_generated_data(self):
        from tools.update_source_manifest import source_files

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("README.md", "tools/check.py", "tests/test_check.py",
                         "runtime/results.json", "outputs/scenario.json",
                         ".venv/library.py", "tools/__pycache__/compiled.pyc",
                         "tools/private.env", "tools/download.exe"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("example", encoding="utf-8")
            self.assertEqual(set(source_files(root)),
                             {"README.md", "tools/check.py", "tests/test_check.py"})

    def test_required_authoring_inputs_and_license_are_present(self):
        parser_root = ROOT / "scanner_prompt_gui_to_carla_0913_source_20260524"
        self.assertTrue((parser_root / "SCANER_tool/simple_hf_scenario_gui.py").is_file())
        self.assertTrue((parser_root / "references/scaner_scenarios/ERTR_LC_Keep_CR.sce").is_file())
        self.assertIn("GPL-3.0-or-later", (ROOT / "License.txt").read_text(encoding="utf-8"))
        self.assertTrue((ROOT / "Notices.txt").is_file())


class StockContractTests(unittest.TestCase):
    def test_ego_selection_overrides_legacy_default_without_mutation(self):
        source = {"vehicles": {"ego": {"blueprint_filter": "vehicle.lincoln.mkz_2020"}},
                  "dreyevr": True, "experiment_events": {"standard_markers_enabled": True}}
        original = copy.deepcopy(source)
        result = stock_addon.convert_for_stock(source, "vehicle.audi.tt", 4336)
        self.assertEqual(result["vehicles"]["ego"]["blueprint_filter"], "vehicle.audi.tt")
        self.assertIs(result["dreyevr"], False)
        self.assertEqual(result["experiment_events"], {})
        self.assertEqual(source, original)
        self.assertNotIn("ego_actor_id", result)

    def test_experiment_features_are_rejected(self):
        for key in ("takeover_requests", "tor_alerts", "audio_events", "audio_cues", "standalone_cues"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "not supported"):
                stock_addon.convert_for_stock({key: [{"start": 3}]}, "vehicle.audi.tt", 4336)
        with self.assertRaisesRegex(ValueError, "not supported"):
            stock_addon.convert_for_stock({"experiment_events": {"tor_windows": [3]}},
                                          "vehicle.audi.tt", 4336)

    def test_binding_does_not_require_hero_role(self):
        actor = SimpleNamespace(id=42, type_id="vehicle.audi.tt", attributes={"role_name": "my_lab"})
        world = SimpleNamespace(id=7, get_actor=lambda _: actor)
        binding = dict(world_id=7, actor_id=42, blueprint="vehicle.audi.tt", role_name="my_lab")
        self.assertIs(stock_addon.validate_binding(world, binding), actor)

    def test_stale_binding_is_rejected(self):
        for change in ("world", "missing", "model", "role"):
            actor = SimpleNamespace(id=42, type_id="vehicle.audi.tt", attributes={"role_name": "my_lab"})
            world = SimpleNamespace(id=7, get_actor=lambda _: actor)
            binding = dict(world_id=7, actor_id=42, blueprint="vehicle.audi.tt", role_name="my_lab")
            if change == "world":
                world.id = 8
            elif change == "missing":
                world.get_actor = lambda _: None
            elif change == "model":
                actor.type_id = "vehicle.tesla.model3"
            else:
                actor.attributes["role_name"] = "hero"
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "Refresh"):
                stock_addon.validate_binding(world, binding)

    def test_spawn_binding_detects_world_reload(self):
        with self.assertRaisesRegex(ValueError, "Refresh"):
            stock_addon.validate_binding(SimpleNamespace(id=8), dict(world_id=7, actor_id=None))

    def test_version_mismatch_is_rejected(self):
        stock_addon.validate_versions("0.9.16", "0.9.16")
        for versions in (("0.9.15", "0.9.15"), ("0.9.16", "0.9.15")):
            with self.subTest(versions=versions), self.assertRaisesRegex(ValueError, "0.9.16"):
                stock_addon.validate_versions(*versions)

    def test_invalid_blueprint_and_port_are_rejected(self):
        for blueprint in ("vehicle.*", "not_a_vehicle"):
            with self.assertRaisesRegex(ValueError, "blueprint"):
                stock_addon.convert_for_stock({}, blueprint, 4336)
        for port in (1, 65535):
            with self.assertRaisesRegex(ValueError, "port"):
                stock_addon.convert_for_stock({}, "vehicle.audi.tt", port)

    def test_gui_inherits_original_editor(self):
        from tools.stock_add_scenario import make_dialog_class

        class Original:
            def _build_start_wizard(self):
                pass

        dialog = make_dialog_class(SimpleNamespace(AddScenarioDialog=Original))
        self.assertTrue(issubclass(dialog, Original))
        self.assertIs(dialog._build_start_wizard, Original._build_start_wizard)


if __name__ == "__main__":
    unittest.main()
