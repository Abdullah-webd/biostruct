"""The Spitch voice client.

The shapes here were captured from the live API, including the two things the
documentation does not state: the transcription path, and the
``detected_language`` field in the response.

The behaviour these tests protect is the asymmetry. Speech is an *input*
affordance — a researcher who thinks in Yorùbá should not have to compose a
precise technical question in English — and what reaches the model is always
English, because the translation step is what repairs the scientific nouns the
speech model mis-hears.

No network.
"""

from __future__ import annotations

import httpx
import pytest

from biostruct.voice.spitch import (
    AUDIO_MAX_BYTES,
    LANGUAGES,
    SpitchClient,
    Transcript,
    VoiceUnavailable,
)

#: A real response, captured live. Note ``detected_language``, which the
#: documentation never mentions.
YORUBA_RESPONSE = {
    "request_id": "6972f8a9-0000-0000-0000-000000000000",
    "text": "arun clepsiella yìí kò yá sí oògùn merry pennym mó kí ni enzyn tó ń fà",
    "segments": None,
    "timestamps": None,
    "detected_language": "yo",
}

ENGLISH_TRANSLATION = {
    "text": "This Klebsiella disease is not responding to meropenem; what enzyme is causing it?"
}


def client_for(handler) -> SpitchClient:  # type: ignore[no-untyped-def]
    return SpitchClient(
        api_key="sk-test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def route(mapping: dict[str, object]):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        for fragment, payload in mapping.items():
            if fragment in str(request.url):
                if isinstance(payload, int):
                    return httpx.Response(payload, json={"message": "nope"})
                return httpx.Response(200, json=payload)
        raise AssertionError(f"unexpected request: {request.url}")

    return handler


class TestTheUndocumentedBits:
    @pytest.mark.anyio
    async def test_transcription_posts_to_the_path_we_had_to_discover(self) -> None:
        """The SDKs describe ``client.speech.transcribe(...)`` and never state
        the REST route. It is ``POST /v1/transcriptions``, multipart."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"text": "hello", "detected_language": "en"})

        await client_for(handler).transcribe(b"audio-bytes")
        assert seen[0].url.path.endswith("/transcriptions")
        assert seen[0].method == "POST"
        assert b"multipart/form-data" in seen[0].headers["content-type"].encode()

    @pytest.mark.anyio
    async def test_the_detected_language_is_read_back(self) -> None:
        """So a researcher can press record and talk, rather than choosing a
        language from a menu before they are allowed to speak."""
        transcript = await client_for(
            route({"/transcriptions": YORUBA_RESPONSE, "/translate": ENGLISH_TRANSLATION})
        ).transcribe(b"audio")
        assert transcript.language == "yo"
        assert transcript.language_name == "Yorùbá"


class TestTranslationRepairsScience:
    @pytest.mark.anyio
    async def test_a_non_english_transcript_reaches_the_agent_in_english(self) -> None:
        transcript = await client_for(
            route({"/transcriptions": YORUBA_RESPONSE, "/translate": ENGLISH_TRANSLATION})
        ).transcribe(b"audio")
        assert transcript.translated
        assert transcript.for_agent == ENGLISH_TRANSLATION["text"]

    @pytest.mark.anyio
    async def test_the_mis_heard_original_is_kept_for_the_researcher_to_see(self) -> None:
        """Measured: the speech model heard "clepsiella" and "merry pennym".
        The researcher is shown what we heard so they can tell when it went
        wrong, even though the agent gets the repaired version."""
        transcript = await client_for(
            route({"/transcriptions": YORUBA_RESPONSE, "/translate": ENGLISH_TRANSLATION})
        ).transcribe(b"audio")
        assert "clepsiella" in transcript.text
        assert "Klebsiella" in transcript.for_agent

    @pytest.mark.anyio
    async def test_english_speech_is_not_sent_for_translation(self) -> None:
        """A needless round trip, and a chance to mangle a question that was
        already correct."""
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(
                200, json={"text": "What enzyme is this?", "detected_language": "en"}
            )

        transcript = await client_for(handler).transcribe(b"audio")
        assert not transcript.translated
        assert not any("translate" in p for p in paths)

    @pytest.mark.anyio
    async def test_a_failed_translation_still_returns_the_transcript(self) -> None:
        """A dead translate call must not swallow what the researcher said.
        They can still read it, edit it and send it."""
        transcript = await client_for(
            route({"/transcriptions": YORUBA_RESPONSE, "/translate": 503})
        ).transcribe(b"audio")
        assert transcript.text.startswith("arun")
        assert transcript.for_agent == transcript.text


class TestRefusals:
    @pytest.mark.anyio
    async def test_an_empty_recording_is_refused_before_a_request(self) -> None:
        def explode(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not have been called")

        with pytest.raises(VoiceUnavailable, match="empty"):
            await client_for(explode).transcribe(b"")

    @pytest.mark.anyio
    async def test_an_oversized_recording_is_refused_before_a_request(self) -> None:
        """Spitch bills per second. A stuck recorder uploading an hour of
        silence is a real way to waste money."""

        def explode(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not have been called")

        with pytest.raises(VoiceUnavailable, match="too long"):
            await client_for(explode).transcribe(b"x" * (AUDIO_MAX_BYTES + 1))

    @pytest.mark.anyio
    async def test_silence_is_reported_rather_than_sent_on_as_an_empty_question(self) -> None:
        transcript_handler = route({"/transcriptions": {"text": "  ", "detected_language": "en"}})
        with pytest.raises(VoiceUnavailable, match="Nothing was heard"):
            await client_for(transcript_handler).transcribe(b"audio")

    @pytest.mark.anyio
    async def test_no_key_means_the_feature_is_off_not_broken(self) -> None:
        client = SpitchClient(api_key="")
        assert not client.is_configured
        with pytest.raises(VoiceUnavailable, match="not configured"):
            await client.transcribe(b"audio")

    @pytest.mark.anyio
    async def test_an_api_error_never_echoes_the_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Invalid key sk-test")

        try:
            await client_for(handler).transcribe(b"audio")
        except VoiceUnavailable as exc:
            assert "sk-test" not in str(exc)
        else:
            raise AssertionError("expected a VoiceUnavailable")

    @pytest.mark.anyio
    async def test_an_unsupported_language_hint_is_dropped_rather_than_sent(self) -> None:
        """ "Any language" was never on offer and the product does not claim
        it. A bogus hint is ignored so detection can still do its job."""
        seen: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content)
            return httpx.Response(200, json={"text": "hi", "detected_language": "en"})

        await client_for(handler).transcribe(b"audio", language="klingon")
        assert b"klingon" not in seen[0]


class TestLanguages:
    def test_we_claim_exactly_the_six_languages_spitch_supports(self) -> None:
        assert set(LANGUAGES) == {"en", "yo", "ha", "ig", "am", "pcm"}

    def test_every_language_has_a_name_a_person_would_recognise(self) -> None:
        assert LANGUAGES["yo"] == "Yorùbá"
        assert LANGUAGES["pcm"] == "Nigerian Pidgin"

    def test_an_unknown_code_degrades_to_the_code_itself(self) -> None:
        assert Transcript(text="x", language="zz").language_name == "zz"
