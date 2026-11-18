"""Application settings, loaded from the environment.

Two rules this module enforces, both learned the hard way (ARCHITECTURE.md ADR-017):

* Money is ``Decimal``, never ``float``. The Boltz API returns costs as strings
  ("0.0500") and float arithmetic would quietly drift our spend tracking.
* A Boltz key announces its own mode ("test" or "live"). We refuse to start if the
  key's mode does not match the environment, because a live key in development
  burns real credits and a test key in production returns fabricated science.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(StrEnum):
    dev = "dev"
    staging = "staging"
    prod = "prod"
    test = "test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Env = Env.dev

    # --- data stores -------------------------------------------------------
    mongodb_uri: str = "mongodb://localhost:27017/biostruct?directConnection=true"
    mongodb_db: str = "biostruct"

    # --- external services -------------------------------------------------
    #: Run a LIVE Boltz key outside production on purpose — a demo on
    #: localhost, where ENV=prod cannot be used because it forces secure
    #: cookies and sign-in then fails over plain http. Deliberately a separate
    #: switch from the environment, so real spending is always an explicit
    #: choice rather than a side effect of a deployment setting.
    #: Whether anyone may create an account. Off on the public demo, where a
    #: live engine key means a new account can spend real credits.
    allow_registration: bool = True
    boltz_allow_live: bool = False
    boltz_api_key: SecretStr = SecretStr("")
    boltz_base_url: str = "https://api.boltz.bio"
    openai_api_key: SecretStr = SecretStr("")
    #: African-language speech (https://docs.spitch.app). Input only: a
    #: researcher speaks their own language, the answer comes back in English.
    spitch_api_key: SecretStr = SecretStr("")
    openai_realtime_model: str = "gpt-realtime-2.1"
    #: The orchestrator's model. "Near-Astra performance for complex work at a
    #: lower cost" at $2/$10 per MTok, verified on the live API 2026-11-17. The
    #: agent chooses which scientific tool to call and a wrong call spends real
    #: money on the wrong protein, so this is not a place for the cheapest
    #: model. See docs/openai-api-observed.md.
    agent_model: str = "gpt-6.1-sol"
    #: "none" is rejected by this model family; "medium" is the API default.
    agent_reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    openai_agents_disable_tracing: bool = True

    # --- storage -----------------------------------------------------------
    storage_backend: Literal["local", "s3", "mongo"] = "local"
    storage_local_path: str = "./data/structures"
    s3_endpoint_url: str = ""
    s3_access_key_id: SecretStr = SecretStr("")
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_bucket: str = "structures"

    # --- app ---------------------------------------------------------------
    session_secret: SecretStr = SecretStr("dev-only-insecure-secret-change-me")
    app_base_url: str = "http://localhost:3000"
    session_ttl_days: int = 30
    default_project_budget_usd: Decimal = Decimal("25")

    # When false the API does not run the reconciler in-process, which is how
    # production separates the web service from the worker service.
    run_reconciler: bool = True

    # --- science sources ---------------------------------------------------
    ncbi_email: str = ""
    ncbi_api_key: SecretStr = SecretStr("")

    @field_validator("default_project_budget_usd", mode="before")
    @classmethod
    def _money_as_decimal(cls, v: object) -> Decimal:
        # Accept strings and ints; never route money through float.
        if isinstance(v, Decimal):
            return v
        if isinstance(v, float):
            raise TypeError("budget must not be a float; use a string or Decimal")
        return Decimal(str(v))

    @property
    def is_production(self) -> bool:
        return self.env in (Env.prod, Env.staging)

    @property
    def cookie_secure(self) -> bool:
        return self.is_production

    def boltz_key_mode(self) -> Literal["test", "live", "unknown"]:
        """Infer the key's mode from its prefix, without calling the API.

        Verified prefixes (docs/research.md §2): sk_bc_ws_test_, sk_bc_ws_live_,
        sk_bc_admin_test_, sk_bc_admin_live_.
        """
        key = self.boltz_api_key.get_secret_value()
        if "_test_" in key:
            return "test"
        if "_live_" in key:
            return "live"
        return "unknown"

    def assert_key_mode_matches_env(self) -> None:
        """Fail fast on a key/environment mismatch.

        Both directions are expensive mistakes, so neither is a warning.
        """
        mode = self.boltz_key_mode()
        if mode == "unknown":
            return  # no key configured yet, or an unrecognised prefix; other checks will catch it
        if self.is_production and mode == "test":
            raise RuntimeError(
                f"Refusing to start: env={self.env} but BOLTZ_API_KEY is a TEST key. "
                "Test keys return synthetic results, which must never reach researchers."
            )
        if not self.is_production and mode == "live" and not self.boltz_allow_live:
            raise RuntimeError(
                f"Refusing to start: env={self.env} but BOLTZ_API_KEY is a LIVE key. "
                "Development would spend real credits. Use a sk_bc_ws_test_ key, or set "
                "BOLTZ_ALLOW_LIVE=1 if you mean it (a demo on localhost, say). "
                "ENV=prod also works but forces secure cookies, which breaks sign-in "
                "over plain http."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings_field = Field  # re-exported for routers that build their own models
