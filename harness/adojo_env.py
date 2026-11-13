"""Build the attacked AgentDojo environment.

The runner and the MCP server are separate processes and must build the same
initial environment, so both call this module. The steps follow what
`benchmark_suite_with_injections` does for one combination of suite, user task,
injection task and attack.
"""

from __future__ import annotations

from agentdojo.task_suite.load_suites import get_suite
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.attacks.attack_registry import load_attack
from agentdojo.models import MODEL_NAMES
from agentdojo.base_tasks import BaseUserTask

BENCHMARK_VERSION = "v1.2.1"


def model_label(model_key: str) -> str:
    """Name that some attacks use to address the model ("...to you, Qwen")."""
    k = model_key.lower()
    if "deepseek" in k:
        return "DeepSeek"
    if "qwen" in k:
        return "Qwen"
    if "claude" in k:
        return "Claude"
    if "gpt" in k:
        return "GPT"
    return model_key


class _PipelineStub:
    """load_attack only reads the pipeline's .name."""
    def __init__(self, label):
        self.name = label


def build_attacked(suite_name: str, user_task_id: str, injection_task_id: str,
                   attack_name: str, label: str = "Qwen"):
    """Return (suite, user_task, injection_task, environment, runtime).

    The environment already has the injection. Deep-copy it before running to
    keep the pre-environment.
    """
    MODEL_NAMES[label] = label
    suite = get_suite(BENCHMARK_VERSION, suite_name)
    user_task = suite.get_user_task_by_id(user_task_id)
    inj_task = suite.get_injection_task_by_id(injection_task_id)

    attack = load_attack(attack_name, suite, _PipelineStub(label))
    task_injections = attack.attack(user_task, inj_task)
    environment = suite.load_and_inject_default_environment(task_injections)
    if isinstance(user_task, BaseUserTask):
        environment = user_task.init_environment(environment)

    runtime = FunctionsRuntime(suite.tools)
    return suite, user_task, inj_task, environment, runtime


def tools_for_mcp(suite):
    """The suite's tools in MCP format, with the same schema AgentDojo's own
    agent gives the model."""
    out = []
    for f in suite.tools:
        out.append({
            "name": f.name,
            "description": f.description,
            "inputSchema": f.parameters.model_json_schema(),
        })
    return out
