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
import sys

from librarian.pipeline import Librarian


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

    q = sub.add_parser("query", help="structured query over the index")
    q.add_argument("--type", default=None)
    q.add_argument("--tag", default=None)
    q.add_argument("--created-after", default=None)
    q.add_argument("--created-before", default=None)
    q.add_argument("--order-by", default="created_date")
    q.add_argument("--asc", action="store_true", help="ascending order")
    q.add_argument("--limit", type=int, default=None)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    lib = Librarian(
        vault_root=args.vault,
        db_path=args.db,
        schema_path=args.schema,
        git_enabled=args.git,
    )
    try:
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
