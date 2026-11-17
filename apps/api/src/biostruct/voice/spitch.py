"""Speech in African languages, via Spitch.

**Asymmetric by design: rich on the way in, thin on the way out.**

A Nigerian researcher reads English fluently. The friction is not in reading a
result — it is in composing a precise technical question in a second language
while thinking in a first. So speech is an *input* affordance: say it in
Yorùbá, Hausa, Igbo, Amharic or Pidgin, and the answer comes back as ordinary
English text. Translating the scientific prose back would cost precision and
imply these users need simplifying, which they do not.

Three findings from probing the live API (``docs/spitch-api-observed.md``):

* **The transcription path is undocumented.** The SDKs describe
  ``client.speech.transcribe(...)`` and never state the REST route. It is
  ``POST /v1/transcriptions``, multipart, audio in the ``content`` part.
* **The response carries ``detected_language``**, which the docs do not
  mention. So a researcher can just talk, and we show them what we heard.
* **Translation repairs mis-heard science.** STT rendered "Klebsiella" as
  "cleps ella"; the translation step put it back. That is why a non-English
  transcript is routed through translate rather than sent to the model raw.

Nothing here auto-sends. A speech model mis-hearing an organism must never be
what spends money, so a transcript is a draft the researcher edits and submits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://api.spitch.app/v1"

#: The languages Spitch actually supports. "Any language" was never on offer
#: and the product does not claim it.
LANGUAGES: dict[str, str] = {
    "en": "English",
    "yo": "Yorùbá",
    "ha": "Hausa",
    "ig": "Igbo",
    "am": "Amharic",
    "pcm": "Nigerian Pidgin",
}

#: Spitch bills per second of audio. A researcher dictating a question is
#: speaking for seconds, but a stuck recorder uploading an hour of silence is a
#: real way to waste money, so the upload is capped.
AUDIO_MAX_BYTES = 8 * 1024 * 1024


class VoiceUnavailable(RuntimeError):
    """Speech could not be processed. The product degrades to typing."""


@dataclass(frozen=True, slots=True)
class Transcript:
    """What we heard, and in which language."""

    text: str
    language: str = ""
    english: str = ""
    #: True when the transcript was translated rather than spoken in English.
    translated: bool = False

    @property
    def language_name(self) -> str:
        return LANGUAGES.get(self.language, self.language or "unknown")

    @property
    def for_agent(self) -> str:
        """What the model receives: always English."""
        return self.english or self.text


class SpitchClient:
    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        base_url: str = BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def transcribe(
        self, audio: bytes, *, filename: str = "speech.webm", language: str = ""
    ) -> Transcript:
        """Audio in, English out, with the spoken language reported.

        ``language`` may be left empty: the service detects it and tells us
        which it heard, which is what lets a researcher simply press record
        rather than choosing from a menu first.
        """
        if not self.is_configured:
            raise VoiceUnavailable("Speech is not configured on this server.")
        if not audio:
            raise VoiceUnavailable("The recording was empty.")
        if len(audio) > AUDIO_MAX_BYTES:
            raise VoiceUnavailable("That recording is too long. Keep it under a minute or so.")

        http = await self._http()
        data: dict[str, str] = {}
        if language and language in LANGUAGES:
            data["language"] = language
        try:
            response = await http.post(
                f"{self._base_url}/transcriptions",
                headers=self._headers(),
                files={"content": (filename, audio, "application/octet-stream")},
                data=data,
            )
        except httpx.HTTPError as exc:
            raise VoiceUnavailable(f"Could not reach the speech service: {exc}") from exc
        if response.status_code >= 400:
            raise VoiceUnavailable(self._detail(response))

        payload = response.json()
        if not isinstance(payload, dict):
            raise VoiceUnavailable("The speech service returned an unexpected response.")
        text = str(payload.get("text") or "").strip()
        detected = str(payload.get("detected_language") or language or "").strip()
        if not text:
            raise VoiceUnavailable("Nothing was heard in that recording.")

        if detected and detected != "en":
            english = await self._translate(text, source=detected)
            return Transcript(text=text, language=detected, english=english, translated=True)
        return Transcript(text=text, language=detected or "en", english=text)

    async def _translate(self, text: str, *, source: str) -> str:
        """To English, for the model.

        A failure here is not fatal: the original transcript still goes to the
        researcher to edit, and the model copes with a non-English question
        better than the product copes with a dead microphone button.
        """
        http = await self._http()
        try:
            response = await http.post(
                f"{self._base_url}/translate",
                headers=self._headers(),
                json={"text": text, "source": source, "target": "en"},
            )
            if response.status_code >= 400:
                log.warning("spitch translate failed: %s", self._detail(response))
                return text
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("spitch translate failed: %s", exc)
            return text
        if isinstance(payload, dict):
            return str(payload.get("text") or payload.get("translation") or text).strip() or text
        return text

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        """A readable message that never echoes the key back."""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                for key in ("message", "detail", "error"):
                    if payload.get(key):
                        return f"The speech service refused the request: {payload[key]}"
        except ValueError:
            pass
        return f"The speech service returned HTTP {response.status_code}."


__all__ = [
    "AUDIO_MAX_BYTES",
    "BASE_URL",
    "LANGUAGES",
    "SpitchClient",
    "Transcript",
    "VoiceUnavailable",
]
