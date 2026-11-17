"""The agent layer: tools, guardrails, and the turn loop.

Nothing here talks to a model yet. The guardrails land first, deliberately —
they are the part that must be true regardless of which model runs behind
them, and building them after the loop would mean building them around
whatever the loop happened to do.
"""
