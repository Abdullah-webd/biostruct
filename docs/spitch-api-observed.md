# Spitch as it actually behaves

Observed against the live API on **2026-11-17** with the project key, by
generating Yorùbá and English speech, transcribing it back, translating, and
listing voices. Docs: https://docs.spitch.app. Base `https://api.spitch.app/v1`,
auth `Authorization: Bearer $SPITCH_API_KEY`.

Spitch is a Nigerian voice-AI company building speech models for African
languages — the reason it is in this product rather than a US speech vendor.

---

## Verified working

| Call | Result |
|---|---|
| `POST /v1/speech` (Yorùbá, voice `sade`) | 200, `content-type: audio/wav`, 163 KB |
| `POST /v1/speech` (English, voice `lucy`) | 200, 346 KB |
| `POST /v1/transcriptions` (multipart) | 200, correct Yorùbá transcript |
| `POST /v1/translate` (en→yo) | 200 |
| `GET /v1/voices` | 200, system voices listed |

### The transcription endpoint path is not in the docs

The documentation describes `client.speech.transcribe(...)` in the SDKs but
never states the REST path. **It is `POST /v1/transcriptions`**, multipart, with
the audio as the `content` part and `language` as a form field.

### The response carries `detected_language`, which the docs do not mention

```json
{
  "request_id": "6972f8a9-…",
  "text": "arun ti ogun ko le wo a ń wá ogun tuntun",
  "segments": null,
  "timestamps": null,
  "detected_language": "yo"
}
```

So **language detection does happen** and is reported back. We can let a user
speak without choosing a language first, then show them which one we heard.

### Round trip loses some diacritics

Sent to TTS: `Àrùn tí òògùn kò lè wò. A ń wá òògùn tuntun.`
Came back from STT: `arun ti ogun ko le wo a ń wá ogun tuntun`

Recognisable, and tone marks are partly dropped. The `/v1/diacritics` endpoint
restores Yorùbá tone marks and is worth applying to transcripts we display.

### Translation keeps English scientific terms

`"Which protein is making this infection resistant?"` → `"Èwo ni protein tó ń
jẹ́ kí àkóràn yìí kò fara mọ́?"`

It kept **"protein"** in English, because there is no Yorùbá word for it. This
is correct behaviour and tells us something important: African-language support
here means *the conversation* is in the user's language while the *scientific
nouns stay in English*, which is how these researchers actually speak.

---

## Catalogue

**Languages: six, not "any".** English, Yorùbá (`yo`), Hausa (`ha`), Igbo
(`ig`), Amharic (`am`), Nigerian Pidgin. Codes are ISO 639 two-letter.

**Voices** (`voice` takes the lowercase name; `language` on the voice record is
`null`, and the docs state any voice can speak any supported language):

| Language | Voices |
|---|---|
| Yorùbá | sade (F), funmi (F), segun (M), femi (M) |
| Hausa | amina (F), zainab (F), hasan (M), aliyu (M) |
| Igbo | ngozi (F), amara (F), obinna (M), ebuka (M) |
| English | lucy (F), lina (F), kani (F), remi (F), john (M), jude (M), henry (M), kingsley (M) |
| Amharic | hana (F), tena (F), haile (M), tesfaye (M) |
| Nigerian Pidgin | boma (F), ufoma (F), justice (M), tega (M) |

**Endpoints**

| Purpose | Call |
|---|---|
| Speak | `POST /v1/speech` — JSON `{text, voice, language?, speed?, format?}`, returns audio bytes |
| Transcribe | `POST /v1/transcriptions` — multipart `content` + `language?`, returns `{text, detected_language}` |
| Translate | `POST /v1/translate` — `{text, target, source?, tone?, formality?}` |
| Yorùbá tone marks | `POST /v1/diacritics` — `{language: "yo", text}` |
| Voices | `GET /v1/voices`, `POST /v1/voices` (custom, needs `consent: true`) |

`speed` 0.7–1.2. `format`: wav (default), mp3, ogg_opus, webm_opus, flac,
pcm_s16le, mulaw, alaw. STT accepts mp3/wav/m4a/ogg, max 25 MB.

**SDKs:** `pip install spitch` / `npm install spitch`. Both read
`SPITCH_API_KEY`. A LiveKit plugin exists (`livekit-agents[spitch]`) for
realtime voice agents — not needed for push-to-talk.

---

## Pricing, and why voice must be metered

| Operation | Rate | In practice |
|---|---|---|
| Speech generation | **$0.0014 / second** | a 30-second answer read aloud = **$0.042** |
| Transcription | **$0.00042 / second** | a 20-second question = $0.008 |
| Translation | $1 / 10,000 words | negligible |
| Tone marking | $1 / 10,000 words | negligible |

**Reading one answer aloud costs more than folding a protein** ($0.042 vs
$0.025). Voice therefore goes on the same budget ledger as the science, with its
own line, and read-aloud is opt-in per message rather than automatic.

New accounts get **$1 of free credit ≈ 700 seconds of speech**, which is about
twelve minutes of read-aloud. Enough for a demo, not for a day of real use.

---

## Three design consequences

### 1. A transcript is a draft, never a command

Scientific vocabulary — *carbapenemase*, *beta-lactamase*, *Klebsiella* — is
not what a general speech model is trained on, and in a Yorùbá or Hausa
sentence it will sometimes come back wrong. So the microphone **fills the
composer and stops**. The user sees what we heard, with the detected language,
edits if needed, and presses send themselves. We never pipe a transcription
straight into the agent.

### 2. Read-aloud must not read the message

Our messages contain SMILES strings (`NC(=O)[C@@H]1CC[C@@H]2CN1C(=O)N2OS(=O)(=O)O`),
accession codes and four-decimal metrics. Spoken literally that is noise.

Every assistant message therefore carries a **separate `spoken` field** — one or
two sentences of plain language the model writes alongside its answer, naming no
identifiers and no raw numbers beyond a rounded percentage. The read-aloud
button plays that, not the message body. The agent is instructed to write it as
if explaining to a colleague over the phone.

### 3. "Any language" is not what we have

Six languages, all African or Nigerian English. That is a far stronger and more
honest claim than "any language", and it is the actual point: these are the
languages spoken in the hospitals where these infections kill people. The UI
names the six rather than implying a universal translator.

---

## The full loop, measured 2026-11-17

This is the product, not an accessory: a researcher works **entirely in their
own language**. Measured end to end with a real Yorùbá question.

```
researcher speaks Yorùbá
  → POST /v1/transcriptions      detected_language: "yo"
  → POST /v1/translate yo→en     English pivot
  → agent reasons in English, calls Boltz/UniProt/Europe PMC
  → POST /v1/translate en→yo     answer in Yorùbá
  → POST /v1/diacritics          tone marks restored
  → POST /v1/speech              answer spoken in Yorùbá
```

### Observed, verbatim

| Step | Result |
|---|---|
| Spoken | `Kí ni ìdí tí àwọn òògùn apakòkòrò kò fi ń ṣiṣẹ́ lórí àrùn Klebsiella mọ́?` |
| Heard | `…lórí àrùn **cleps ella** mọ́` ← mis-heard |
| Translated to English | `Why don't antibiotics work on **Klebsiella** anymore?` ← **recovered** |
| Answer in Yorùbá | `Klebsiella pneumoniae máa ń ṣe ensaemusi kan tí a ń pè ní KPC carbapenemase. Ó máa ń fọ àpòrò-ara náà káàkiri kí oògùn náà tó lè ṣiṣẹ́.` |
| Hausa | `Klebsiella pneumoniae tana samar da wani enzyme da ake kira KPC carbapenemase…` |
| Igbo | `Klebsiella pneumoniae na-emepụta ensaem a na-akpọ KPC carbapenemase…` |

### Three findings

**1. Translation repairs mis-heard scientific terms.** STT returned
`cleps ella`; the translator returned `Klebsiella`. It has enough context to
fix what the speech model mangled. This does not remove the need to show the
user their transcript, but it means the pipeline is more robust than the raw
STT accuracy suggests.

**2. Scientific nouns survive translation correctly.** `KPC carbapenemase`
stayed in English; `enzyme` became `ensaemusi` and `molecule` became
`molikula` — transliterations a Yorùbá speaker reads naturally. Hausa kept
`enzyme`, Igbo used `ensaem`. This is how these researchers actually speak and
we did nothing to arrange it.

**3. English is the right pivot, and the user never sees it.** The agent
reasons, calls tools and reads papers in English, where both the model and the
literature are strongest. Translation happens at the edges only. The researcher
asks and is answered in their own language throughout.

### What stays in English, honestly

Papers are written in English, so citation titles and abstracts stay English
and are labelled as such. We translate the agent's *explanation* of a paper,
never fake a translated title.
