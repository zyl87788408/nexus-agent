"""Quantisation names from real GGUF/Ollama metadata must not crash estimation."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nexus.models.estimator import _base_quant, estimate_model_memory


class BaseQuantTest(unittest.TestCase):
    def test_ollama_k_quants_map_to_family(self):
        self.assertEqual(_base_quant("Q4_K_M"), "q4")
        self.assertEqual(_base_quant("Q5_K_S"), "q5")
        self.assertEqual(_base_quant("Q6_K"), "q6")
        self.assertEqual(_base_quant("Q8_0"), "q8")

    def test_float_formats_map_to_fp16(self):
        for name in ("f16", "FP16", "bf16", "float16"):
            self.assertEqual(_base_quant(name), "fp16", name)

    def test_bare_and_unknown_do_not_raise(self):
        self.assertEqual(_base_quant("q4"), "q4")
        self.assertEqual(_base_quant("q4_0"), "q4")
        self.assertEqual(_base_quant("weird"), "fp16")


class EstimateWithRealQuantTest(unittest.TestCase):
    """Previously raised KeyError('q4_k_m') for Ollama-style quant names."""

    def test_ollama_quant_without_size_estimates(self):
        result = estimate_model_memory(
            {"name": "qwen2.5:7b", "parameters": "7b", "quantization": "Q4_K_M"}
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["quantization"], "q4")
        self.assertEqual(result["estimate_source"], "parameter_estimate")

    def test_size_bytes_takes_precedence(self):
        result = estimate_model_memory(
            {"name": "qwen2.5:7b", "quantization": "Q4_K_M",
             "size_bytes": 5 * 1024 ** 3}
        )
        self.assertEqual(result["estimate_source"], "model_file_size")

    def test_larger_models_estimate_larger(self):
        small = estimate_model_memory({"name": "m", "parameters": "3b",
                                       "quantization": "Q4_K_M"})
        large = estimate_model_memory({"name": "m", "parameters": "14b",
                                       "quantization": "Q4_K_M"})
        self.assertLess(small["estimated_memory_mb"], large["estimated_memory_mb"])

    def test_q4_cheaper_than_q8(self):
        q4 = estimate_model_memory({"name": "m", "parameters": "7b",
                                    "quantization": "Q4_K_M"})
        q8 = estimate_model_memory({"name": "m", "parameters": "7b",
                                    "quantization": "Q8_0"})
        self.assertLess(q4["estimated_memory_mb"], q8["estimated_memory_mb"])


if __name__ == "__main__":
    unittest.main()
