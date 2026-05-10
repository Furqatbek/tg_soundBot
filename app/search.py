"""Query parsing + SQLite FTS5 search.

`parse_query` extracts a `pack:slug` token from the user's text. The
remaining text is converted to an FTS5 MATCH expression by
`to_fts_match` — each word becomes a `word*` prefix term so that typing
a partial name still hits results.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Sound

_PACK_PREFIX = "pack:"
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def parse_query(raw: str) -> tuple[Optional[str], str]:
    """Pull a single ``pack:slug`` token out and return (slug, rest)."""
    pack: Optional[str] = None
    rest: list[str] = []
    for tok in (raw or "").split():
        low = tok.lower()
        if low.startswith(_PACK_PREFIX) and len(low) > len(_PACK_PREFIX):
            pack = low[len(_PACK_PREFIX):]
        else:
            rest.append(tok)
    return pack, " ".join(rest).strip()


def to_fts_match(text_query: str) -> str:
    """Turn free text into an FTS5 prefix MATCH expression. Returns ``""``
    if there are no usable tokens (caller should skip the FTS join then).
    """
    tokens = _TOKEN_RE.findall((text_query or "").lower())
    return " ".join(f"{t}*" for t in tokens)


async def search_sounds(
    session: AsyncSession,
    *,
    text_query: str,
    pack_id: Optional[int] = None,
    limit: int = 50,
) -> Sequence[Sound]:
    """Run a search ordered by FTS rank (when text given) or popularity.

    `pack_id`, if supplied, restricts results to that pack.
    """
    fts = to_fts_match(text_query)
    if fts:
        clauses = [
            "SELECT s.* FROM sounds s",
            "JOIN sounds_fts fts ON s.id = fts.rowid",
            "WHERE sounds_fts MATCH :q",
        ]
        params: dict = {"q": fts, "lim": limit}
        if pack_id is not None:
            clauses.append("AND s.pack_id = :pack_id")
            params["pack_id"] = pack_id
        clauses.append("ORDER BY fts.rank, s.play_count DESC, s.created_at DESC")
        clauses.append("LIMIT :lim")
        sql = text("\n".join(clauses))
        stmt = select(Sound).from_statement(sql)
        result = await session.execute(stmt, params)
        return result.scalars().all()

    # No text — fall back to ORM with optional pack filter.
    stmt = select(Sound).order_by(
        Sound.play_count.desc(), Sound.created_at.desc()
    ).limit(limit)
    if pack_id is not None:
        stmt = stmt.where(Sound.pack_id == pack_id)
    return (await session.execute(stmt)).scalars().all()
