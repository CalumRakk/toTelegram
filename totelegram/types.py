from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

import peewee

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Chat

    from totelegram.cli.state import CLIState
    from totelegram.concurrency import ConcurrencyCoordinator
    from totelegram.discovery import DiscoveryService
    from totelegram.identity import Settings
    from totelegram.models import (
        Job,
        RemotePayload,
        TelegramUser,
    )
    from totelegram.schemas import AvailabilityState


@dataclass
class UploadContext:
    """Contexto unificado de ejecución para operaciones de carga."""

    tg_chat: "Chat"
    owner: "TelegramUser"
    client: "Client"
    db: peewee.Database
    discovery: "DiscoveryService"
    settings: "Settings"
    state: "CLIState"
    coordinator: "ConcurrencyCoordinator"

    @property
    def account_id(self) -> Optional[int]:
        return self.settings.telegram_account_id


@dataclass
class AvailabilityReport:
    """Reporte generado por DiscoveryService tras inspeccionar un recurso."""

    state: "AvailabilityState"
    remotes: List["RemotePayload"] = field(default_factory=list)

    @property
    def can_forward(self) -> bool:
        from totelegram.schemas import AvailabilityState

        return self.state == AvailabilityState.CAN_FORWARD and len(self.remotes) > 0


@dataclass
class StrategyResult:
    """Resultado devuelto por la ejecución de una estrategia concreta."""

    strategy_name: str
    pieces_processed: int = 0
    did_upload_bytes: bool = False
    is_completed: bool = False
    message: str = ""


@dataclass
class JobExecutionResult:
    """Resultado consolidado del ciclo de vida completo de un Job en el Pipeline."""

    job: "Job"
    path: Path
    strategy_result: Optional[StrategyResult] = None
    is_completed: bool = False
    snapshot_generated: bool = False
    message: str = ""
