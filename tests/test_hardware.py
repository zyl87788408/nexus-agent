"""Hardware detection and adaptation on constrained machines."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus.hardware.detector import detect_hardware
from nexus.hardware.optimizer import MIN_BUDGET_MB, model_budget_mb, optimize


FOUR_GB = {"ram_mb": 3915, "ram_available_mb": 2419, "cpu_cores": 2,
           "vram_mb": None, "disk_free_gb": 6.39, "gpu_count": 0}


class DetectorTest(unittest.TestCase):
    def test_reports_required_fields(self):
        hw = detect_hardware()
        for field in ("os", "architecture", "cpu", "cpu_cores", "ram_mb",
                      "disk_free_gb", "gpu", "vram_mb"):
            self.assertIn(field, hw)

    def test_values_are_sane(self):
        hw = detect_hardware()
        self.assertGreater(hw["ram_mb"], 0)
        self.assertGreaterEqual(hw["cpu_cores"], 1)
        self.assertGreaterEqual(hw["disk_free_gb"], 0)

    def test_gpu_absent_is_reported_as_none_not_faked(self):
        """A machine without a GPU must say None, never invent capability."""
        hw = detect_hardware()
        if hw["gpu"] is None:
            self.assertIsNone(hw["vram_mb"])
            self.assertEqual(hw["gpu_count"], 0)
        else:
            self.assertGreater(hw["vram_mb"] or 0, 0)


class BudgetTest(unittest.TestCase):
    def test_budget_never_exceeds_physical_memory(self):
        for hw in ({"ram_mb": 512, "ram_available_mb": 100, "cpu_cores": 1},
                   FOUR_GB,
                   {"ram_mb": 16384, "ram_available_mb": 16384, "cpu_cores": 16}):
            self.assertLessEqual(model_budget_mb(hw), hw["ram_mb"])

    def test_budget_has_a_floor(self):
        self.assertGreaterEqual(
            model_budget_mb({"ram_mb": 256, "ram_available_mb": 64}), MIN_BUDGET_MB
        )

    def test_4gb_box_gets_a_usable_budget(self):
        """The old flat 30% (1174 MB) could never host any real model."""
        budget = model_budget_mb(FOUR_GB)
        self.assertGreater(budget, 1174)
        self.assertLessEqual(budget, FOUR_GB["ram_mb"] - 900 + MIN_BUDGET_MB)


class OptimizeTest(unittest.TestCase):
    def test_4gb_profile_is_lite_and_single_threaded(self):
        plan = optimize(FOUR_GB)
        self.assertEqual(plan["mode"], "Lite")
        self.assertEqual(plan["max_parallel_agents"], 1)
        self.assertEqual(plan["parallel_requests"], 1)
        self.assertTrue(plan["low_resource"])
        self.assertGreater(plan["context_tokens"], 512)

    def test_context_scales_with_memory(self):
        small = optimize({"ram_mb": 2048, "ram_available_mb": 1500,
                          "cpu_cores": 2, "vram_mb": None})
        large = optimize({"ram_mb": 32768, "ram_available_mb": 30000,
                          "cpu_cores": 16, "vram_mb": None})
        self.assertLess(small["context_tokens"], large["context_tokens"])

    def test_bigger_boxes_allow_more_agents(self):
        self.assertEqual(optimize(FOUR_GB)["max_parallel_agents"], 1)
        plan = optimize({"ram_mb": 16384, "ram_available_mb": 12000,
                         "cpu_cores": 8, "vram_mb": None})
        self.assertGreaterEqual(plan["max_parallel_agents"], 2)

    def test_gpu_raises_the_budget_to_vram(self):
        cpu_only = optimize({"ram_mb": 16384, "ram_available_mb": 12000,
                             "cpu_cores": 8, "vram_mb": None})
        with_gpu = optimize({"ram_mb": 16384, "ram_available_mb": 12000,
                             "cpu_cores": 8, "vram_mb": 24576,
                             "gpu_count": 1})
        self.assertTrue(with_gpu["has_gpu"])
        self.assertGreater(with_gpu["model_budget_mb"],
                           cpu_only["model_budget_mb"])

    def test_low_disk_is_surfaced(self):
        plan = optimize({**FOUR_GB, "disk_free_gb": 1.0})
        self.assertIn("disk", plan["recommendation"].lower())

    def test_no_gpu_claim_without_vram(self):
        plan = optimize(FOUR_GB)
        self.assertFalse(plan["has_gpu"])
        self.assertIsNone(plan["vram_mb"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
