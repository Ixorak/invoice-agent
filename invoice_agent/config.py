"""Settings, read from config.json. Every key is optional."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CONFIG_FILE_NAME = "config.json"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """config.json is missing, unreadable or invalid."""


class Folders(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inbox: str = "inbox"
    processed: str = "processed"
    review: str = "review"
    output: str = "output"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str = "claude-code"
    claude_path: str = ""
    model: str | None = "sonnet"
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    timeout_seconds: float = Field(default=240, gt=0)
    folders: Folders = Field(default_factory=Folders)
    demo_folder: str = "demo"
    swiss_vat_rates: list[Decimal] = Field(
        default_factory=lambda: [Decimal("8.1"), Decimal("2.6"), Decimal("3.8"), Decimal("0")]
    )
    tolerance: Decimal = Field(default=Decimal("0.05"), ge=0)
    watch_interval_seconds: float = Field(default=10, gt=0)
    min_text_chars: int = Field(default=25, ge=0)
    max_text_chars: int = Field(default=60000, gt=1000)

    @field_validator("backend")
    @classmethod
    def _known_backend(cls, value: str) -> str:
        if value != "claude-code":
            raise ValueError("only the 'claude-code' backend is implemented")
        return value

    @field_validator("swiss_vat_rates", mode="before")
    @classmethod
    def _rates_as_text(cls, value):
        # 8.1 in JSON is a float; go through str() so it becomes Decimal("8.1") exactly.
        if isinstance(value, list):
            return [str(v) if isinstance(v, float) else v for v in value]
        return value

    @field_validator("tolerance", mode="before")
    @classmethod
    def _tolerance_as_text(cls, value):
        return str(value) if isinstance(value, float) else value


@dataclass(frozen=True)
class Workspace:
    """The folders the agent reads from and writes to. It touches nothing else."""

    inbox: Path
    processed: Path
    review: Path
    output: Path

    @classmethod
    def from_config(cls, config: Config, base_dir: Path) -> "Workspace":
        def resolve(p: str) -> Path:
            path = Path(p).expanduser()
            return (path if path.is_absolute() else base_dir / path).resolve()

        f = config.folders
        return cls(resolve(f.inbox), resolve(f.processed), resolve(f.review), resolve(f.output))

    @classmethod
    def under(cls, root: Path) -> "Workspace":
        root = root.resolve()
        return cls(root / "inbox", root / "processed", root / "review", root / "output")

    @property
    def json_dir(self) -> Path:
        return self.output / "json"

    @property
    def excel_path(self) -> Path:
        return self.output / "invoices.xlsx"

    @property
    def registry_path(self) -> Path:
        return self.output / "registry.json"

    @property
    def lock_path(self) -> Path:
        return self.output / ".invoice-agent.lock"

    def create(self) -> None:
        for folder in (self.inbox, self.processed, self.review, self.output, self.json_dir):
            folder.mkdir(parents=True, exist_ok=True)


def load_config(path: Path | None = None) -> tuple[Config, Path]:
    """Load config.json. Returns the config and the folder relative paths are based on.

    Without an explicit path, ./config.json is used if it exists, else the defaults.
    """
    if path is None:
        candidate = Path.cwd() / CONFIG_FILE_NAME
        if not candidate.exists():
            return Config(), Path.cwd()
        path = candidate
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    try:
        config = Config.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ConfigError(f"Invalid {path.name}: {details}") from exc
    return config, path.resolve().parent
