"""The four models: price, spending cap and path through OpenClaw."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness import openclaw
from harness.adojo_env import model_label
from harness.budget import BudgetExceeded, from_env
from harness.pricing import MODELS, cost_usd, is_known, resolve

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_openclaw  # noqa: E402


class Price(unittest.TestCase):
    def test_deepinfra_has_no_cache_price(self):
        qwen = MODELS["qwen3-32b"]
        self.assertAlmostEqual(cost_usd(qwen, 1_000_000, 1_000_000),
                               qwen.usd_per_m_input + qwen.usd_per_m_output)

    def test_claude_charges_cache_apart(self):
        s5 = MODELS["claude-sonnet-5"]
        # 1M of each: input 2 + output 10 + read 0.20 + write 2.50
        self.assertAlmostEqual(
            cost_usd(s5, 1_000_000, 1_000_000, 1_000_000, 1_000_000), 14.70)

    def test_cache_read_is_10x_cheaper_than_input(self):
        s5 = MODELS["claude-sonnet-5"]
        self.assertAlmostEqual(cost_usd(s5, 0, 0, 1_000_000, 0) * 10,
                               cost_usd(s5, 1_000_000, 0))

    def test_unknown_model_is_not_known(self):
        self.assertFalse(is_known(resolve("no-such-model")))
        for m in MODELS:
            self.assertTrue(is_known(resolve(m)))

    def test_providers(self):
        self.assertEqual(MODELS["claude-sonnet-5"].provider, "anthropic")
        for m in ("qwen3-32b", "deepseek-flash", "gpt-oss-120b"):
            self.assertEqual(MODELS[m].provider, "deepinfra")


class LedgerPerProvider(unittest.TestCase):
    def test_separate_files(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"TCC_BUDGET_CAP_USD": "11",
                                             "TCC_BUDGET_CAP_ANTHROPIC_USD": "90"}):
            di, an = from_env(d), from_env(d, provider="anthropic")
            self.assertEqual(di.path.name, "spend-deepinfra.json")
            self.assertEqual(an.path.name, "spend-anthropic.json")
            self.assertEqual((di.cap_usd, an.cap_usd), (11.0, 90.0))
            an.record(1.0, 10, 5)
            self.assertEqual(from_env(d).ledger.spent_usd, 0.0)

    def test_claude_without_a_cap_spends_nothing(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TCC_BUDGET_CAP_ANTHROPIC_USD", None)
            an = from_env(d, provider="anthropic")
            with self.assertRaises(BudgetExceeded) as e:
                an.check(0.01)
            self.assertIn("TCC_BUDGET_CAP_ANTHROPIC_USD", str(e.exception))


class Label(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(model_label("qwen3-32b"), "Qwen")
        self.assertEqual(model_label("deepseek-flash"), "DeepSeek")
        self.assertEqual(model_label("claude-sonnet-5"), "Claude")
        self.assertEqual(model_label("gpt-oss-120b"), "GPT")


class OpenClawConfig(unittest.TestCase):
    def _write(self, **kw):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".openclaw" / "openclaw.json"
            with mock.patch.object(openclaw, "CONFIG", target):
                openclaw.write_config(8898, **kw)
            return target.read_text(encoding="utf-8")

    def test_deepinfra_matches_the_campaign(self):
        expected = json.dumps({
            "models": {"mode": "merge", "providers": {"di": {
                "api": "openai-completions",
                "baseUrl": "http://127.0.0.1:8898/v1",
                "apiKey": "shim",
                "models": [{"id": "Qwen/Qwen3-32B", "name": "Qwen3-32B",
                            "contextWindow": 40960, "maxTokens": 4096,
                            "reasoning": True,
                            "compat": {"supportsTools": True}}]}}},
            "tools": {"allow": ["adojo__*"]},
        }, indent=2)
        self.assertEqual(self._write(model_id="Qwen/Qwen3-32B"), expected)

    def test_claude_uses_the_native_provider(self):
        cfg = json.loads(self._write(model_id="claude-sonnet-5", provider="anthropic"))
        self.assertNotIn("models", cfg)
        self.assertEqual(cfg["tools"], {"allow": ["adojo__*"]})

    def test_model_ref(self):
        self.assertEqual(openclaw.model_ref("claude-sonnet-5", "anthropic"),
                         "anthropic/claude-sonnet-5")
        self.assertEqual(openclaw.model_ref("openai/gpt-oss-120b"),
                         "di/openai/gpt-oss-120b")

    def test_anthropic_key_only_for_claude(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            self.assertNotIn("ANTHROPIC_API_KEY", openclaw._env("deepinfra"))
            self.assertEqual(openclaw._env("anthropic")["ANTHROPIC_API_KEY"], "sk-test")


class McpRegistration(unittest.TestCase):
    """An episode must run only with its own MCP server."""

    OLD, NEW = "/tmp/ep/old/spec.json", "/tmp/ep/new/spec.json"

    def _register(self, provider, add_works):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / ".openclaw" / "openclaw.json"

            def fake_run(args, **kw):
                cfg = json.loads(target.read_text())
                servers = cfg.setdefault("mcp", {}).setdefault("servers", {})
                if args[2] == "add" and add_works and "adojo" not in servers:
                    servers["adojo"] = {"env": {"AGENTDOJO_SPEC": self.NEW}}
                    target.write_text(json.dumps(cfg))
                    return mock.Mock(returncode=0)
                return mock.Mock(returncode=1)  # unset refused, or already exists

            with mock.patch.object(openclaw, "CONFIG", target), \
                    mock.patch.object(openclaw, "_run", fake_run):
                openclaw.write_config(8898, "x", provider=provider)
                cfg = json.loads(target.read_text())
                cfg["mcp"] = {"servers": {"adojo": {"env": {"AGENTDOJO_SPEC": self.OLD}}}}
                target.write_text(json.dumps(cfg))
                return openclaw.register_mcp(self.NEW, "/tmp/ep/new/out.pkl",
                                             provider=provider)

    def test_server_of_the_previous_episode_fails(self):
        self.assertFalse(self._register("deepinfra", add_works=True))

    def test_refused_add_fails(self):
        self.assertFalse(self._register("deepinfra", add_works=False))

    def test_claude_resets_the_config_first(self):
        self.assertTrue(self._register("anthropic", add_works=True))


class AttemptUsage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "usage.jsonl"
        self.patch = mock.patch.object(run_openclaw, "USAGE_LOG", self.log)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_claude_with_cache(self):
        output = {"usage": {"input": 1000, "output": 500, "cacheRead": 20_000,
                            "cacheWrite": 4000}}
        usage = run_openclaw.attempt_usage(output, MODELS["claude-sonnet-5"])
        self.assertEqual(usage["source"], "openclaw")
        self.assertAlmostEqual(usage["cost_usd"], 0.021)

    def test_claude_timeout_charges_the_ceiling(self):
        output = {"ok": False, "error": {"message": "timeout"},
                  "usage": {"input": 0, "output": 0}}
        usage = run_openclaw.attempt_usage(output, MODELS["claude-sonnet-5"])
        self.assertEqual(usage["source"], "estimated")
        self.assertAlmostEqual(usage["cost_usd"], 0.20)

    def test_claude_acted_without_usage_charges_the_ceiling(self):
        output = {"ok": False, "error": {"message": "LLM request failed: x"},
                  "assistantTurns": 3, "usage": None}
        usage = run_openclaw.attempt_usage(output, MODELS["claude-sonnet-5"])
        self.assertEqual(usage["source"], "estimated")

    def test_claude_with_no_response_charges_nothing(self):
        output = {"ok": False, "status": "timeout", "model": None, "usage": None,
                  "error": {"message": "LLM request failed: DNS lookup for the "
                                       "provider endpoint failed.",
                            "kind": "timeout"}}
        usage = run_openclaw.attempt_usage(output, MODELS["claude-sonnet-5"])
        self.assertEqual((usage["source"], usage["cost_usd"]), ("no_response", 0.0))

    def test_deepinfra_uses_the_shim_count(self):
        self.log.write_text('{"input": 1000, "output": 100}\n'
                            '{"input": 2000, "output": 200}\n')
        output = {"usage": {"input": 1, "output": 1}}
        usage = run_openclaw.attempt_usage(output, MODELS["qwen3-32b"])
        self.assertEqual((usage["input"], usage["output"], usage["source"]),
                         (3000, 300, "shim"))
        self.assertAlmostEqual(usage["cost_usd"], cost_usd(MODELS["qwen3-32b"], 3000, 300))

    def test_deepinfra_without_shim_count_uses_openclaw(self):
        output = {"usage": {"input": 700, "output": 70, "cacheRead": 9999}}
        usage = run_openclaw.attempt_usage(output, MODELS["deepseek-flash"])
        self.assertEqual((usage["input"], usage["cache_read"], usage["source"]),
                         (700, 0, "openclaw"))


if __name__ == "__main__":
    unittest.main()
