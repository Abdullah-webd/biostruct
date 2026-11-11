# Credentials — what's needed, when, and how to get it

**Short version: two keys.** Everything else is either free, local, or not needed until we deploy.

| Key | Where to get it |
|---|---|
| `BOLTZ_API_KEY` | **https://api.boltz.com/console** — sign up, create a key, free credits included |
| `OPENAI_API_KEY` | **https://platform.openai.com/api-keys** |

## How to hand them over

Open the `.env` file in the project root (already created, already gitignored) and paste each key
after the `=`. No quotes, no spaces.

```
BOLTZ_API_KEY=sk_bc_ws_test_xxxxxxxxxxxx
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxx
```

**Do not paste keys into a chat message.** Anything typed into a conversation is stored in that
conversation's history. A key in a file read at runtime is not. If a key ever does get pasted
somewhere it shouldn't, rotate it — both providers let you revoke and reissue in seconds.

---

## Required now

| # | Credential | Needed from | Cost | Status |
|---|---|---|---|---|
| 1 | `BOLTZ_API_KEY` | **M1** (first milestone) | Free — test-mode keys are unbilled | [api.boltz.com/console](https://api.boltz.com/console) |
| 2 | `OPENAI_API_KEY` | M5 (agents) | ~$30–50/mo at launch; pennies while building | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |

### 1. Boltz API key

**Sign up at → https://api.boltz.com/console**

Their quickstart says: *"After signing up for an account in the API Console, you'll be granted
credits so you can test the API for free."* The credit amount isn't stated.

1. Open **https://api.boltz.com/console** and create an account.
2. Create an API key. **The full key is shown only once, at creation time** — copy it immediately.
3. Paste it into `.env` as `BOLTZ_API_KEY=`.

> The real API documentation lives at **https://api.boltz.com/docs**. That is a completely
> different site from `docs.boltz.com`, which only documents Boltz Lab (their web product) and has
> no API or authentication pages at all. An easy trap — we fell into it on the first pass and
> wrongly concluded there was no self-serve signup.

#### Start with a TEST key, not a live one

Keys carry a prefix saying what they are:

| Prefix | What it is |
|---|---|
| `sk_bc_ws_test_` | workspace, **test mode** |
| `sk_bc_ws_live_` | workspace, live |
| `sk_bc_admin_test_` | admin, test mode |
| `sk_bc_admin_live_` | admin, live |

**Test-mode keys are free.** Boltz documents them as *"no GPU usage, and no billing"*, returning
synthetic results. That is ideal for building: we can develop and test the whole platform against a
test key without spending credits or waiting on real GPU runs, and switch to a live key only for
the few genuine runs in M1 and the demo.

**Use workspace keys, not admin keys,** for the app. Admin keys can create and revoke other keys
(`POST /compute/v1/admin/api-keys`) and set spending limits — more power than the app needs.

#### Technical details, for the record

| | |
|---|---|
| Base URL | `https://api.boltz.bio` |
| Auth header | `x-api-key: <your-api-key>` |
| Env var | `BOLTZ_API_KEY` |
| Python | `pip install boltz-api` → `Boltz(base_url="https://api.boltz.bio", api_key=...)` |
| TypeScript | `npm install boltz-api` |
| CLI | `curl -fsSL https://install.boltz.bio/boltz-api/install.sh | sh` |

Note the dashboard is `api.boltz.**com**/console` but the API base URL is `api.boltz.**bio**` —
both domains are live, and that is not a typo.

#### If the console gives you trouble

There is a second route needing **no API key at all** — the CLI logs in over OAuth:

```bash
boltz-api --auth-issuer-url "https://lab.boltz.bio" auth login
# on a remote machine, or if the browser flow fails:
boltz-api --auth-issuer-url "https://lab.boltz.bio" auth login --device-code
```

A useful fallback, though the app itself uses a key.

#### Still unconfirmed

- Whether the **200 free predictions/month** figure applies to API usage. It appears on the *Boltz
  Lab* pricing page; the API quickstart only says "you'll be granted credits", with no number.
  Verified in M1 via `client.auth.me()` and `GET /compute/v1/admin/usage` — both free calls.
- Whether a card is needed. No API page says so, and test mode is explicitly unbilled. Their Lab
  billing page mentions payment methods only for on-demand usage above the free allowance.
- No academic plan or academic credits documented anywhere.
- No OpenAPI spec published — `api.boltz.com/openapi.json` returns the console shell, not a spec.
  Hence pinning the SDK and contract-testing against recorded fixtures (ARCHITECTURE.md §6.1).

---

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

## Not needed until we deploy

Local development needs **no external accounts at all** — MongoDB runs in Docker and structure
files go to local disk (`STORAGE_BACKEND=local`). These only matter when the app goes online:

| Service | For | Cost | How |
|---|---|---|---|
| **Railway** | API + worker + object storage | ~$10–15/mo | `railway login` (creates an account on the fly) — no key to paste |
| **MongoDB Atlas** | hosted database | Free cluster to start | Account + a database user; gives a `MONGODB_URI` |
| **Vercel** | frontend hosting | Hobby free | `vercel login` — no key to paste |

### Railway

Railway holds three things in one project, so there is no extra storage vendor:

| Service | What |
|---|---|
| `api` | FastAPI |
| `worker` | the reconciler |
| `structures` | a **Bucket** — S3-compatible object storage |

Sign in and deploy in one step from the project root:

```bash
railway up      # signs you in if needed, creates the project, deploys
```

Then create the bucket and read its credentials:

```bash
railway add --service worker --json
railway bucket credentials --bucket structures --json
```

That returns an S3 endpoint, access key id and secret, which map onto `S3_ENDPOINT_URL`,
`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET`. Set them as Railway environment variables
rather than in a local file.

> The Railway MCP connector is not authorised in this workspace yet. It isn't required — the CLI
> does everything we need. To enable it anyway, authorise Railway in your claude.ai connector
> settings.

### MongoDB Atlas

Create a **free cluster**, add a database user, allow network access, and copy the connection
string into `MONGODB_URI`.

We use Atlas rather than Railway's managed MongoDB for one specific reason: **change streams
require a replica set**, and Atlas always runs as one. Railway's Mongo is a single container and its
replica-set support is unverified — we check in M0, and if it works we can consolidate onto Railway.

## Generated, not obtained

`SESSION_SECRET` is already in your `.env`, generated with `secrets.token_urlsafe(48)`. Nothing to do.

---

## Running it locally

`make dev` runs the API natively and is the path to prefer.

If your Docker is **Colima**, Rancher Desktop or Podman machine, containers run
inside a Linux VM and published ports do not necessarily reach macOS. The
container reports healthy, `docker port` shows `0.0.0.0:8000`, and
`curl http://localhost:8000` still fails — which is indistinguishable from a
broken application. Confirmed on this project with Colima on
Virtualization.Framework.

`make test` keeps using Docker regardless, because the suite needs a MongoDB
replica set and reaches it over the container network rather than the host.

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
