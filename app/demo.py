"""Explicit fabricated fixture, never imported into the real upload corpus."""

from app.corpus import validate_source
from app.transcript import normalize


def source():
    return validate_source(
        {
            "id": "synthetic_council",
            "kind": "synthetic",
            "title": "SYNTHETIC council transcript",
            "url": "https://example.invalid/synthetic-council",
            "duration": 180,
            "provenance": "Fabricated by this project for deterministic demonstrations; no recording exists.",
            "permission": "Project-authored synthetic fixture.",
            "segments": [
                {
                    "start": 90,
                    "end": 100,
                    "text": "The council asked whether emergency shelters should close during winter.",
                },
                {
                    "start": 100,
                    "end": 115,
                    "text": "The city should close emergency shelters during winter only if every resident has safe permanent housing.",
                },
                {
                    "start": 115,
                    "end": 130,
                    "text": "That condition is not satisfied today, so the shelters must remain open.",
                },
            ],
        }
    )


def transcript():
    return normalize(
        {
            "segments": [
                {
                    "start": 0,
                    "end": 6,
                    "text": "The city should close emergency shelters during winter.",
                }
            ]
        },
        6,
    )
