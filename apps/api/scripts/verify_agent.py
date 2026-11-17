"""Live acceptance for M7: the real model, the real sources, a real turn.

Run from apps/api with the venv:  .venv/bin/python scripts/verify_agent.py
Costs a few cents of OpenAI tokens. Spends nothing on Boltz: paid tools only
ever produce proposals.
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

from biostruct.agent.llm import OpenAIClient
from biostruct.agent.orchestrator import (
    Completed,
    Delta,
    Failed,
    Orchestrator,
    ProposalRaised,
    ToolFinished,
    ToolStarted,
)
from biostruct.agent.tools.base import ToolContext
from biostruct.agent.tools.science import build_registry
from biostruct.db import ensure_indexes
from biostruct.models.conversation import Block, MessageCreate, Role, SessionCreate
from biostruct.repositories.conversations import ConversationRepository
from biostruct.sources import ScienceSources
from biostruct.sources.chembl import ChemblClient
from biostruct.sources.europepmc import EuropePMCClient
from biostruct.sources.http import CachedHTTPClient

USER = "live-verify"
QUESTIONS = [
    "I have an ICU outbreak of Klebsiella pneumoniae that no longer responds to "
    "meropenem. What enzyme is likely responsible, and what does the literature say?",
    "Build me a library to screen against it, and tell me what it would cost.",
]


async def main() -> int:
    uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017/?directConnection=true")
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        print("OPENAI_API_KEY is not set")
        return 1

    client = AsyncIOMotorClient(uri, tz_aware=True)
    db = client["biostruct_agent_live"]
    for c in ("research_sessions", "messages", "artifacts", "source_cache"):
        await db[c].delete_many({})
    await ensure_indexes(db)

    sources = ScienceSources(db=db, ncbi_email="", ncbi_api_key="")
    europepmc = EuropePMCClient(CachedHTTPClient(source="europepmc", db=db, rate_per_second=3.0))
    chembl = ChemblClient(
        CachedHTTPClient(source="chembl", db=db, rate_per_second=2.0, timeout=30.0)
    )
    llm = OpenAIClient(api_key=key, client=httpx.AsyncClient(timeout=180.0))
    repo = ConversationRepository(db)
    orch = Orchestrator(llm=llm, registry=build_registry(), conversations=repo, europepmc=europepmc)

    print(f"model: {llm.model}\n")
    session = await repo.create_session(
        user_id=USER, data=SessionCreate(title="Live check"), default_budget_usd=Decimal("25")
    )
    total = Decimal("0")
    proposals = 0

    for n, question in enumerate(QUESTIONS, 1):
        print(f"{'=' * 78}\nQ{n}: {question}\n{'=' * 78}")
        await repo.append_message(
            session_id=session.id,
            user_id=USER,
            data=MessageCreate(role=Role.user, blocks=[Block(text=question)]),
        )
        ctx = ToolContext(
            user_id=USER,
            session_id=session.id,
            sources=sources,
            chembl=chembl,
            europepmc=europepmc,
        )
        wrote = False
        async for event in orch.run_turn(session_id=session.id, user_id=USER, ctx=ctx):
            if isinstance(event, ToolStarted):
                tag = " [PAID -> proposal only]" if event.spend == "paid" else ""
                print(f"\n  >> tool: {event.name}{tag}")
            elif isinstance(event, ToolFinished):
                first = event.summary.splitlines()[0] if event.summary else ""
                print(f"     {'ok' if event.ok else 'FAILED'}: {first[:110]}")
            elif isinstance(event, ProposalRaised):
                proposals += 1
                p = event.proposal
                print(f"\n  $$ PROPOSAL: {p['title']} — ${p['estimate_usd']} (not started)")
            elif isinstance(event, Delta):
                if not wrote:
                    print("\n  --- answer ---")
                    wrote = True
                print(event.text, end="", flush=True)
            elif isinstance(event, Completed):
                total += Decimal(event.cost_usd)
                print(f"\n\n  references ({len(event.references)}):")
                for r in event.references:
                    note = f" [{r['note']}]" if r["note"] else ""
                    print(f"    [{r['n']}] {r['citation'][:92]}{note}")
                    print(f"        {r['url']}")
                if event.warning:
                    print(f"  WARNING: {event.warning}")
                for s in event.flagged:
                    print(f"  FLAGGED: {s[:100]}")
                print(f"  tokens: {event.usage} | this turn: ${event.cost_usd}")
            elif isinstance(event, Failed):
                print(f"\n  FAILED: {event.message}")
                return 1
        print()

    messages = await repo.list_messages(session.id, user_id=USER)
    artifacts = await repo.list_artifacts(session.id, user_id=USER)
    print(f"{'=' * 78}")
    print(f"thread: {len(messages)} messages")
    for m in messages:
        label = m.tool_results[0].name if m.tool_results else m.text[:52].replace("\n", " ")
        print(f"  {m.seq:2}. {m.role.value:9} {label}")
    print(f"artifacts: {[a.title for a in artifacts]}")
    print(f"proposals raised: {proposals} | Boltz spend: $0.00 (proposals only)")
    print(f"OpenAI spend: ${total}")

    await db.client.drop_database("biostruct_agent_live")
    await llm.aclose()
    await sources.aclose()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
