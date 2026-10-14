"""Hermes-native Kumi plugin."""

from __future__ import annotations

def register(ctx) -> None:
    """Register stable Kumi domain tools with Hermes."""
    from .tools import TOOLS

    for name, schema, handler, emoji, check_fn in TOOLS:
        ctx.register_tool(
            name=name,
            toolset="kumi",
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            emoji=emoji,
        )
