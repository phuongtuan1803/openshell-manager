"""CLI-only adapters. Isolation, scopes, retries and publication belong to workflow."""

import json
import re
from dataclasses import dataclass

UNVERIFIED = "NOT LIVE VERIFIED — Founder approval required for live evaluation."


@dataclass(frozen=True)
class Agent:
    name: str
    package: str
    version: str
    executable: str
    live: bool = False

    def install(self):
        # Installation happens in an image build, never in a model session.
        return ["npm", "install", "--global", f"{self.package}@{self.version}"]

    def model_args(self, model):
        if model is None:
            return []
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,160}", model):
            raise ValueError("Invalid model identifier")
        return ["--model", model]

    def interactive(self, executable=None, model=None):
        return [executable or self.executable] + self.model_args(model)

    def autonomous(self, prompt, executable=None, model=None):
        raise NotImplementedError

    def auth_configuration(self, mode="api"):
        raise NotImplementedError

    def accounting(self, event):
        raise NotImplementedError

    def environment(self):
        return {
            "HOME": f"/sandbox/{self.name}-home",
            "XDG_CONFIG_HOME": f"/sandbox/{self.name}-home/.config",
            "XDG_DATA_HOME": f"/sandbox/{self.name}-home/.local/share",
        }


class Codex(Agent):
    def shell_args(self):
        # Keep native Provider placeholders and Git config in child commands.
        # Disable shell snapshots so this transient environment is never cached.
        return [
            "-c",
            'shell_environment_policy.inherit="all"',
            "-c",
            "shell_environment_policy.ignore_default_excludes=true",
            "--disable",
            "shell_snapshot",
        ]

    def interactive(self, executable=None, model=None):
        return (
            [
                executable or self.executable,
                "--no-daemon",
                "--ask-for-approval",
                "never",
                "--sandbox",
                "danger-full-access",
            ]
            + self.shell_args()
            + self.model_args(model)
        )

    def autonomous(self, prompt, executable=None, model=None):
        return (
            [
                executable or self.executable,
                "--no-daemon",
                "exec",
                "--json",
                "--ephemeral",
                "--sandbox",
                "danger-full-access",
            ]
            + self.shell_args()
            + self.model_args(model)
            + ["--", prompt]
        )

    def auth_configuration(self, mode="api"):
        return {"mode": "guest-device-login", "home": "/sandbox/.codex"}

    def environment(self):
        return {"CODEX_HOME": "/sandbox/.codex"}

    def accounting(self, event):
        if event.get("type") == "turn.completed":
            return event.get("usage", {}), None
        return None, None


class Claude(Agent):
    def autonomous(self, prompt, executable=None, model=None):
        return (
            [
                executable or self.executable,
                "--print",
                "--output-format",
                "stream-json",
                "--verbose",
            ]
            + self.model_args(model)
            + ["--", prompt]
        )

    def auth_configuration(self, mode="api"):
        if mode not in {"api", "subscription"}:
            raise ValueError("Claude auth mode must be api or subscription")
        return {
            "mode": mode,
            "credential_env": "ANTHROPIC_API_KEY" if mode == "api" else "CLAUDE_CODE_OAUTH_TOKEN",
            "delivery": "reviewed OpenShell provider; no host credential files",
            "enabled": False,
        }

    def accounting(self, event):
        if event.get("type") == "result":
            return event.get("usage", {}), event.get("total_cost_usd")
        return None, None


class OpenCode(Agent):
    def model_args(self, model):
        if model is None or "/" not in model:
            raise ValueError("OpenCode requires an explicit provider/model")
        return super().model_args(model)

    def autonomous(self, prompt, executable=None, model=None):
        return (
            [executable or self.executable, "run", "--format", "json"]
            + self.model_args(model)
            + ["--", prompt]
        )

    def auth_configuration(self, mode="api"):
        return {
            "mode": "selected-provider",
            "delivery": "reviewed OpenShell provider",
            "enabled": False,
        }

    def accounting(self, event):
        part = event.get("part")
        if event.get("type") == "step_finish" and isinstance(part, dict):
            return part.get("tokens", {}), part.get("cost")
        return None, None


AGENTS = {
    "codex": Codex("codex", "@openai/codex", "0.162.1", "/usr/local/bin/codex", True),
    "claude": Claude("claude", "@anthropic-ai/claude-code", "2.1.296", "/usr/local/bin/claude"),
    "opencode": OpenCode("opencode", "opencode-ai", "1.19.0", "/usr/local/bin/opencode"),
}


def adapter(name):
    if name not in AGENTS:
        raise ValueError("Choose codex, claude or opencode")
    return AGENTS[name]


def require_live(name):
    agent = adapter(name)
    if not agent.live:
        raise ValueError(UNVERIFIED)
    return agent


def usage(name, output):
    """Whitelist numeric accounting only. Never persist prompts, events or tool output."""
    records = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        raw, cost = adapter(name).accounting(event)
        if not isinstance(raw, dict):
            continue
        row = {
            k: v
            for k, v in raw.items()
            if k
            in {
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "input",
                "output",
                "reasoning",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            }
            and type(v) in {int, float}
            and v >= 0
        }
        row["cost_usd"] = cost if type(cost) in {int, float} and cost >= 0 else None
        records.append(row)
    return records


def failure_category(output):
    """Classify CLI failure without persisting arbitrary messages or identifiers."""
    lowered = output.lower()
    for label, patterns in {
        "cli-arguments": ["unexpected argument", "unrecognized", "invalid value", "usage:"],
        "model-unavailable": ["model is not supported", "model_not_found", "does not exist"],
        "authentication": ["unauthorized", "not logged in", "authentication", "401"],
        "network": ["connection", "network", "stream disconnected", "failed to fetch"],
        "namespace": ["bwrap", "namespace"],
    }.items():
        if any(pattern in lowered for pattern in patterns):
            return label
    return "unclassified"
