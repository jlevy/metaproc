"""Tests for the three-way failure taxonomy (plan §Phase 5 P5.5).

Covers:

- ``FailureSeverity`` constants + ``default_severity_for_status`` map.
- ``AuthFailureClassification.effective_severity()`` including the
  known-bug override invariant.
- Per-adapter ``classify_failure`` fixtures asserting each realistic
  error string maps to the expected ``(status, severity)`` pair.
- Property tests:
  * ``severity=ABORT`` always surfaces (never swallowed).
  * ``severity=RETRY_NOW`` never flips pool state (credential fine).
  * Known-bug signature always yields ABORT regardless of status.
- Known-bug detector registry + matching.
"""

from __future__ import annotations

from metaproc.adapters.base import (
    AuthFailureClassification,
    FailureSeverity,
    default_severity_for_status,
)
from metaproc.adapters.claude_code import ClaudeCodeCliAdapter
from metaproc.adapters.codex import CodexCliAdapter
from metaproc.dispatch.known_bugs import (
    KNOWN_BUG_REGISTRY,
    detect_known_bug,
    registered_signatures,
)

# ── FailureSeverity + AuthFailureClassification ────────────────


class TestFailureSeverityConstants:
    def test_three_string_values(self):
        # Verified against the three-way spec: RETRY_NOW /
        # RETRY_AFTER_WAIT / ABORT. String values are stable for
        # log aggregation.
        assert FailureSeverity.RETRY_NOW == "retry-now"
        assert FailureSeverity.RETRY_AFTER_WAIT == "retry-after-wait"
        assert FailureSeverity.ABORT == "abort"

    def test_constants_are_distinct(self):
        values = {
            FailureSeverity.RETRY_NOW,
            FailureSeverity.RETRY_AFTER_WAIT,
            FailureSeverity.ABORT,
        }
        assert len(values) == 3


class TestDefaultSeverityForStatus:
    def test_cooling_maps_to_retry_after_wait(self):
        assert default_severity_for_status("cooling") == FailureSeverity.RETRY_AFTER_WAIT

    def test_expired_maps_to_abort(self):
        assert default_severity_for_status("expired") == FailureSeverity.ABORT

    def test_ok_maps_to_retry_now(self):
        # `ok` shouldn't appear on a failure classification, but
        # default map must still be defined for completeness.
        assert default_severity_for_status("ok") == FailureSeverity.RETRY_NOW

    def test_unknown_maps_to_retry_now(self):
        # Unknown is the less-destructive default: let the generic
        # retry classifier own the real decision rather than flipping
        # pool state on an unexplained failure.
        assert default_severity_for_status("unknown") == FailureSeverity.RETRY_NOW


class TestEffectiveSeverity:
    def test_explicit_severity_wins_over_status_default(self):
        # cooling default is RETRY_AFTER_WAIT, but explicit override
        # wins. Gives adapters escape hatch when they have more
        # specific signal than the status alone.
        c = AuthFailureClassification(status="cooling", severity=FailureSeverity.ABORT)
        assert c.effective_severity() == FailureSeverity.ABORT

    def test_unset_falls_back_to_status_default(self):
        c = AuthFailureClassification(status="cooling")
        assert c.effective_severity() == FailureSeverity.RETRY_AFTER_WAIT

    def test_known_bug_forces_abort_over_status(self):
        # Bugs always abort. Even a `cooling` status yields ABORT
        # when known_bug_signature is set — we don't retry past a
        # known bug.
        c = AuthFailureClassification(
            status="cooling",
            cooling_until_ts=9999,
            known_bug_signature="test-bug",
        )
        assert c.effective_severity() == FailureSeverity.ABORT

    def test_known_bug_forces_abort_over_explicit_severity(self):
        # Explicit severity can't escape known-bug override.
        c = AuthFailureClassification(
            status="unknown",
            severity=FailureSeverity.RETRY_NOW,
            known_bug_signature="test-bug",
        )
        assert c.effective_severity() == FailureSeverity.ABORT


# ── Known-bug registry + detector ──────────────────────────────


class TestKnownBugRegistry:
    def test_registry_has_expected_signatures(self):
        names = set(registered_signatures())
        # Starter registry ships at least these four — add-only per
        # spec; fewer would be a regression, more is fine.
        for expected in (
            "claude-startup-exit-1-silent",
            "retired-packet-yaml-path",
            "mcp-schema-config-invalid",
            "metaproc-version-mismatch",
        ):
            assert expected in names, f"missing registered bug: {expected}"

    def test_every_signature_has_hint(self):
        for sig in KNOWN_BUG_REGISTRY:
            assert sig.hint, f"{sig.name} missing operator hint"
            assert sig.first_seen, f"{sig.name} missing first_seen date"


class TestKnownBugDetector:
    def test_detects_claude_startup_silent(self):
        sig = detect_known_bug(stderr="Process exited with exit code 1.")
        assert sig is not None
        assert sig.name == "claude-startup-exit-1-silent"

    def test_does_not_detect_claude_startup_when_ratelimit_header_present(self):
        # Pattern is explicitly negative on anthropic-ratelimit to
        # avoid false-firing on real rate-limit exits.
        sig = detect_known_bug(stderr="exit code 1: anthropic-ratelimit-unified-reset=1700000000")
        assert sig is None

    def test_detects_retired_packet_yaml(self):
        sig = detect_known_bug(
            stderr="FileNotFoundError: [Errno 2] No such file: /runs/x/v11/packet.yaml",
        )
        assert sig is not None
        assert sig.name == "retired-packet-yaml-path"

    def test_detects_mcp_schema_invalid(self):
        sig = detect_known_bug(stderr="strict-mcp-config validation failed")
        assert sig is not None
        assert sig.name == "mcp-schema-config-invalid"

    def test_detects_metaproc_version_mismatch(self):
        sig = detect_known_bug(
            stderr="ImportError: cannot import name 'compute_run_dir' from 'metaproc.engine'",
        )
        assert sig is not None
        assert sig.name == "metaproc-version-mismatch"

    def test_session_log_target_ignored_when_stderr_matches_first(self):
        # Registry is scanned in order; first match wins. Verifies
        # the detector returns early rather than scanning everything.
        sig = detect_known_bug(
            stderr="FileNotFoundError: /runs/x/v11/packet.yaml",
            session_log_text="ImportError in metaproc",
        )
        assert sig is not None
        assert sig.name == "retired-packet-yaml-path"

    def test_no_match_returns_none(self):
        assert detect_known_bug(stderr="HTTP 429 too_many_requests") is None

    def test_empty_inputs_return_none(self):
        assert detect_known_bug() is None

    # Narrowed silent-exit-1 pattern: must NOT match any text that
    # carries a credential-terminal or diagnostic word. The classifier
    # relies on this narrowing so its ordered `invalid_grant` check
    # doesn't have to overlap with the detector.
    def test_silent_exit_1_does_not_match_invalid_grant(self):
        assert detect_known_bug(stderr="exit code 1: invalid_grant") is None

    def test_silent_exit_1_does_not_match_authentication_error(self):
        assert detect_known_bug(stderr="exit code 1: authentication_error (401)") is None

    def test_silent_exit_1_does_not_match_unauthorized(self):
        assert detect_known_bug(stderr="exit code 1: unauthorized") is None

    def test_silent_exit_1_does_not_match_traceback(self):
        # Diagnostic prose means the CLI DID report a cause — leave
        # it to the generic retry classifier rather than tagging as
        # a known bug.
        assert (
            detect_known_bug(stderr="exit code 1\nTraceback (most recent call last): ...") is None
        )


# ── Per-adapter classifier fixtures ────────────────────────────


class TestClaudeClassifierTaxonomy:
    def setup_method(self):
        self.adapter = ClaudeCodeCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    # Credential-level failures: status=expired marks the label in the
    # pool. Severity differs: refresh-race failures retry to alt label
    # (RETRY_AFTER_WAIT); access-token rejection without refresh failure
    # is unrecoverable on the same label (ABORT).
    def test_oauth_refresh_400_is_expired_retry_after_wait(self):
        debug_text = (
            "[ERROR] AxiosError: [url=https://platform.claude.com/v1/oauth/token,"
            " status=400] AxiosError: Request failed with status code 400\n"
        )
        r = self.adapter.classify_failure(None, debug_text, None)
        assert r.status == "expired"
        # The label transitions via state_mark_expired regardless of
        # severity. RETRY_AFTER_WAIT lets the runpool re-queue the item
        # so the next selection picks the alt label (which is healthy).
        assert r.effective_severity() == FailureSeverity.RETRY_AFTER_WAIT
        assert r.oauth_refresh_status == 400

    def test_api_401_via_debug_log_is_expired_retry_after_wait(self):
        # API 401 (access token rejected) is recoverable via failover:
        # ``RETRY_AFTER_WAIT`` adds the failed label to ``pool_exclude``
        # and the next acquire picks the alt. ``mark_expired`` from a
        # sibling teardown also flips the label ineligible for new
        # acquisitions in parallel. Earlier ``ABORT`` semantics burned
        # the cohort with retry_count=0 (the multi-label failure
        # mode); fixed so a single dispatch can recover end-to-end.
        debug_text = "[ERROR] API error (attempt 1/11): 401 authentication_error\n"
        r = self.adapter.classify_failure(None, debug_text, None)
        assert r.status == "expired"
        assert r.effective_severity() == FailureSeverity.RETRY_AFTER_WAIT
        assert r.api_status == 401

    # ABORT: known bugs
    def test_retired_packet_yaml_is_known_bug_abort(self):
        r = self.adapter.classify_failure(None, "FileNotFoundError: .../v11/packet.yaml", None)
        assert r.effective_severity() == FailureSeverity.ABORT
        assert r.known_bug_signature == "retired-packet-yaml-path"

    # RETRY_AFTER_WAIT: cooling-family
    def test_too_many_requests_is_cooling_retry_after_wait(self):
        r = self.adapter.classify_failure(None, "HTTP 429 too_many_requests", None)
        assert r.status == "cooling"
        assert r.effective_severity() == FailureSeverity.RETRY_AFTER_WAIT

    def test_overloaded_error_is_cooling_retry_after_wait(self):
        r = self.adapter.classify_failure(None, "overloaded_error: try later", None)
        assert r.status == "cooling"
        assert r.effective_severity() == FailureSeverity.RETRY_AFTER_WAIT

    def test_pacific_reset_phrase_is_cooling_retry_after_wait(self):
        r = self.adapter.classify_failure(None, "resets 11am (America/Los_Angeles)", None)
        assert r.status == "cooling"
        assert r.effective_severity() == FailureSeverity.RETRY_AFTER_WAIT
        assert r.cooling_until_ts is not None

    # RETRY_NOW: transient network/5xx
    def test_503_is_unknown_retry_now(self):
        r = self.adapter.classify_failure(None, "HTTP 503 service unavailable", None)
        assert r.status == "unknown"
        assert r.effective_severity() == FailureSeverity.RETRY_NOW

    def test_econnreset_is_unknown_retry_now(self):
        r = self.adapter.classify_failure(None, "ECONNRESET during POST", None)
        assert r.status == "unknown"
        assert r.effective_severity() == FailureSeverity.RETRY_NOW

    def test_timeout_is_unknown_retry_now(self):
        r = self.adapter.classify_failure(None, "Request timeout after 30s", None)
        assert r.status == "unknown"
        assert r.effective_severity() == FailureSeverity.RETRY_NOW

    # RETRY_NOW default: truly unknown
    def test_truly_unknown_defaults_to_retry_now(self):
        r = self.adapter.classify_failure(None, "something completely weird", None)
        assert r.status == "unknown"
        assert r.effective_severity() == FailureSeverity.RETRY_NOW

    # Priority: known bug > cooling
    def test_known_bug_beats_cooling(self):
        # Error text mentions 429 but also contains a known-bug
        # signature — known bug wins, severity ABORT.
        r = self.adapter.classify_failure(
            None,
            "FileNotFoundError: /runs/x/v11/packet.yaml (HTTP 429 too_many_requests)",
            None,
        )
        assert r.effective_severity() == FailureSeverity.ABORT
        assert r.known_bug_signature == "retired-packet-yaml-path"

    # Regression: the silent-exit-1 known-bug signature must NOT
    # swallow real credential failures. A real refresh-token failure
    # surfaces as a structured AxiosError on /v1/oauth/token in the
    # slot debug log; classify_failure_for_slot prepends that to
    # error_str so the parser sees it. The classifier routes to
    # expired (with RETRY_AFTER_WAIT severity so concurrent fan-out
    # items re-queue and pick the alt label) via the structured signal
    # — never via substring search of unconstrained text.
    def test_oauth_refresh_400_is_expired_not_known_bug(self):
        debug_text = (
            "[DEBUG] [API:auth] OAuth token check starting\n"
            "[ERROR] AxiosError: [url=https://platform.claude.com/v1/oauth/token,"
            " status=400] AxiosError: Request failed with status code 400\n"
        )
        r = self.adapter.classify_failure(None, debug_text, None)
        assert r.status == "expired"
        assert r.effective_severity() == FailureSeverity.RETRY_AFTER_WAIT
        assert r.known_bug_signature is None
        assert r.oauth_refresh_status == 400

    def test_api_401_via_stream_json_is_expired_not_known_bug(self, tmp_path):
        # API rejection: stream-json result event with api_error_status=401.
        # Severity ``RETRY_AFTER_WAIT`` so cohort items rotate to the
        # alt label rather than burning at retry_count=0.
        session_log = tmp_path / "session.jsonl"
        session_log.write_text(
            '{"type":"result","is_error":true,"api_error_status":401,'
            '"result":"Failed to authenticate. API Error: 401 ..."}\n'
        )
        r = self.adapter.classify_failure(None, "exit code 1", session_log)
        assert r.status == "expired"
        assert r.effective_severity() == FailureSeverity.RETRY_AFTER_WAIT
        assert r.known_bug_signature is None
        assert r.api_status == 401

    def test_silent_exit_1_still_matches_known_bug(self):
        # The narrowing must NOT regress the genuinely silent case
        # the signature was designed for. A plain "exit code 1" with
        # no error detail stays a known bug.
        r = self.adapter.classify_failure(None, "Process exited with exit code 1.", None)
        assert r.known_bug_signature == "claude-startup-exit-1-silent"
        assert r.effective_severity() == FailureSeverity.ABORT


class TestCodexClassifierTaxonomy:
    def setup_method(self):
        self.adapter = CodexCliAdapter()  # pyright: ignore[reportUninitializedInstanceVariable]

    def test_invalid_grant_is_expired_abort(self):
        r = self.adapter.classify_failure(None, "refresh failed: invalid_grant", None)
        assert r.status == "expired"
        assert r.effective_severity() == FailureSeverity.ABORT

    def test_unauthorized_is_expired_abort(self):
        r = self.adapter.classify_failure(None, "HTTP 401 Unauthorized", None)
        assert r.status == "expired"
        assert r.effective_severity() == FailureSeverity.ABORT

    def test_too_many_requests_is_cooling_retry_after_wait(self):
        r = self.adapter.classify_failure(None, "HTTP 429 too_many_requests", None)
        assert r.status == "cooling"
        assert r.effective_severity() == FailureSeverity.RETRY_AFTER_WAIT

    def test_rate_limit_is_cooling_retry_after_wait(self):
        r = self.adapter.classify_failure(None, "rate limit exceeded", None)
        assert r.status == "cooling"
        assert r.effective_severity() == FailureSeverity.RETRY_AFTER_WAIT

    def test_503_is_retry_now(self):
        r = self.adapter.classify_failure(None, "HTTP 503 bad gateway", None)
        assert r.status == "unknown"
        assert r.effective_severity() == FailureSeverity.RETRY_NOW

    def test_econnrefused_is_retry_now(self):
        r = self.adapter.classify_failure(None, "ECONNREFUSED", None)
        assert r.status == "unknown"
        assert r.effective_severity() == FailureSeverity.RETRY_NOW

    def test_known_bug_is_abort(self):
        r = self.adapter.classify_failure(None, "FileNotFoundError: packet.yaml", None)
        assert r.effective_severity() == FailureSeverity.ABORT
        assert r.known_bug_signature == "retired-packet-yaml-path"

    def test_truly_unknown_defaults_to_retry_now(self):
        r = self.adapter.classify_failure(None, "unrecognized thing", None)
        assert r.effective_severity() == FailureSeverity.RETRY_NOW

    # Regression: same precedence rule as Claude — credential
    # terminal signals must beat the silent-exit-1 bug pattern.
    def test_exit_code_1_invalid_grant_is_expired_not_known_bug(self):
        r = self.adapter.classify_failure(
            None, "Process exited with exit code 1: invalid_grant", None
        )
        assert r.status == "expired"
        assert r.effective_severity() == FailureSeverity.ABORT
        assert r.known_bug_signature is None

    def test_exit_code_1_unauthorized_is_expired_not_known_bug(self):
        r = self.adapter.classify_failure(
            None, "Process exited with exit code 1: unauthorized", None
        )
        assert r.status == "expired"
        assert r.effective_severity() == FailureSeverity.ABORT
        assert r.known_bug_signature is None


# ── Invariant property tests ───────────────────────────────────


class TestTaxonomyInvariants:
    """Structural properties that must hold across all classifier paths."""

    def test_abort_never_maps_to_silent_retry(self):
        # effective_severity() == ABORT must be distinguishable from
        # the retry verdicts — no downstream path should conflate
        # them. Basic sanity: strings are distinct.
        assert FailureSeverity.ABORT != FailureSeverity.RETRY_NOW
        assert FailureSeverity.ABORT != FailureSeverity.RETRY_AFTER_WAIT

    def test_retry_now_status_does_not_flip_pool_state(self):
        # RETRY_NOW is reserved for errors where the credential
        # didn't cause the failure. Verified indirectly: classifiers
        # that emit RETRY_NOW also emit status="unknown" (not
        # cooling/expired), so the pool-side teardown path won't
        # mutate state. Any new path that violates this rule would
        # show up as a different status with RETRY_NOW severity.
        adapter = ClaudeCodeCliAdapter()
        for stderr in (
            "HTTP 503 bad gateway",
            "ECONNRESET",
            "Request timeout",
            "random-unclassified-thing",
        ):
            r = adapter.classify_failure(None, stderr, None)
            if r.effective_severity() == FailureSeverity.RETRY_NOW:
                assert r.status == "unknown", (
                    f"RETRY_NOW severity must not ride a credential-flipping "
                    f"status, got status={r.status!r} for stderr={stderr!r}"
                )

    def test_known_bug_always_forces_abort(self):
        # Regardless of what the classifier would have returned,
        # setting known_bug_signature MUST yield ABORT severity.
        for base_status in ("ok", "cooling", "expired", "unknown"):
            for base_severity in (
                None,
                FailureSeverity.RETRY_NOW,
                FailureSeverity.RETRY_AFTER_WAIT,
                FailureSeverity.ABORT,
            ):
                c = AuthFailureClassification(
                    status=base_status,  # type: ignore[arg-type]
                    severity=base_severity,
                    known_bug_signature="anything",
                )
                assert c.effective_severity() == FailureSeverity.ABORT, (
                    f"known-bug must force ABORT; got "
                    f"{c.effective_severity()} for {base_status}/{base_severity}"
                )

    def test_classifier_outputs_always_populate_severity_or_default(self):
        # Every classifier path must return a classification whose
        # effective_severity() is one of the three taxonomy values.
        claude = ClaudeCodeCliAdapter()
        codex = CodexCliAdapter()
        fixtures = [
            "HTTP 429 too_many_requests",
            "OAuth error: invalid_grant",
            "HTTP 503",
            "unclassified",
            "FileNotFoundError: packet.yaml",
            "resets 3am (America/Los_Angeles)",
            "authentication_error",
        ]
        valid = {
            FailureSeverity.RETRY_NOW,
            FailureSeverity.RETRY_AFTER_WAIT,
            FailureSeverity.ABORT,
        }
        for adapter in (claude, codex):
            for stderr in fixtures:
                r = adapter.classify_failure(None, stderr, None)
                assert r.effective_severity() in valid, (
                    f"unknown severity {r.effective_severity()!r} for "
                    f"{adapter.adapter_type} / {stderr!r}"
                )
