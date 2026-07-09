"""CLI test harness for the Stage 1 Librarian pipeline (no LLM).

Drive create/update/query/delete directly against the pipeline:

    python -m librarian.cli create --type note --body "an idea" --tag ml
    python -m librarian.cli create --type contact --field name=Alex --field birthday=2000-01-01
    python -m librarian.cli query --type contact
    python -m librarian.cli update contacts/alex.md --field likes=coffee
    python -m librarian.cli delete notes/an-idea.md

Global flags let you point at a scratch vault/db for experiments.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from librarian.pipeline import Librarian
from librarian.store.vault_io import default_vault_root
from librarian.vault_init import init_vault

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """Load `.env` from the repo root if present. Does not override existing env."""
    env_file = _REPO_ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _run_eval(lib: Librarian, args) -> int:
    """Build an auto-generated eval set and score the chosen retrieval path."""
    from librarian.eval.harness import (
        GeminiQuestionGenerator,
        KeywordQuestionGenerator,
        build_eval_set,
        score,
    )
    from librarian.retrieval.hybrid import HybridRetriever
    from librarian.retrieval.semantic import SemanticRetriever

    if lib.vector_store is None or lib.embedder is None:
        print("eval requires vectors enabled", file=sys.stderr)
        return 1

    # Ensure the vector index reflects the vault on disk before scoring.
    lib.reindex()

    has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    generator_kind = args.generator or ("gemini" if has_key else "keyword")
    generator = (
        GeminiQuestionGenerator() if generator_kind == "gemini" else KeywordQuestionGenerator()
    )

    cases = build_eval_set(lib.vault, generator, n=args.n, seed=args.seed)
    if not cases:
        print("no notes to evaluate", file=sys.stderr)
        return 1

    if args.path == "hybrid":
        retriever = HybridRetriever(lib.vector_store, lib.embedder, lib.meta)
    else:
        retriever = SemanticRetriever(lib.vector_store, lib.embedder)

    report = score(retriever, cases, k=args.k)
    print(f"{args.path} | {generator_kind} generator | {report.summary()}")
    if args.verbose:
        for c in report.per_case:
            mark = "OK " if c["hit"] else "MISS"
            print(f"  [{mark}] rank={c['rank']} {c['note_path']} :: {c['question']}")
    return 0


def _parse_fields(pairs: list[str] | None) -> dict:
    out: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--field expects key=value, got: {pair!r}")
        key, value = pair.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="librarian", description="Stage 1 Librarian CLI harness")
    p.add_argument("--vault", default=None, help="vault root (default: repo vault/)")
    p.add_argument("--db", default=None, help="metadata db path (default: index.sqlite)")
    p.add_argument("--schema", default=None, help="schema.json path")
    p.add_argument("--git", action="store_true", help="commit each write to git")

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the vault folder structure + seed schema.json")

    sub.add_parser(
        "reindex",
        help="rebuild the metadata + vector index from vault markdown (source of truth)",
    )

    e = sub.add_parser("eval", help="auto-generated retrieval eval (recall@k / MRR)")
    e.add_argument("-n", type=int, default=None, help="sample size (default: all notes)")
    e.add_argument("-k", type=int, default=5, help="top-k cutoff for recall@k")
    e.add_argument("--path", choices=["semantic", "hybrid"], default="semantic")
    e.add_argument(
        "--generator",
        choices=["keyword", "gemini"],
        default=None,
        help="question generator (default: gemini if an API key is set, else keyword)",
    )
    e.add_argument("--seed", type=int, default=0)
    e.add_argument("--verbose", action="store_true", help="print per-case ranks")

    c = sub.add_parser("create", help="create a note")
    c.add_argument("--type", required=True)
    c.add_argument("--body", default="")
    c.add_argument("--raw", default=None, help="original input to archive (defaults to body)")
    c.add_argument("--slug", default=None)
    c.add_argument("--field", action="append", metavar="K=V", help="frontmatter field")
    c.add_argument("--tag", action="append", metavar="TAG", help="tag (repeatable)")

    u = sub.add_parser("update", help="update a note")
    u.add_argument("path")
    u.add_argument("--body", default=None)
    u.add_argument("--field", action="append", metavar="K=V")
    u.add_argument("--tag", action="append", metavar="TAG")

    d = sub.add_parser("delete", help="soft-delete a note")
    d.add_argument("path")

    b = sub.add_parser("benchmark", help="token A/B benchmark (full agent vs passthrough)")
    b.add_argument("--arms", default="A,B", help="comma-separated arms to run (A, B)")
    b.add_argument("--no-seed", action="store_true", help="don't seed the vault with demo notes first")

    h = sub.add_parser("handle", help="route a free-text request through the full LLM pipeline")
    h.add_argument("request", help="verbatim user request")
    h.add_argument("--context", default=None, help="recent conversation turns (for coreference)")

    cf = sub.add_parser("confirm", help="approve or reject a pending confirmation")
    cf.add_argument("pending_id", help="id from a prior needs_clarification response")
    cf.add_argument("--approve", action="store_true", help="execute the pending action")
    cf.add_argument("--reject", action="store_true", help="cancel the pending action")

    q = sub.add_parser("query", help="structured query over the index")
    q.add_argument("--type", default=None)
    q.add_argument("--tag", default=None)
    q.add_argument("--created-after", default=None)
    q.add_argument("--created-before", default=None)
    q.add_argument("--order-by", default="created_date")
    q.add_argument("--asc", action="store_true", help="ascending order")
    q.add_argument("--limit", type=int, default=None)

    return p


def _run_benchmark(lib: Librarian, args) -> int:
    """Run the token A/B benchmark against live Gemini and print the report."""
    from librarian.benchmark.ab import run_benchmark, seed_demo_vault
    from librarian.llm.gemini_client import get_llm_client

    try:
        llm = get_llm_client()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not args.no_seed:
        seed_demo_vault(lib)

    arms = tuple(a.strip().upper() for a in args.arms.split(",") if a.strip())
    report = run_benchmark(lib, llm, arms=arms)
    print(report.format_text())
    return 0


def _run_handle(lib: Librarian, args) -> int:
    """Route a free-text request through classify → retrieve/write → respond."""
    from librarian.agent import LibrarianAgent
    from librarian.llm.gemini_client import get_llm_client

    try:
        llm = get_llm_client()
    except RuntimeError as exc:  # no API key / SDK missing
        print(str(exc), file=sys.stderr)
        return 1

    agent = LibrarianAgent(lib, llm)
    res = agent.handle(args.request, context=args.context)
    print(f"[{res.status}] {res.message}")
    if res.pending_id:
        print(f"pending: {res.pending_id}", file=sys.stderr)
    if res.note_id:
        print(f"note: {res.note_id} | action: {res.action}", file=sys.stderr)
    return 1 if res.status == "error" else 0


def _run_confirm(lib: Librarian, args) -> int:
    from librarian.agent import LibrarianAgent
    from librarian.llm.gemini_client import get_llm_client

    if args.approve == args.reject:
        print("pass exactly one of --approve or --reject", file=sys.stderr)
        return 1

    try:
        llm = get_llm_client()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    agent = LibrarianAgent(lib, llm)
    res = agent.handle_confirm(args.pending_id, approved=args.approve)
    print(f"[{res.status}] {res.message}")
    if res.note_id:
        print(f"note: {res.note_id} | action: {res.action}", file=sys.stderr)
    return 1 if res.status == "error" else 0


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    args = _build_parser().parse_args(argv)

    # init doesn't need (and can't assume) an existing vault/schema.
    if args.command == "init":
        root = init_vault(args.vault or default_vault_root())
        print(f"initialized vault at {root}")
        return 0

    lib = Librarian(
        vault_root=args.vault,
        db_path=args.db,
        schema_path=args.schema,
        git_enabled=args.git,
    )
    try:
        if args.command == "reindex":
            n = lib.reindex()
            print(f"reindexed {n} note(s)")
            return 0

        if args.command == "eval":
            return _run_eval(lib, args)

        if args.command == "handle":
            return _run_handle(lib, args)

        if args.command == "confirm":
            return _run_confirm(lib, args)

        if args.command == "benchmark":
            return _run_benchmark(lib, args)

        if args.command == "create":
            fields = _parse_fields(args.field)
            if args.tag:
                fields["tags"] = args.tag
            res = lib.create(
                type=args.type,
                fields=fields,
                body=args.body,
                raw_text=args.raw,
                slug=args.slug,
            )
            print(res.message)
            return 0 if res.ok else 1

        if args.command == "update":
            fields = _parse_fields(args.field)
            if args.tag:
                fields["tags"] = args.tag
            res = lib.update(args.path, fields=fields, body=args.body)
            print(res.message)
            return 0 if res.ok else 1

        if args.command == "delete":
            res = lib.delete(args.path)
            print(res.message)
            return 0 if res.ok else 1

        if args.command == "query":
            rows = lib.query_raw(
                type=args.type,
                tag=args.tag,
                created_after=args.created_after,
                created_before=args.created_before,
                order_by=args.order_by,
                descending=not args.asc,
                limit=args.limit,
            )
            print(json.dumps(rows, indent=2))
            print(f"\n{len(rows)} result(s)", file=sys.stderr)
            return 0
    finally:
        lib.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
