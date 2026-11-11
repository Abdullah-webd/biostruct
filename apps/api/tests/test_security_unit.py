"""Pure unit tests: no database, no network. These must always run in CI."""

from __future__ import annotations

from decimal import Decimal

import pytest

from biostruct import security
from biostruct.config import Env, Settings
from biostruct.models.project import Budget, RankingWeights


class TestPasswordHashing:
    def test_hash_is_not_the_password(self) -> None:
        h = security.hash_password("correct-horse-battery")
        assert "correct-horse-battery" not in h
        assert h.startswith("$argon2id$")

    def test_same_password_hashes_differently(self) -> None:
        """Distinct salts: two users with the same password must not share a hash."""
        assert security.hash_password("same-password-12") != security.hash_password(
            "same-password-12"
        )

    def test_verify_roundtrip(self) -> None:
        h = security.hash_password("correct-horse-battery")
        assert security.verify_password("correct-horse-battery", h) is True
        assert security.verify_password("wrong-horse-battery", h) is False

    def test_verify_against_missing_hash_is_false_but_does_work(self) -> None:
        """An absent user must still cost a verification, or timing leaks existence."""
        assert security.verify_password("anything-at-all", None) is False

    def test_verify_rejects_garbage_hash(self) -> None:
        assert security.verify_password("whatever", "not-a-hash") is False


class TestPasswordStrength:
    @pytest.mark.parametrize("pw", ["short", "elevenchars", "a" * 11])
    def test_too_short_rejected(self, pw: str) -> None:
        assert security.validate_password_strength(pw) is not None

    def test_common_password_rejected(self) -> None:
        assert security.validate_password_strength("password123") is not None

    def test_password_containing_email_rejected(self) -> None:
        reason = security.validate_password_strength(
            "chinedu-is-my-password", email="chinedu@example.org"
        )
        assert reason is not None and "email" in reason.lower()

    def test_password_containing_name_rejected(self) -> None:
        reason = security.validate_password_strength(
            "amara-secure-phrase", email="a@b.co", name="Amara"
        )
        assert reason is not None and "name" in reason.lower()

    def test_low_variety_rejected(self) -> None:
        assert security.validate_password_strength("ababababababab") is not None

    def test_good_passphrase_accepted(self) -> None:
        assert (
            security.validate_password_strength(
                "ribosome velvet cartography", email="r@example.org", name="Ada"
            )
            is None
        )


class TestSessionTokens:
    def test_tokens_are_unique_and_long(self) -> None:
        tokens = {security.new_session_token() for _ in range(200)}
        assert len(tokens) == 200
        assert all(len(t) >= 40 for t in tokens)

    def test_stored_value_is_a_hash_not_the_token(self) -> None:
        token = security.new_session_token()
        stored = security.hash_session_token(token)
        assert token not in stored
        assert len(stored) == 64  # sha256 hex
        assert security.hash_session_token(token) == stored  # deterministic


class TestCsrf:
    def test_matching_tokens_pass(self) -> None:
        t = security.new_csrf_token()
        assert security.csrf_tokens_match(t, t) is True

    def test_mismatched_or_absent_tokens_fail(self) -> None:
        t = security.new_csrf_token()
        assert security.csrf_tokens_match(t, security.new_csrf_token()) is False
        assert security.csrf_tokens_match(None, t) is False
        assert security.csrf_tokens_match(t, None) is False
        assert security.csrf_tokens_match("", "") is False


class TestRateLimiter:
    def test_allows_up_to_the_limit_then_blocks(self) -> None:
        limiter = security.RateLimiter(max_hits=3, window_seconds=60)
        assert limiter.check("k") is None
        assert limiter.check("k") is None
        assert limiter.check("k") is None
        retry = limiter.check("k")
        assert retry is not None and retry > 0

    def test_buckets_are_independent(self) -> None:
        limiter = security.RateLimiter(max_hits=1, window_seconds=60)
        assert limiter.check("a") is None
        assert limiter.check("a") is not None
        assert limiter.check("b") is None  # a different key is unaffected

    def test_reset_clears_a_bucket(self) -> None:
        limiter = security.RateLimiter(max_hits=1, window_seconds=60)
        limiter.check("k")
        assert limiter.check("k") is not None
        limiter.reset("k")
        assert limiter.check("k") is None


class TestMoneyIsDecimal:
    """ADR-017: the Boltz API returns money as strings; floats would drift."""

    def test_budget_rejects_floats(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 - pydantic wraps the TypeError
            Budget(limit_usd=25.0)  # type: ignore[arg-type]

    def test_budget_accepts_strings_exactly(self) -> None:
        b = Budget(limit_usd="25", spent_usd="0.05", reserved_usd="0.10")
        assert b.limit_usd == Decimal("25")
        assert b.committed_usd == Decimal("0.15")
        assert b.remaining_usd == Decimal("24.85")

    def test_decimal_arithmetic_is_exact(self) -> None:
        """The case that motivated this: 0.05 x 3 is 0.15, not 0.15000000000000002."""
        b = Budget(limit_usd="1", spent_usd="0.05")
        total = b.spent_usd * 3
        assert total == Decimal("0.15")
        assert str(total) == "0.15"

    def test_can_afford_boundary(self) -> None:
        b = Budget(limit_usd="5", spent_usd="4.95")
        assert b.can_afford(Decimal("0.05")) is True
        assert b.can_afford(Decimal("0.06")) is False

    def test_can_afford_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            Budget(limit_usd="5").can_afford(Decimal("-1"))

    def test_settings_budget_rejects_float(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            Settings(default_project_budget_usd=25.0)  # type: ignore[arg-type]


class TestRankingWeights:
    def test_defaults_sum_to_one(self) -> None:
        assert sum(RankingWeights().as_mapping().values()) == Decimal("1.00")

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            RankingWeights(iptm="-0.1")

    def test_all_zero_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            RankingWeights(
                binding_confidence="0",
                iptm="0",
                ipsae_min="0",
                min_interaction_pae="0",
                structure_confidence="0",
            )


class TestBoltzKeyModeGuard:
    """ADR-017: a live key in dev burns credits; a test key in prod fabricates science."""

    def test_detects_test_and_live_prefixes(self) -> None:
        assert Settings(boltz_api_key="sk_bc_ws_test_abc").boltz_key_mode() == "test"
        assert Settings(boltz_api_key="sk_bc_ws_live_abc").boltz_key_mode() == "live"
        assert Settings(boltz_api_key="sk_bc_admin_test_abc").boltz_key_mode() == "test"
        assert Settings(boltz_api_key="nonsense").boltz_key_mode() == "unknown"

    def test_live_key_in_dev_refuses_to_start(self) -> None:
        s = Settings(env=Env.dev, boltz_api_key="sk_bc_ws_live_abc")
        with pytest.raises(RuntimeError, match="LIVE key"):
            s.assert_key_mode_matches_env()

    def test_test_key_in_prod_refuses_to_start(self) -> None:
        s = Settings(env=Env.prod, boltz_api_key="sk_bc_ws_test_abc")
        with pytest.raises(RuntimeError, match="TEST key"):
            s.assert_key_mode_matches_env()

    def test_matching_combinations_are_fine(self) -> None:
        Settings(env=Env.dev, boltz_api_key="sk_bc_ws_test_abc").assert_key_mode_matches_env()
        Settings(env=Env.prod, boltz_api_key="sk_bc_ws_live_abc").assert_key_mode_matches_env()
        # No key configured yet must not block startup.
        Settings(env=Env.dev, boltz_api_key="").assert_key_mode_matches_env()
