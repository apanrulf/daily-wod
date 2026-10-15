"""Inställningar som läses från .env och filerna i projektmappen."""
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"


@dataclass
class Config:
    anthropic_api_key: str
    ig_access_token: str
    ig_user_id: str | None
    model: str
    api_version: str
    brand_path: Path
    queue_path: Path
    state_path: Path
    flagged_path: Path
    wod_path: Path
    images_dir: Path
    image_base_url: str | None
    ig_handle: str | None
    timezone: str
    wod_format: str


def load_config() -> Config:
    load_dotenv(ENV_PATH)
    missing = [k for k in ("ANTHROPIC_API_KEY", "IG_ACCESS_TOKEN") if not os.getenv(k)]
    if missing:
        sys.exit(f"Saknar {', '.join(missing)} i .env. Se README, steg 4.")
    wod_format = (os.getenv("WOD_FORMAT") or "karusell").strip().lower()
    if wod_format not in ("karusell", "bild"):
        sys.exit(f'WOD_FORMAT måste vara "karusell" eller "bild", inte "{wod_format}".')
    return Config(
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        ig_access_token=os.environ["IG_ACCESS_TOKEN"],
        ig_user_id=os.getenv("IG_USER_ID") or None,
        model=os.getenv("CLAUDE_MODEL") or "claude-sonnet-5-5",
        api_version=os.getenv("IG_API_VERSION") or "v25.0",
        brand_path=ROOT / "varumarke.md",
        queue_path=ROOT / "ko.json",
        state_path=ROOT / "state.json",
        flagged_path=ROOT / "flaggade.md",
        wod_path=ROOT / "wods.json",
        images_dir=ROOT / "bilder",
        image_base_url=os.getenv("BILD_BAS_URL") or None,
        ig_handle=os.getenv("IG_HANDLE") or None,
        timezone=os.getenv("TIDSZON") or "Europe/Stockholm",
        wod_format=wod_format,
    )
