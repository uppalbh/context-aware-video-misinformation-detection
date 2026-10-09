"""Local operator-managed permitted transcript corpus; URLs are references, never fetch targets."""

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit

from app.errors import ProcessingError


def tokens(text):
    return [m.group().casefold() for m in re.finditer(r"\w+(?:['’]\w+)?", text)]


def validate_source(value):
    try:
        if not isinstance(value, dict) or value.get("kind") not in {"real", "synthetic"}:
            raise ValueError()
        if not re.fullmatch(r"[a-z0-9_-]{1,64}", value["id"]):
            raise ValueError()
        for name in ("title", "url", "provenance", "permission"):
            if (
                not isinstance(value[name], str)
                or not value[name].strip()
                or not 1 <= len(value[name]) <= 2000
            ):
                raise ValueError()
        url = urlsplit(value["url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError()
        if any(ord(c) < 32 for c in value["url"]):
            raise ValueError()
        duration = value["duration"]
        if (
            type(duration) not in (int, float)
            or not math.isfinite(duration)
            or not 0 < duration <= 14400
        ):
            raise ValueError()
        raw = value["segments"]
        if not isinstance(raw, list) or not 1 <= len(raw) <= 1000:
            raise ValueError()
        segments, previous, total = [], 0, 0
        for i, segment in enumerate(raw):
            start, end, text = segment["start"], segment["end"], segment["text"]
            if (
                type(start) not in (int, float)
                or type(end) not in (int, float)
                or not math.isfinite(start)
                or not math.isfinite(end)
                or not 0 <= start <= end <= duration
                or start < previous
                or not isinstance(text, str)
                or not text.strip()
                or len(text) > 4000
            ):
                raise ValueError()
            total += len(tokens(text))
            previous = start
            segments.append({"id": f"s{i}", "start": start, "end": end, "text": text})
        if total > 20000:
            raise ValueError()
        source = {
            k: value[k]
            for k in ("id", "title", "url", "provenance", "permission", "kind", "duration")
        }
        source["segments"] = segments
        source["revision"] = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
        return source
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ProcessingError(
            "invalid_corpus", "Source transcript/provenance is invalid or exceeds corpus limits."
        )


def load_corpus(directory: Path):
    if not directory.exists():
        return []
    files = sorted(directory.glob("*.json"))
    if len(files) > 100:
        raise ProcessingError("invalid_corpus", "Corpus exceeds 100 source recordings.")
    sources = []
    try:
        for path in files:
            if path.is_symlink() or path.stat().st_size > 5_000_000:
                raise ValueError()
            sources.append(validate_source(json.loads(path.read_text(encoding="utf-8"))))
        if len({s["id"] for s in sources}) != len(sources):
            raise ValueError()
    except (ValueError, OSError):
        raise ProcessingError(
            "invalid_corpus", "Corpus files are unreadable, duplicated, or too large."
        )
    return sources


def main():
    from app.config import Settings

    parser = argparse.ArgumentParser(
        description="Import one authorized timestamped source; no URL fetching."
    )
    parser.add_argument("file", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if args.file.stat().st_size > 5_000_000:
        parser.error("Source JSON exceeds 5 MB")
    source = validate_source(json.loads(args.file.read_text(encoding="utf-8")))
    cfg = Settings()
    cfg.corpus_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = cfg.corpus_dir / (source["id"] + ".json")
    if target.exists() and not args.replace:
        parser.error("Source exists; use --replace for an explicit corpus revision")
    existing = load_corpus(cfg.corpus_dir)
    if len(existing) >= 100 and not target.exists():
        parser.error("Corpus capacity reached")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    print(
        "Imported source", source["id"], "kind", source["kind"], "revision", source["revision"][:12]
    )


if __name__ == "__main__":
    main()
