import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "data")).resolve())
    store: str = field(default_factory=lambda: os.getenv("STORE", "supabase"))
    max_upload_mb: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_MB", "100")))
    max_duration: float = field(
        default_factory=lambda: float(os.getenv("MAX_DURATION_SECONDS", "600"))
    )
    max_pending: int = field(default_factory=lambda: int(os.getenv("MAX_PENDING_JOBS", "20")))
    retention_hours: float = field(
        default_factory=lambda: float(os.getenv("MEDIA_RETENTION_HOURS", "24"))
    )
    media_timeout: int = field(
        default_factory=lambda: int(os.getenv("MEDIA_TIMEOUT_SECONDS", "60"))
    )
    ffmpeg: str = field(default_factory=lambda: os.getenv("FFMPEG_BIN", "ffmpeg"))
    ffprobe: str = field(default_factory=lambda: os.getenv("FFPROBE_BIN", "ffprobe"))
    openai_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""), repr=False)
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_key: str = field(
        default_factory=lambda: os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""), repr=False
    )
    cookie_secure: bool = field(
        default_factory=lambda: os.getenv("COOKIE_SECURE", "true").lower() == "true"
    )
    url_hosts: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            host.strip().lower()
            for host in os.getenv("VIDEO_URL_HOSTS", "media.w3.org").split(",")
            if host.strip()
        )
    )
    corpus_dir: Path | None = None
    semantic_provider: str = field(default_factory=lambda: os.getenv("SEMANTIC_PROVIDER", "none"))
    context_model: str = field(default_factory=lambda: os.getenv("CONTEXT_MODEL", "gpt-4.1-mini"))
    demo_enabled: bool = field(
        default_factory=lambda: os.getenv("ENABLE_SYNTHETIC_DEMO", "false").lower() == "true"
    )

    def __post_init__(self):
        self.corpus_dir = (
            self.corpus_dir
            or Path(os.getenv("CORPUS_DIR", str(self.data_dir / "corpus"))).resolve()
        )
        if self.semantic_provider not in {"none", "openai"}:
            raise ValueError("SEMANTIC_PROVIDER must be none or openai")
        if not (0 < self.max_duration <= 3600 and 0 < self.max_upload_mb <= 1024):
            raise ValueError("Upload limit must be 1–1024 MB; duration must be 0–3600 seconds")
        if self.max_pending < 1 or self.retention_hours <= 0 or self.media_timeout < 1:
            raise ValueError("Queue, retention and timeout limits must be positive")
