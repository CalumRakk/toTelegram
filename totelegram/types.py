from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Generator, List

import peewee

from totelegram.engine.events import DuplicateCandidate
from totelegram.schemas import ExecutionDecision

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Chat

    from totelegram.concurrency import ConcurrencyCoordinator
    from totelegram.discovery import DiscoveryService
    from totelegram.identity import Settings
    from totelegram.models import (
        Job,
        RemotePayload,
        TelegramUser,
    )

# --- SESIÓN TÉCNICA DE SUBIDA (SIN DEPENDENCIAS DE CLI) ---


@dataclass(frozen=True)
class UploadSession:
    """Entorno técnico y recursos requeridos para ejecutar operaciones de carga."""

    client: "Client"
    db: peewee.Database
    tg_chat: "Chat"
    owner: "TelegramUser"
    settings: "Settings"
    coordinator: "ConcurrencyCoordinator"
    discovery: "DiscoveryService"

    @property
    def account_id(self) -> int | None:
        """ID único de la cuenta de Telegram para coordinación de bloqueos."""
        return self.settings.telegram_account_id

    @contextmanager
    def exclusive_lock(self, ttl_seconds: int = 300) -> Generator[str, None, None]:
        """
        Bloquea la cuenta de Telegram para uso exclusivo de este nodo durante la subida.
        Garantiza que ninguna otra terminal o VPS procese transferencias con la misma cuenta.
        """
        with self.coordinator.guard_account(
            self.account_id, ttl_seconds=ttl_seconds
        ) as resource_id:
            yield resource_id


# Alias de retrocompatibilidad transitoria
UploadContext = UploadSession


# --- REPORTES Y RESULTADOS ---


@dataclass
class InvestigationReport:
    """Reporte exhaustivo generado por DiscoveryService sobre el estado físico del Job en el chat."""

    decision: ExecutionDecision
    valid_remotes: List["RemotePayload"] = field(default_factory=list)
    orphaned_remotes: List["RemotePayload"] = field(default_factory=list)
    missing_part_indices: List[int] = field(default_factory=list)
    total_parts: int = 0
    potential_duplicates: List[DuplicateCandidate] = field(default_factory=list)
    detail: str = ""

    @property
    def is_fulfilled(self) -> bool:
        return self.decision == ExecutionDecision.ALREADY_FULFILLED

    @property
    def can_seal(self) -> bool:
        return self.decision == ExecutionDecision.SEAL_HEALED


@dataclass
class StrategyResult:
    """Resultado devuelto por la ejecución física de subida."""

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
    decision: ExecutionDecision = ExecutionDecision.CLEAN_RESTART
    strategy_result: StrategyResult | None = None
    is_completed: bool = False
    snapshot_generated: bool = False
    message: str = ""
