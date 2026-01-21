from typing import List

from pydantic import BaseModel

from totelegram.core.enums import AvailabilityState
from totelegram.store.models import RemotePayload


class ExecutionPlan(BaseModel):
    """Clase base para cualquier decisión del sistema."""


class SkipPlan(ExecutionPlan):
    reason: str
    is_already_fulfilled: bool = False


class PhysicalUploadPlan(ExecutionPlan):
    reason: str = "Integrando archivo al sistema por primera vez."


class ForwardPlan(ExecutionPlan):
    """Clonación simple o reconstrucción total."""

    remotes: List[RemotePayload]
    is_puzzle: bool = False

    @property
    def source_chats_count(self) -> int:
        return len({r.chat_id for r in self.remotes})


class AskUserPlan(ExecutionPlan):
    """Situación ambigua que requiere intervención humana."""

    state: AvailabilityState
    remotes: List[RemotePayload]
