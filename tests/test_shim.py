"""The shim changes only the format of the tool call, never its content."""

import json
import os
import unittest

os.environ.setdefault("DEEPINFRA_API_KEY", "test")  # needed at import time
from harness.openclaw_shim import _hoist, should_retry, without_stream  # noqa: E402


class HoistToolCall(unittest.TestCase):
    def test_text_becomes_structured(self):
        msg = {"role": "assistant",
               "content": '<think>I will call it</think>\n<tool_call>\n'
                          '{"name": "send_email", "arguments": {"recipients": ["a@b.c"], "body": "hi"}}'
                          '\n</tool_call>'}
        out = _hoist(msg)
        self.assertEqual(len(out["tool_calls"]), 1)
        call = out["tool_calls"][0]["function"]
        self.assertEqual(call["name"], "send_email")
        self.assertEqual(json.loads(call["arguments"]), {"recipients": ["a@b.c"], "body": "hi"})
        self.assertIsNone(out["content"])

    def test_structured_stays_as_is(self):
        calls = [{"id": "x", "type": "function",
                  "function": {"name": "f", "arguments": "{}"}}]
        msg = {"role": "assistant", "content": "<tool_call>{\"name\":\"g\"}</tool_call>",
               "tool_calls": calls}
        out = _hoist(dict(msg))
        self.assertEqual(out["tool_calls"], calls)
        self.assertEqual(out["content"], msg["content"])

    def test_no_tool_call_stays_as_is(self):
        msg = {"role": "assistant", "content": "You have 3 events on May 15."}
        out = _hoist(dict(msg))
        self.assertNotIn("tool_calls", out)
        self.assertEqual(out["content"], msg["content"])


class RetryTransientFailures(unittest.TestCase):
    def test_retryable_status(self):
        for s in (429, 500, 502, 503, 504):
            self.assertTrue(should_retry(s), s)

    def test_other_status(self):
        for s in (200, 400, 401, 403, 404, 422):
            self.assertFalse(should_retry(s), s)


class RequestWithoutStreaming(unittest.TestCase):
    def test_removes_stream_options_and_nothing_else(self):
        body = {"model": "openai/gpt-oss-120b", "stream": True,
                "stream_options": {"include_usage": True}, "tool_choice": "auto",
                "max_completion_tokens": 4096, "messages": [], "tools": []}
        self.assertEqual(without_stream(dict(body)),
                         {"model": "openai/gpt-oss-120b", "stream": False,
                          "tool_choice": "auto", "max_completion_tokens": 4096,
                          "messages": [], "tools": []})

    def test_without_stream_options_only_turns_streaming_off(self):
        self.assertEqual(without_stream({"model": "m", "stream": True}),
                         {"model": "m", "stream": False})


if __name__ == "__main__":
    unittest.main()
