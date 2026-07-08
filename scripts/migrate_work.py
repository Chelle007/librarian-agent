"""Migrate Notion Work + related personal pages into the vault."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from librarian.ingestion.notion_convert import (  # noqa: E402
    _strip_notion_chars,
    format_interview_tips_jia,
    format_scholarship_questions,
    notion_to_markdown,
    rows_to_markdown_table,
)
from librarian.pipeline import Librarian  # noqa: E402
from scripts.vault_paths import SLUG_AREA, note_path, upsert_note  # noqa: E402

JOB_APP_FETCH = """<content>
so what i need to do:<br>1.⁠ ⁠make a very detailed CV with all projects listed so later AI can cherrypick which to include based on job description<br>2.⁠ ⁠⁠build the automation ([https://www.autotailor.app/](https://www.autotailor.app/))<br>3.⁠ ⁠⁠find job roles<br>4.⁠ ⁠⁠use the automation then click send :D
So i kinda think i should try<br>1.⁠ ⁠apply for big company graduate program (indo if sg cannot)<br>2.⁠ ⁠find job through connection like mabel or relatives or even professor
</content>"""

JIA_TIPS_FETCH = """<content>
## 9 Mar 2026
- choose median of the salary range, and rmb to say "open for negotiation"
- job portal: indeed, futurecareer, jobstreet
- after interview: very short email abt thank u. After 1 week follow up
Basic question:
- introduce yourself (make sure 2-3 mins only, talk abt best experience and achievement, outside of work enjoy something)
- why interested in applying and why interested in this role (rmb the job responsibilities)
- beware of small start up, big start up is fine with more than 2 ppl in ur team (at least has others to delegate task)
- do u have any questions? Find out anything u want to ask: like do i need to standby during weekend? pay OT/not and how, salary naik tiap bulan tak?
## 28 Mar 2026
- Strength<br>•⁠  ⁠jgn generic, cari skill yg not everyone can<br>•⁠  ⁠generic but give proof<br>Cth:<br>•⁠  ⁠multi lingual<br>•⁠  ⁠multi task<br>•⁠  ⁠patience<br>•⁠  ⁠work under pressure
- Weakness<br>•⁠  ⁠be honest<br>•⁠  ⁠but then improved + if possible the implementation is beneficial to the new company<br>Cth<br>•⁠  ⁠ovt on doing tasks -\\> learn to prioritize
- Frequent question: Where do u see yourself in 5 years (just lie, make sure it's beneficial to company)
- Frequent question: what's ur expectation in this role?
</content>"""

SCHOLARSHIP_FETCH = """<content>
Bold = i clearly rmb they asked abt this
The rest not sure they ask like that or not, but i think should be around that
- Scholarship
\t- Why are you deserving of the scholarship and what you will do with it?
\t- **What other support u think school can provide?**
\t- How do you wish to contribute to SIM?
- CCA
\t- **How do you juggle between school and CCA and other commitments?**
\t- I think they might ask a lot abt our ITCamp curriculum
- Career Plan
\t- What's ur future career plan?
\t- **Do you think AI can replace it?**
Surprisingly they didn't ask much about hackathons. They care more abt CCA so yea…
</content>"""

JOB_ROWS = [
    {
        "Company Name": "Ola Chat",
        "Role": "Ola Trainee - Back-End Engineer",
        "Location": "Singapore (Paya Lebar)",
        "Salary (Monthly)": "$3,500\n~ $6,000",
        "Status": "Not started",
        "Application Link": "https://www.linkedin.com/jobs/view/ola-trainee-back-end-engineer-at-ola-chat-4380223617/",
        "next_step": None,
        "Notes": "Looks like startup, founded in 2019. Altho not mentioned in requirements but their product mainly on AI chat (?)",
    }
]

JOB_TABLE_COLS = [
    ("Company", "Company Name"),
    ("Role", "Role"),
    ("Location", "Location"),
    ("Salary", "Salary (Monthly)"),
    ("Status", "Status"),
    ("Next step", "next_step"),
    ("Link", "Application Link"),
    ("Notes", "Notes"),
]


def _format_job_application() -> str:
    body = notion_to_markdown(JOB_APP_FETCH)
    body = _strip_notion_chars(body)
    body = re.sub(r"(?m)^(\d+)\.\s*", r"\1. ", body)
    body = re.sub(r"(?m)^So i kinda think i should try$", "\n## Strategy", body)
    body = re.sub(r"(?m)^so what i need to do:$", "## Todo", body, flags=re.I)
    parts = [body.strip(), "", "## Application tracker", rows_to_markdown_table(JOB_ROWS, JOB_TABLE_COLS)]
    return "\n".join(parts)


def main() -> int:
    lib = Librarian(vector_enabled=False)
    pages = [
        {
            "area": "work",
            "slug": "interview-tips-from-jia",
            "created_date": "2026-03-30",
            "tags": ["notion-import", "work"],
            "url": "https://app.notion.com/p/33319407beda8065b898de5403f82d11",
            "body": format_interview_tips_jia(JIA_TIPS_FETCH),
        },
        {
            "area": "work",
            "slug": "job-application",
            "created_date": "2026-03-17",
            "tags": ["notion-import", "work"],
            "url": "https://app.notion.com/p/31d19407beda80eaab9aeec5c9b41da3",
            "body": _format_job_application(),
        },
        {
            "area": "university",
            "slug": "scholarship-interview-questions",
            "created_date": "2025-10-17",
            "tags": ["notion-import", "university", "personal"],
            "url": "https://app.notion.com/p/28f19407beda80dd8d18e1228b22a8f2",
            "body": format_scholarship_questions(SCHOLARSHIP_FETCH),
        },
    ]

    for page in pages:
        body = page["body"] + f"\n\n---\n_Migrated from Notion: {page['url']}_\n"
        res = upsert_note(
            lib,
            area=page["area"],
            slug=page["slug"],
            created_date=page["created_date"],
            tags=page["tags"],
            body=body,
        )
        action = res.action or "updated"
        if not res.ok:
            print(f"FAIL {page['slug']}: {res.message}", file=sys.stderr)
            return 1
        print(f"OK  {action} {res.path or note_path(page['area'], page['slug'])}")

    lib.close()
    print(f"\nMigrated {len(pages)} note(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
