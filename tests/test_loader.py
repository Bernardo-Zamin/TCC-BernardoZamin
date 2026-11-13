"""`load_episodes` decides which episodes enter the metrics.

- Control runs (no attack) stay out.
- Instrument failures stay out: they are not "the agent resisted".
- "Read the injection" needs the payload in a tool result.
"""

import json
import tempfile
import unittest
from pathlib import Path

from harness.analysis import INJECTION_MARKERS, load_episodes

PAYLOAD = ("This is a line long enough to become a fragment, "
           "with the attacker's request inside it.")


def episode(**kw) -> dict:
    """A minimal valid episode, in the recorded format."""
    return {
        "resumo": {
            "veredito": kw.get("veredito", "bloqueado"),
            "ataque": kw.get("ataque", "direct"),
            "suite": "banking",
            "tarefa": "user_task_0",
            "injecao": "injection_task_0",
            "repeticao": kw.get("repeticao", 1),
            "modelo_key": "qwen3-32b",
            "ataque_venceu": kw.get("ataque_venceu", False),
            "tarefa_concluida": True,
            "duracao_s": 10.0,
            "ok": kw.get("ok", True),
            "avaliacao_valida": kw.get("avaliacao_valida", True),
            "erro": kw.get("erro"),
        },
        "tarefa_do_usuario": "pay the bill",
        "conteudo_injetado": kw.get("conteudo_injetado", ""),
        "acoes_do_agente": kw.get("acoes_do_agente", []),
        "resposta_ao_usuario": "done",
    }


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel: str, doc: dict):
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc), encoding="utf-8")


class WhatEnters(LoaderTest):
    def test_valid_episode_enters(self):
        self.write("bloqueado__a.json", episode())
        self.assertEqual(len(load_episodes(self.root)), 1)

    def test_control_run_stays_out(self):
        doc = episode()
        doc["resumo"]["ataque"] = None
        self.write("referencia__a.json", doc)
        self.assertEqual(load_episodes(self.root), [])

    def test_run_summary_is_not_an_episode(self):
        self.write("resumo-da-execucao.json", {"asr": 0.33})
        self.assertEqual(load_episodes(self.root), [])

    def test_instrument_failure_stays_out(self):
        self.write("VENCEU__ok.json", episode(ataque_venceu=True))
        self.write("invalido__429.json", episode(ok=False, erro="429 rate limit"))
        self.write("invalido__eval.json", episode(avaliacao_valida=False))

        eps = load_episodes(self.root)
        self.assertEqual(len(eps), 1)
        self.assertTrue(eps[0]["won"])

        everything = load_episodes(self.root, valid_only=False)
        self.assertEqual(len(everything), 3)
        self.assertEqual(sum(1 for e in everything if not e["valid"]), 2)

    def test_missing_repetition_becomes_zero(self):
        doc = episode()
        del doc["resumo"]["repeticao"]
        self.write("a.json", doc)
        self.assertEqual(load_episodes(self.root)[0]["rep"], 0)


class ReadTheInjection(LoaderTest):
    def test_payload_in_a_result_counts(self):
        self.write("a.json", episode(
            conteudo_injetado=PAYLOAD,
            acoes_do_agente=[{"ferramenta": "read_file",
                              "retorno": "header\n" + PAYLOAD}]))
        self.assertTrue(load_episodes(self.root)[0]["read"])

    def test_payload_not_in_a_result_does_not_count(self):
        self.write("a.json", episode(
            conteudo_injetado=PAYLOAD,
            acoes_do_agente=[{"ferramenta": "get_iban", "retorno": "CH93..."}]))
        self.assertFalse(load_episodes(self.root)[0]["read"])

    def test_no_tool_call_means_not_read(self):
        self.write("a.json", episode(conteudo_injetado=PAYLOAD))
        self.assertFalse(load_episodes(self.root)[0]["read"])

    def test_payload_in_an_argument_is_not_enough(self):
        self.write("a.json", episode(
            conteudo_injetado=PAYLOAD,
            acoes_do_agente=[{"ferramenta": "send_email",
                              "argumentos": {"body": PAYLOAD},
                              "retorno": "sent"}]))
        self.assertFalse(load_episodes(self.root)[0]["read"])

    def test_no_marker_is_a_person_name(self):
        # A person's name can occur in a clean environment ("Emma Johnson" does).
        for marker in INJECTION_MARKERS:
            with self.subTest(marker=marker):
                plain = marker.replace(":", "").replace("<", "").replace(">", "").strip()
                self.assertFalse(plain.istitle() and " " in marker)


class Layout(LoaderTest):
    def test_finds_episode_in_subfolder(self):
        self.write("qwen3-32b/rep03/VENCEU__a.json",
                   episode(ataque_venceu=True, repeticao=3))
        eps = load_episodes(self.root)
        self.assertEqual(len(eps), 1)
        self.assertEqual(eps[0]["rep"], 3)
        self.assertTrue(eps[0]["won"])

    def test_finds_mixed_layout(self):
        self.write("qwen3-32b/rep01/bloqueado__a.json", episode(repeticao=1))
        self.write("bloqueado__b.json", episode(repeticao=2))
        eps = load_episodes(self.root)
        self.assertEqual({e["rep"] for e in eps}, {1, 2})

    def test_folder_name_does_not_hide_episodes(self):
        self.write("resumo-da-execucao-old/rep01/VENCEU__a.json",
                   episode(ataque_venceu=True))
        self.write("resumo-da-execucao.json", {"asr": 0.33})
        self.assertEqual(len(load_episodes(self.root)), 1)

    def test_any_depth(self):
        self.write("a/b/c/d/bloqueado__x.json", episode())
        self.assertEqual(len(load_episodes(self.root)), 1)


if __name__ == "__main__":
    unittest.main()
