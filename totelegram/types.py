from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List

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


# --- EXCEPCIONES DE DOMINIO DE LA MÁQUINA DE ESTADOS ---


class PipelineExecutionError(Exception):
    """Excepción base para fallos en la resolución o ejecución del pipeline."""


class IncompatibleCapacityError(PipelineExecutionError):
    """Lanzada cuando un Job previo exige capacidades superiores a la sesión actual (ej. 4GB vs 2GB)."""

    def __init__(self, job_chunk_size: int, account_max_size: int, is_premium: bool):
        self.job_chunk_size = job_chunk_size
        self.account_max_size = account_max_size
        self.is_premium = is_premium
        super().__init__(
            f"El Job previo requiere partes de {job_chunk_size // (1024 * 1024)} MB, "
            f"pero la cuenta activa solo soporta {account_max_size // (1024 * 1024)} MB "
            f"({'Premium' if is_premium else 'Estándar'}). "
            f"Usa '--force' para descartar el Job y re-particionar, o cambia de cuenta."
        )


class PermissionDeniedCleanupError(PipelineExecutionError):
    """Lanzada cuando se requiere eliminar o reubicar mensajes pero faltan permisos de administrador."""

    def __init__(
        self,
        chat_id: int | str,
        reason: str = "Permiso 'can_delete_messages' requerido",
    ):
        self.chat_id = chat_id
        self.reason = reason
        super().__init__(
            f"No se pueden limpiar o reordenar los mensajes en el chat {chat_id}: {reason}. "
            f"Otorga permisos de eliminación a esta cuenta en Telegram para continuar."
        )


class PipelineCancelledError(PipelineExecutionError):
    """Lanzada cuando el usuario cancela interactivamente una operación destructiva."""


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
