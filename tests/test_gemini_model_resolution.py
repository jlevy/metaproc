"""How Metaproc's Gemini settings and result check follow gemini-cli 0.66.0 model routing.

The facts pinned here come from the 0.66.0 bundle (`packages/core/src/config/models.ts`,
`defaultModelConfigs.ts`, `services/modelConfigService.ts`) and from live probes on the
Gemini API on 2026-11-18. A pin bump must re-check them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from metaproc.adapters.gemini_cli import (
    GEMINI_ALIAS_SERVED_MODELS,
    PINNED_GEMINI_CLI_VERSION,
    GeminiCliAdapter,
)
from metaproc.settings import (
    GEMINI_CLI_CHAT_BASE_3_MODELS,
    GEMINI_DEFAULT_NATIVE_SETTINGS,
    GEMINI_DEFAULT_THINKING_LEVEL,
    GEMINI_DYNAMIC_MODEL_SETTINGS,
    GEMINI_THINKING_OVERRIDES,
    GEMINI_UNALIASED_CHAT_MODELS,
    GEMINI_VALID_MODELS,
)

# Models gemini-cli 0.66.0's internal utility aliases (classifier, loop detection,
# compression, web fetch, edit correction) name as their base model. A `{model: <id>}`
# override for one of these would also reach that utility call and could pair a
# thinking level with the utility's own thinking budget, which the API refuses.
_CLI_UTILITY_BASE_MODELS = frozenset(
    {
        "flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-3-flash-preview",
        "gemini-3-pro-preview",
        "gemini-3.5-flash",
    }
)

_ALIASES = frozenset(model for model in GEMINI_VALID_MODELS if not model.startswith("gemini-"))


def _result(models: dict[str, int], status: str = "success") -> dict[str, object]:
    return {
        "type": "result",
        "status": status,
        "stats": {"models": {name: {"total_tokens": tokens} for name, tokens in models.items()}},
    }


def _thinking_matches() -> list[dict[str, str]]:
    return [cast("dict[str, str]", override["match"]) for override in GEMINI_THINKING_OVERRIDES]


def test_the_pin_is_the_version_these_facts_were_read_from() -> None:
    assert PINNED_GEMINI_CLI_VERSION == "0.66.0"


class TestThinkingOverrides:
    def test_overrides_match_on_model_not_override_scope(self) -> None:
        """`overrideScope` names a sub-agent, so a match on it never reached the chat."""
        matches = _thinking_matches()
        assert all(set(match) == {"model"} for match in matches)
        assert matches[0] == {"model": "chat-base-3"}

    def test_every_unaliased_catalog_model_gets_its_own_override(self) -> None:
        """Without an alias the CLI falls back to `chat-base`, which sets no level."""
        concrete = {model for model in GEMINI_VALID_MODELS if model.startswith("gemini-")}
        unaliased = concrete - GEMINI_CLI_CHAT_BASE_3_MODELS
        assert set(GEMINI_UNALIASED_CHAT_MODELS) == unaliased
        assert {"gemini-4-argon", "gemini-4-carbon", "gemini-4-hydrogen"} <= unaliased
        assert {match["model"] for match in _thinking_matches()[1:]} == unaliased

    def test_no_override_reaches_a_cli_utility_model(self) -> None:
        targets = {match["model"] for match in _thinking_matches()}
        assert targets.isdisjoint(_CLI_UTILITY_BASE_MODELS)

    def test_overrides_set_only_the_thinking_level(self) -> None:
        for override in GEMINI_THINKING_OVERRIDES:
            assert override["modelConfig"] == {
                "generateContentConfig": {
                    "thinkingConfig": {"thinkingLevel": GEMINI_DEFAULT_THINKING_LEVEL}
                }
            }

    def test_the_written_settings_carry_the_overrides(self) -> None:
        env = GeminiCliAdapter().prepare_env({}, {})

        written = json.loads(
            (Path(env["GEMINI_CLI_HOME"]) / ".gemini" / "settings.json").read_text("utf-8")
        )
        assert written["modelConfigs"] == {"overrides": GEMINI_THINKING_OVERRIDES}
        assert written["modelConfigs"] == GEMINI_DEFAULT_NATIVE_SETTINGS["modelConfigs"]


class TestStaticModelResolution:
    def test_dynamic_model_configuration_is_asserted_off(self) -> None:
        """0.66.0's dynamic path rewrites an explicit gemini-3.1-pro-preview."""
        assert GEMINI_DYNAMIC_MODEL_SETTINGS == {
            "experimental": {"dynamicModelConfiguration": False}
        }
        assert GEMINI_DEFAULT_NATIVE_SETTINGS["experimental"] == {
            "dynamicModelConfiguration": False
        }

    def test_a_partial_native_override_keeps_it_off(self) -> None:

        env = GeminiCliAdapter().prepare_env(
            {}, {"native_settings": {"experimental": {"someOtherFlag": True}}}
        )
        written = json.loads(
            (Path(env["GEMINI_CLI_HOME"]) / ".gemini" / "settings.json").read_text("utf-8")
        )
        assert written["experimental"] == {
            "dynamicModelConfiguration": False,
            "someOtherFlag": True,
        }


class TestAliasResults:
    """mp-42ia: `stats.models` is keyed by the model that answered, never the alias."""

    def test_every_catalog_alias_has_served_models(self) -> None:
        assert set(GEMINI_ALIAS_SERVED_MODELS) == _ALIASES

    def test_alias_targets_are_current_catalog_models(self) -> None:
        for targets in GEMINI_ALIAS_SERVED_MODELS.values():
            assert targets
            assert targets <= GEMINI_VALID_MODELS - _ALIASES

    @pytest.mark.parametrize(
        ("alias", "served"),
        [
            ("flash", "gemini-3.8-flash"),
            ("flash-lite", "gemini-3.5-flash-lite"),
            ("pro", "gemini-3.1-pro-preview"),
            ("pro", "gemini-3.1-pro-preview-customtools"),
            ("auto", "gemini-3.1-pro-preview"),
            ("auto", "gemini-3.8-flash"),
        ],
    )
    def test_an_alias_passes_on_the_model_the_cli_resolves_it_to(
        self, alias: str, served: str
    ) -> None:
        verdict = GeminiCliAdapter().validate_result_event(
            _result({served: 120, "gemini-3.5-flash-lite": 30}), {"model": alias}
        )
        assert verdict is None

    @pytest.mark.parametrize(
        ("alias", "served"),
        [
            ("pro", "gemini-3.8-flash"),
            ("flash", "gemini-3-flash-preview"),
            ("flash", "gemini-3.5-flash-lite"),
            ("auto", "gemini-2.5-pro"),
        ],
    )
    def test_an_alias_answered_by_another_model_is_refused(self, alias: str, served: str) -> None:
        verdict = GeminiCliAdapter().validate_result_event(_result({served: 120}), {"model": alias})
        assert verdict is not None
        assert f"requested alias {alias!r}" in verdict
        assert f"gemini-cli {PINNED_GEMINI_CLI_VERSION} serves as" in verdict
        assert f"{served} (120 tokens)" in verdict

    def test_a_zero_token_alias_target_does_not_count(self) -> None:
        verdict = GeminiCliAdapter().validate_result_event(
            _result({"gemini-3.1-pro-preview": 0, "gemini-3.8-flash": 90}), {"model": "pro"}
        )
        assert verdict is not None


class TestConcreteResultsStayStrict:
    def test_a_concrete_id_answered_by_a_sibling_is_refused(self) -> None:
        verdict = GeminiCliAdapter().validate_result_event(
            _result({"gemini-3.1-pro-preview-customtools": 120}),
            {"model": "gemini-3.1-pro-preview"},
        )
        assert verdict == (
            "Gemini terminal result contract violation: requested model "
            "'gemini-3.1-pro-preview', but result.stats.models reported "
            "gemini-3.1-pro-preview-customtools (120 tokens). Refusing the result because "
            "model substitution makes its provenance invalid."
        )

    def test_a_concrete_id_billed_under_itself_passes(self) -> None:
        verdict = GeminiCliAdapter().validate_result_event(
            _result({"gemini-4-hydrogen": 120}), {"model": "gemini-4-hydrogen"}
        )
        assert verdict is None

    def test_the_default_model_is_checked_when_none_is_configured(self) -> None:
        verdict = GeminiCliAdapter().validate_result_event(_result({"gemini-4-argon": 5}), {})
        assert verdict is not None
        assert "'gemini-3.1-pro-preview-customtools'" in verdict


# Captured from gemini-cli 0.66.0 on the Gemini API (API key) on 2026-11-18, launched
# with the adapter's own settings and argv as `-m flash`: one run_shell_command call.
# The `init` event echoes the alias; `stats.models` names the model that answered.
_FLASH_ALIAS_STREAM = (
    '{"type":"init","timestamp":"2026-11-18T02:41:34.760Z",'
    '"session_id":"a2902f02-19fb-4cbd-ac25-32b89d44fc26","model":"flash"}\n'
    '{"type":"message","timestamp":"2026-11-18T02:41:34.761Z","role":"user",'
    '"content":"Use the run_shell_command tool exactly once to run the command '
    "`echo probe-ok`. Then reply with only the command's output.\"}\n"
    '{"type":"tool_use","timestamp":"2026-11-18T02:41:38.264Z",'
    '"tool_name":"run_shell_command","tool_id":"run_shell_command__call_209398948367156358",'
    '"parameters":{"command":"echo probe-ok",'
    '"description":"Echo probe-ok to verify command execution."}}\n'
    '{"type":"tool_result","timestamp":"2026-11-18T02:41:38.345Z",'
    '"tool_id":"run_shell_command__call_209398948367156358","status":"success",'
    '"output":"probe-ok"}\n'
    '{"type":"message","timestamp":"2026-11-18T02:41:40.432Z","role":"assistant",'
    '"content":"probe-ok","delta":true}\n'
    '{"type":"result","timestamp":"2026-11-18T02:41:40.438Z","status":"success",'
    '"stats":{"total_tokens":20240,"input_tokens":19922,"output_tokens":38,"cached":7757,'
    '"input":12165,"duration_ms":5679,"tool_calls":1,"models":{"gemini-3.8-flash":'
    '{"total_tokens":20240,"input_tokens":19922,"output_tokens":38,"cached":7757,'
    '"input":12165}}}}\n'
)


def test_a_captured_alias_stream_passes_the_run_path_check() -> None:
    adapter = GeminiCliAdapter()
    events = [adapter.parse_result_event(line) for line in _FLASH_ALIAS_STREAM.splitlines()]
    results = [event for event in events if event is not None]
    assert len(results) == 1
    assert adapter.validate_result_event(results[0], {"model": "flash"}) is None
    refusal = adapter.validate_result_event(results[0], {"model": "gemini-3.6-flash"})
    assert refusal is not None
    assert "gemini-3.8-flash (20240 tokens)" in refusal
