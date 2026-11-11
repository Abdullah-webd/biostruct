# Credentials — what's needed, when, and how to get it

**Short version: two keys, and you already have both.** Everything else is either free, local, or
not needed until we deploy.

## How to hand them over

Open the `.env` file in the project root (already created, already gitignored) and paste each key
after the `=`. No quotes, no spaces.

```
BOLTZ_API_KEY=bz_xxxxxxxxxxxx
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxx
```

**Do not paste keys into a chat message.** Anything typed into a conversation is stored in that
conversation's history. A key in a file read at runtime is not. If a key ever does get pasted
somewhere it shouldn't, rotate it — both providers let you revoke and reissue in seconds.

---

## Required now

| # | Credential | Needed from | Cost | Status |
|---|---|---|---|---|
| 1 | `BOLTZ_API_KEY` | **M1** (first milestone) | Free tier covers the whole build | ✅ you have it |
| 2 | `OPENAI_API_KEY` | M5 (agents) | ~$30–50/mo at launch; pennies while building | ✅ you have it |

### 1. Boltz API key

You already have this, so nothing to do. For the record, if it ever needs reissuing: Boltz has
**no publicly documented self-serve key flow.** `docs.boltz.com` covers only Boltz Lab (the web
product), there is no API-key or authentication page, and `api.boltz.com/console` is a JavaScript
app shell that returns no readable content to a non-browser client. Boltz Lab itself is in beta
with access by request form, and sign-in instructions arrive by email.

So: keep the key you have somewhere safe, and if you need another, email **support@boltz.bio**
(listed on their billing docs) rather than hunting for a signup page.

**Two things to check once, in M1** — both are free API calls, so they cost nothing to answer:
- Does the **200 free predictions/month** allowance apply to **API** usage, or only to Boltz Lab?
  The figure appears on the Lab pricing table and is *not* repeated on the API page. Unconfirmed.
- How much of this month's allowance is left?

`client.auth.me()` and `GET /compute/v1/admin/usage` answer both. We call them before spending
anything.

### 2. OpenAI API key

Already have it. If you need another: **https://platform.openai.com/api-keys** → *Create new
secret key*. It's shown once, so copy it immediately.

One thing to verify before M7: **Realtime voice needs a funded account.** Check
*Settings → Billing* has credit. Text agents are cheap, but voice is ~$0.10/minute, so a zero
balance will fail at the voice milestone rather than earlier.

---

## Optional, free, nice to have

| Credential | What it buys | How |
|---|---|---|
| `NCBI_EMAIL` | NCBI asks API callers to identify themselves. Costs nothing, just put your email in. | Any email you're happy sending to NCBI |
| `NCBI_API_KEY` | Raises PubMed from **3 requests/sec to 10**. We work fine at 3 with caching. | Free: [NCBI account](https://account.ncbi.nlm.nih.gov/) → *Account Settings* → *API Key Management* |

---

## Not needed until we deploy (milestone M0 deploy / M8)

Local development needs **no external accounts at all** — MongoDB runs in Docker, and structure
files go to local disk (`STORAGE_BACKEND=local`). These only matter when the app goes on the
internet:

| Service | For | Cost | Note |
|---|---|---|---|
| **MongoDB Atlas** | hosted database | Free cluster is enough to start | Free clusters support change streams (we need them), capped at 100 ops/sec |
| **Cloudflare R2** | structure file storage | 10 GB + 1M writes + 10M reads/month free, **egress free** | ⚠️ Requires completing an **R2 subscription checkout** in the dashboard before you can create a token. Their docs don't say whether a card is required — worth checking at that point. This is why dev uses local disk instead. |
| **Vercel** | frontend hosting | Hobby tier free | `vercel login`, no key to paste |
| **Fly.io** | backend + worker hosting | ~$10/mo | `fly auth login`, no key to paste |

### Cloudflare R2, when we get there

1. Dashboard → **Storage & databases → R2 → Overview**
2. Complete the **R2 subscription** checkout (required before tokens can be made)
3. **Account Details → API Tokens → Manage → Create Account API token**
4. Permission: **Object Read & Write**, scoped to our bucket only — not Admin
5. Record the **Access Key ID** and **Secret Access Key**. The secret is shown **once**.
6. Account ID comes from the dashboard separately.

S3-compatible endpoint: `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`

Egress being free is the reason we picked R2 — we re-serve the same structure files to every
viewer, which on AWS S3 would be billed every time.

---

## Generated, not obtained

`SESSION_SECRET` is already in your `.env`, generated with `secrets.token_urlsafe(48)`. Nothing to do.

---

## Security rules for this project

- `.env` is gitignored; `.env.example` holds names only, never values. Confirmed with
  `git check-ignore`.
- **No science key ever reaches the browser.** The only credential a user's browser ever sees is a
  600-second OpenAI ephemeral voice token, scoped to one session (ARCHITECTURE.md §11).
- Agents SDK tracing defaults to **off** (`OPENAI_AGENTS_DISABLE_TRACING=1`). On by default it
  would send run content — including unpublished target data — to OpenAI's dashboard.
- CI runs a secret scan, so an accidentally committed key fails the build rather than sitting in
  history.
- If a key leaks: revoke first, then investigate. Both providers reissue instantly.
