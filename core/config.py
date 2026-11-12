"""Local configuration. Secrets are loaded only on the server."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1]:
            value = value[1:-1]
        if key.replace("_", "").isalnum():
            os.environ.setdefault(key, value)


load_env(ROOT / ".env")


class Settings:
    def __init__(self, **overrides):
        self.root = ROOT
        self.data_dir = os.getenv("DATA_DIR") or (ROOT / "data")
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.base_url = "https://generativelanguage.googleapis.com/v1"
        self.api_style = os.getenv("GEMINI_API_STYLE", "generate_content")
        self.thinking_level = os.getenv("GEMINI_THINKING_LEVEL", "minimal" if "flash-lite" in self.model else "low")
        self.mode = os.getenv("APP_MODE", "auto")
        self.port = int(os.getenv("PORT", "5109"))
        self.openalex_key = os.getenv("OPENALEX_API_KEY", "").strip()
        self.openalex_mailto = os.getenv("OPENALEX_MAILTO", "").strip()
        self.timeout = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "300"))
        for key, value in overrides.items():
            setattr(self, key, value)
        self.data_dir = Path(self.data_dir)
        if not self.data_dir.is_absolute():
            self.data_dir = ROOT / self.data_dir
        self.live = self.mode != "preview" and bool(self.api_key)
