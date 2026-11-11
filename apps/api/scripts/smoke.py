"""End-to-end smoke test against a RUNNING Biostruct API.

Unit and integration tests exercise the app in-process. This drives the real
HTTP surface of a deployed container: cookies, CSRF, status codes and all. It is
what `ship-verify` runs before and after a deploy.

Usage:
    python scripts/smoke.py [base_url]     # default http://localhost:8000
    SMOKE_BASE_URL=https://... python scripts/smoke.py

Exits non-zero if any check fails, so CI and the ship gate can depend on it.
Uses only the standard library, so it runs anywhere the API image runs.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any

PASSWORD = "ribosome velvet cartography"

ROOT = (
    sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SMOKE_BASE_URL", "http://localhost:8000")
).rstrip("/")
BASE = f"{ROOT}/api/v1"

_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))

_failures: list[str] = []


def _csrf() -> str | None:
    return next((c.value for c in _jar if c.name == "biostruct_csrf"), None)


def call(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    # Read the CSRF cookie on every request, exactly as the web client does.
    token = _csrf()
    if token:
        req.add_header("x-csrf-token", token)
    try:
        with _opener.open(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else None)
    except urllib.error.URLError as e:
        print(f"  cannot reach {ROOT}: {e.reason}")
        raise SystemExit(2) from e


def check(label: str, got: object, want: object) -> None:
    passed = got == want
    if not passed:
        _failures.append(f"{label}: got {got}, expected {want}")
    print(f"{'PASS' if passed else 'FAIL'}  {label}: {got} (expected {want})")


def note(message: str) -> None:
    print(f"      {message}")


def main() -> int:
    print(f"Smoke testing {ROOT}\n")
    email = f"smoke-{uuid.uuid4().hex[:8]}@example.org"

    status, _ = call("GET", "/auth/csrf")
    check("get csrf token", status, 200)

    status, _ = call("GET", "/auth/me")
    check("me before sign-in is 401", status, 401)

    status, _ = call(
        "POST",
        "/auth/register",
        {"email": email, "name": "Smoke Test", "password": PASSWORD},
    )
    check("register", status, 201)

    status, me = call("GET", "/auth/me")
    check("me after register", status, 200)
    if me:
        note(f"signed in as {me['email']} role={me['role']}")
        if "password" in json.dumps(me).lower():
            _failures.append("a password field leaked into /auth/me")

    status, project = call(
        "POST",
        "/projects",
        {
            "name": "KPC-2 nanobody binders",
            "infection": {
                "name": "Carbapenem-resistant Klebsiella pneumoniae",
                "pathogen": "Klebsiella pneumoniae",
                "amr_context": "KPC-2 carbapenemase",
            },
        },
    )
    check("create project", status, 201)
    if not project:
        print("\nSMOKE TEST FAILED (no project returned)")
        return 1
    note(
        f"budget_limit={project['budget']['limit_usd']} "
        f"binding_confidence_weight={project['ranking_weights']['binding_confidence']}"
    )
    project_id = project["id"]

    status, listing = call("GET", "/projects")
    check("list projects", status, 200)
    if listing:
        check("list contains the new project", listing["total"], 1)

    status, _ = call(
        "PUT",
        f"/projects/{project_id}/ranking-weights",
        {
            "binding_confidence": "0.5",
            "iptm": "0.2",
            "ipsae_min": "0.2",
            "min_interaction_pae": "0.05",
            "structure_confidence": "0.05",
        },
    )
    check("re-rank with new weights", status, 200)

    status, _ = call("POST", "/auth/logout")
    check("logout", status, 204)

    status, _ = call("GET", "/auth/me")
    check("me after logout is 401", status, 401)

    status, _ = call("GET", f"/projects/{project_id}")
    check("project unreachable after logout", status, 401)

    # Regression guard: logout used to clear the CSRF cookie, which made the
    # very next sign-in fail with a 403 until the page was reloaded.
    status, _ = call("POST", "/auth/login", {"email": email, "password": PASSWORD})
    check("sign in immediately after logout", status, 200)

    status, _ = call("DELETE", f"/projects/{project_id}")
    check("delete project", status, 204)

    print()
    if _failures:
        print(f"SMOKE TEST FAILED ({len(_failures)} problem(s))")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
