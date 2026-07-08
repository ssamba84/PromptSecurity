"""Generate PDF fixtures used by the tests and for manual end-to-end testing.

Run once (or whenever you want to regenerate):
    python -m tests.make_fixtures
"""
from __future__ import annotations

import pathlib

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures"

# The AWS example key from the assignment.
SECRET_KEY = "AKIAIOSFODNN7EXAMPLE"


def _write_pdf(path: pathlib.Path, lines: list[str]) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    text = c.beginText(72, 720)
    for line in lines:
        text.textLine(line)
    c.drawText(text)
    c.showPage()
    c.save()


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    _write_pdf(
        FIXTURE_DIR / "secret.pdf",
        [
            "Quarterly Infrastructure Notes",
            "",
            "Please rotate the following credential before Friday:",
            f"AWS_ACCESS_KEY_ID = {SECRET_KEY}",
            "Contact the platform team with questions.",
        ],
    )

    _write_pdf(
        FIXTURE_DIR / "clean.pdf",
        [
            "Weekly Team Update",
            "",
            "The migration finished ahead of schedule.",
            "No blockers this week. Thanks everyone!",
        ],
    )

    print(f"Wrote fixtures to {FIXTURE_DIR}")


if __name__ == "__main__":
    main()
