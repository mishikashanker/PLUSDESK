"""Platform-neutral intake boundary; future Discord adapter implements this DTO."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass(frozen=True)
class Submission:
    platform: str
    workspace_id: str
    channel_id: str
    external_user_id: str
    input_kind: Literal["command", "message"]
    source_id: str
    source_url: str | None
    raw_text: str
    submitted_at: datetime


@dataclass(frozen=True)
class Receipt:
    entry_id: str
    duplicate: bool


class Intake(Protocol):
    def accept(self, submission: Submission) -> Receipt: ...
