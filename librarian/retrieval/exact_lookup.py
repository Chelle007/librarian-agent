"""`exact_lookup` — the merged keyword + structured retrieval path (no LLM).

The architecture keeps *keyword* and *structured/metadata* retrieval as distinct
conceptual paths but collapses them into one implementation module, since both
are non-LLM SQLite lookups that differ only in which field they match:

- **structured** — filter the metadata index by `type` / `tag` / date range.
- **keyword**    — narrow by literal token match against the note's title (its
  slugified path) and tags.

Both hand back the same `LookupResult`, rendered by a deterministic template
(no generation → no hallucination, no token cost). An **aggregation** sub-flag
answers "how many X" with a dual-check (strict type count + tag scan) that
surfaces schema-on-read undercounts instead of silently returning a partial one.

Structural filtering runs in SQLite over the index; only the handful of matched
notes are then read from the vault to render field-level detail — the index does
the narrowing, disk reads stay proportional to results, not to vault size.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from librarian.retrieval import templates
from librarian.store.metadata_store import MetadataStore
from librarian.store.vault_io import VaultIO, VaultIOError

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass
class LookupHit:
    """A matched note, enriched from the vault for template rendering."""

    path: str
    type: str
    title: str
    created_date: str | None
    tags: list[str] = field(default_factory=list)
    frontmatter: dict = field(default_factory=dict)


@dataclass
class AggregationInfo:
    """Result of the strict-count vs tag-scan dual-check."""

    strict_count: int
    tag_count: int

    @property
    def discrepancy(self) -> int:
        """Notes carrying the tag but not the strict type (potential undercount)."""
        return self.tag_count - self.strict_count


@dataclass
class LookupResult:
    hits: list[LookupHit]
    count: int
    message: str
    aggregation: AggregationInfo | None = None


class ExactLookup:
    def __init__(self, meta: MetadataStore, vault: VaultIO):
        self.meta = meta
        self.vault = vault

    def lookup(
        self,
        *,
        type: str | None = None,
        tag: str | None = None,
        keyword: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        order_by: str = "created_date",
        descending: bool = True,
        limit: int | None = None,
        aggregate: bool = False,
    ) -> LookupResult:
        """Run a structured/keyword lookup, returning enriched hits + a message."""
        if aggregate:
            return self._aggregate(type=type, tag=tag)

        rows = self.meta.query(
            type=type,
            tag=tag,
            created_after=created_after,
            created_before=created_before,
            order_by=order_by,
            descending=descending,
            # A keyword filters in Python after the SQL filter, so don't cap the
            # SQL result before that narrowing has happened.
            limit=None if keyword else limit,
        )

        if keyword:
            rows = self._filter_by_keyword(rows, keyword)
            if limit is not None:
                rows = rows[:limit]

        hits = [self._to_hit(row) for row in rows]
        return LookupResult(
            hits=hits,
            count=len(hits),
            message=templates.render_results(hits, keyword=keyword),
        )

    # --------------------------------------------------------------- aggregate
    def _aggregate(self, *, type: str | None, tag: str | None) -> LookupResult:
        """Dual-check count: strict `type` match plus a tag scan for undercounts."""
        scan_tag = tag or type
        strict_rows = self.meta.query(type=type, limit=None) if type else []
        strict_paths = {r["path"] for r in strict_rows}

        tag_rows = self.meta.query(tag=scan_tag, limit=None) if scan_tag else []
        extra = [r for r in tag_rows if r["path"] not in strict_paths]

        agg = AggregationInfo(
            strict_count=len(strict_rows),
            tag_count=len(strict_rows) + len(extra),
        )
        # Hits = the strict matches (what a plain count would return), so a caller
        # can still show them; the discrepancy lives in `aggregation`/`message`.
        hits = [self._to_hit(r) for r in strict_rows]
        message = templates.render_aggregation(
            note_type=type or "note",
            strict_count=agg.strict_count,
            tag_count=agg.tag_count,
            tag=scan_tag,
        )
        return LookupResult(hits=hits, count=agg.strict_count, message=message, aggregation=agg)

    # ----------------------------------------------------------------- helpers
    def _filter_by_keyword(self, rows: list[dict], keyword: str) -> list[dict]:
        """Keep rows whose title (slug) or tags contain every keyword token."""
        tokens = _TOKEN_RE.findall(keyword.lower())
        if not tokens:
            return rows

        kept = []
        for row in rows:
            haystack = row["path"].lower() + " " + " ".join(
                str(t).lower() for t in (row.get("tags") or [])
            )
            if all(tok in haystack for tok in tokens):
                kept.append(row)
        return kept

    def _to_hit(self, row: dict) -> LookupHit:
        """Enrich an index row with the note's frontmatter (for field-level detail).

        The index is a derived cache; if the file is missing (stale index between
        reindexes) we still return a usable hit from the row alone rather than fail.
        """
        path = row["path"]
        frontmatter: dict = {}
        try:
            frontmatter = self.vault.read(path).frontmatter
        except VaultIOError:
            pass

        title = frontmatter.get("title") or frontmatter.get("name") or _title_from_path(path)
        return LookupHit(
            path=path,
            type=row.get("type") or frontmatter.get("type") or "note",
            title=str(title),
            created_date=row.get("created_date") or frontmatter.get("created_date"),
            tags=row.get("tags") or [],
            frontmatter=frontmatter,
        )


def _title_from_path(path: str) -> str:
    """Fallback display title from the filename slug (`notes/my-idea.md` → 'my idea')."""
    return Path(path).stem.replace("-", " ")
