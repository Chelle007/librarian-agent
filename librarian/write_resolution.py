"""Resolve whether a mutation should create a new note or update an existing one.

Create and update both need the same question answered first: *which note on disk
is this about?* Resolution order:

1. Conversation context — path from the prior turn (coreference after a query).
2. Explicit reference — classifier ``target_ref`` or a vault-relative path.
3. Schema identity — typed fields from the classifier (``name``, ``due_date``, …)
   or values already in the vault mentioned in text. One match → update; several
   → clarify among those only; none → create. Semantic search is never used here.
4. Vague reference — pronouns / "that note" only: semantic search + best-guess confirm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from librarian.classifier import Classification
from librarian.store.metadata_store import MetadataStore
from librarian.store.schema import Schema
from librarian.store.vault_io import VaultIO, VaultIOError
from librarian.target_resolution import (
    TargetResolution,
    recency_pick,
    resolve_existing_path,
    resolve_target,
)


@dataclass
class WriteResolution:
    action: str  # "create" | "update" | "clarify"
    path: str | None = None
    candidates: list[str] = field(default_factory=list)
    target: TargetResolution | None = None


def resolve_write_target(
    c: Classification,
    raw_request: str,
    *,
    schema: Schema,
    meta: MetadataStore,
    vault: VaultIO,
    retriever,
    context: str | None = None,
) -> WriteResolution:
    """Pick create vs update vs clarify before any vault write."""
    if _is_statement(raw_request):
        ctx_path = _path_from_context(context, meta)
        if ctx_path:
            return WriteResolution("update", ctx_path, [ctx_path])

    ref = (c.target_ref or "").strip()
    explicit = resolve_existing_path(ref, meta)
    if explicit:
        return WriteResolution("update", explicit, [explicit])

    identity = _identity_values(c, raw_request, schema, meta, vault)
    if _is_identity_anchored(identity, schema, c.note_type):
        return _resolve_by_identity(c, identity, schema, meta, vault)

    return _resolve_vague_reference(c, raw_request, meta, retriever, context)


def identity_keys_for(schema: Schema, note_type: str | None) -> tuple[str, ...]:
    """Per-type required fields (excl. base) that anchor create-vs-update."""
    if not note_type or not schema.is_known_type(note_type):
        return ()
    base = set(schema.base_required)
    return tuple(f for f in schema.types[note_type].required if f not in base)


def all_identity_keys(schema: Schema) -> tuple[str, ...]:
    keys: list[str] = []
    for note_type in schema.types:
        keys.extend(identity_keys_for(schema, note_type))
    return tuple(dict.fromkeys(keys))


def infer_note_type(
    schema: Schema, fields: dict, note_type_hint: str | None = None
) -> str | None:
    """Pick the schema type best matched by populated identity fields."""
    if note_type_hint and schema.is_known_type(note_type_hint):
        return schema.resolve_type(note_type_hint)

    ranked: list[tuple[bool, int, int, str]] = []
    base = set(schema.base_required)
    for note_type, spec in schema.types.items():
        required = [f for f in spec.required if f not in base]
        if not required:
            continue
        filled = sum(1 for f in required if fields.get(f))
        if filled == 0:
            continue
        ranked.append((filled == len(required), filled, len(required), note_type))

    if not ranked:
        return None
    ranked.sort(reverse=True)
    return schema.resolve_type(ranked[0][3])


def _resolve_by_identity(
    c: Classification,
    identity: dict[str, str],
    schema: Schema,
    meta: MetadataStore,
    vault: VaultIO,
) -> WriteResolution:
    """Schema-identity resolution — never semantic search."""
    id_paths = _find_by_identity(schema, meta, vault, c.note_type, identity)
    if len(id_paths) == 1:
        return WriteResolution("update", id_paths[0], id_paths)
    if len(id_paths) > 1:
        primary = recency_pick(id_paths, meta) or id_paths[0]
        return WriteResolution("clarify", primary, id_paths)
    return WriteResolution("create")


def _resolve_vague_reference(
    c: Classification,
    raw_request: str,
    meta: MetadataStore,
    retriever,
    context: str | None,
) -> WriteResolution:
    """Pronouns and unclear refs — semantic search only as a last resort."""
    if retriever is None:
        return WriteResolution("create" if c.intent == "create" else "clarify")

    ref = (c.target_ref or "").strip()
    tr = resolve_target(
        ref or raw_request,
        meta=meta,
        retriever=retriever,
        context=context,
    )
    if tr.resolved:
        return WriteResolution("update", tr.path, tr.candidates, target=tr)
    if c.intent == "update":
        return WriteResolution("clarify", tr.path, tr.candidates, target=tr)
    return WriteResolution("create")


def _is_identity_anchored(
    identity: dict[str, str], schema: Schema, note_type: str | None
) -> bool:
    if not identity:
        return False
    type_keys = (
        identity_keys_for(schema, note_type)
        if note_type and schema.is_known_type(note_type)
        else ()
    )
    if type_keys:
        return any(identity.get(k) for k in type_keys)
    return True


def _identity_values(
    c: Classification,
    raw_request: str,
    schema: Schema,
    meta: MetadataStore,
    vault: VaultIO,
) -> dict[str, str]:
    out = {
        key: str(c.fields[key]).strip()
        for key in all_identity_keys(schema)
        if c.fields.get(key)
    }
    for key, value in _identity_mentioned_in_vault(
        raw_request, schema, meta, vault
    ).items():
        out.setdefault(key, value)
    return out


def _identity_mentioned_in_vault(
    raw_request: str,
    schema: Schema,
    meta: MetadataStore,
    vault: VaultIO,
) -> dict[str, str]:
    """Identity field values already in the vault that appear in the request."""
    text = (raw_request or "").lower()
    if not text:
        return {}

    found: dict[str, str] = {}
    for note_type in schema.types:
        for key in identity_keys_for(schema, note_type):
            if key in found:
                continue
            for row in meta.query(type=note_type):
                try:
                    note = vault.read(row["path"])
                except VaultIOError:
                    continue
                value = str(note.frontmatter.get(key, "")).strip()
                if value and value.lower() in text:
                    found[key] = value
                    break
    return found


def _find_by_identity(
    schema: Schema,
    meta: MetadataStore,
    vault: VaultIO,
    note_type: str | None,
    identity: dict[str, str],
) -> list[str]:
    """Notes whose typed identity fields match the provided values."""
    types = _identity_search_types(schema, note_type, identity)

    matches: list[str] = []
    for nt in types:
        keys = identity_keys_for(schema, nt)
        if not any(identity.get(k) for k in keys):
            continue
        for row in meta.query(type=nt):
            try:
                note = vault.read(row["path"])
            except VaultIOError:
                continue
            if _note_matches_identity(note.frontmatter, row["path"], keys, identity):
                matches.append(row["path"])

    return _prefer_canonical_slug(matches, identity)


def _identity_search_types(
    schema: Schema, note_type: str | None, identity: dict[str, str]
) -> list[str]:
    """Types to scan for identity matches."""
    if note_type and schema.is_known_type(note_type):
        keys = identity_keys_for(schema, note_type)
        if keys and any(identity.get(k) for k in keys):
            return [schema.resolve_type(note_type)]
    return [
        t
        for t in schema.types
        if any(identity.get(k) for k in identity_keys_for(schema, t))
    ]


def _note_matches_identity(
    frontmatter: dict, path: str, keys: tuple[str, ...], identity: dict[str, str]
) -> bool:
    provided = {k: identity[k] for k in keys if identity.get(k)}
    if not provided:
        return False

    stem = Path(path).stem.lower()
    for key, want in provided.items():
        have = str(frontmatter.get(key, "")).strip().lower()
        want = want.strip().lower()
        if have == want:
            continue
        if key == "name" and (stem == want or stem.startswith(want + "-")):
            continue
        return False
    return True


def _prefer_canonical_slug(paths: list[str], identity: dict[str, str]) -> list[str]:
    """When several notes share an identity, keep only exact-slug matches if unique."""
    if len(paths) <= 1:
        return paths
    name = identity.get("name")
    if not name:
        return paths
    slug = name.strip().lower().replace(" ", "-")
    exact = [p for p in paths if Path(p).stem.lower() == slug]
    return exact if len(exact) == 1 else paths


def _path_from_context(context: str | None, meta: MetadataStore) -> str | None:
    if not context:
        return None
    m = re.search(r"\bfrom\s+(.+\.md)\b", context, re.IGNORECASE)
    raw = m.group(1).strip() if m else None
    if not raw:
        m = re.search(r"(\S+(?:\s+\S+)*\.md)", context)
        raw = m.group(1).strip() if m else None
    if not raw:
        return None
    return resolve_existing_path(raw, meta)


def _is_statement(text: str) -> bool:
    t = (text or "").strip()
    if not t or "?" in t:
        return False
    if re.match(r"^(who|what|when|where|why|how|find|show|list)\b", t, re.I):
        return False
    return True
