"""
Jerarquía centralizada de excepciones de dominio para toTelegram.
Permite encapsular metadatos de presentación (título, hint, comandos sugeridos y severidad)
"""

from pathlib import Path
from typing import List, Literal

SeverityLevel = Literal["info", "warning", "error"]


class ToTelegramError(Exception):
    """
    Excepción raíz para todo el ecosistema toTelegram.

    Transporta los datos necesarios para que la capa CLI decida cómo renderizar
    el mensaje y determine el código de salida adecuado.
    """

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        title: str | None = None,
        hint: str | None = None,
        suggested_commands: List[str] | None = None,
        severity: SeverityLevel = "error",
    ):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.title = title
        self.hint = hint
        self.suggested_commands = suggested_commands or []
        self.severity = severity

    def __str__(self) -> str:
        return self.message


class DomainError(ToTelegramError):
    """Excepción base para violaciones de reglas de negocio o inconsistencias de datos."""


class ConfigurationError(ToTelegramError):
    """Excepción base para configuraciones faltantes, inválidas o destinos pendientes."""


class InfrastructureError(ToTelegramError):
    """Excepción base para problemas de I/O, permisos en Telegram o conectividad."""


class OperationAbortNotice(ToTelegramError):
    """
    Representa una terminación temprana controlada que no constituye un fallo técnico.
    Por defecto tiene exit_code = 0 y severidad 'info' o 'warning'.
    """

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 0,
        title: str | None = None,
        hint: str | None = None,
        suggested_commands: List[str] | None = None,
        severity: SeverityLevel = "info",
    ):
        super().__init__(
            message=message,
            exit_code=exit_code,
            title=title,
            hint=hint,
            suggested_commands=suggested_commands,
            severity=severity,
        )


class NoPendingCandidatesError(OperationAbortNotice):
    """Lanzada cuando un escaneo de inventario no encuentra candidatos para procesar."""

    def __init__(
        self,
        item_type: str = "archivo",
        destination_title: str | None = None,
    ):
        target_info = (
            f" para el destino '{destination_title}'" if destination_title else ""
        )
        super().__init__(
            message=f"No se encontraron {item_type}s pendientes para subir{target_info}.",
            exit_code=0,
            severity="warning",
            hint="Todos los elementos ya están respaldados en este destino o fueron omitidos por reglas de exclusión/integridad.",
        )


class TargetNotSetError(ConfigurationError):
    """Lanzada cuando se intenta operar sin un chat de destino especificado ni por defecto."""

    def __init__(self):
        super().__init__(
            message="No se ha especificado un chat de destino.",
            title="Destino requerido",
            hint=(
                "El perfil activo no tiene un chat por defecto y no indicaste '--target'.\n\n"
                "Tienes dos formas de continuar:\n"
                "1. Definir el destino solo para esta subida usando [bold]--target <chat>[/].\n"
                "2. Guardar un destino por defecto permanente usando [bold]totelegram target <chat>[/]."
            ),
            suggested_commands=[
                'totelegram send ... --target "Mi Canal"',
                "totelegram send ... --target me",
                'totelegram target "Mi Canal"',
            ],
            exit_code=1,
            severity="error",
        )


class TargetAmbiguousError(ConfigurationError):
    """Lanzada cuando una búsqueda de destino produce múltiples coincidencias."""

    def __init__(self, query: str, candidates: list):
        self.query = query
        self.candidates = candidates

        commands = []
        for c in candidates[:4]:
            commands.append(f'--target {c.id}  # Para: "{c.title}"')

        super().__init__(
            message=f"El destino '{query}' es ambiguo ({len(candidates)} coincidencias encontradas).",
            title="Destino Ambiguo",
            hint="Especifica el ID directo del chat para resolver la ambigüedad:",
            suggested_commands=commands,
            exit_code=1,
            severity="error",
        )


class PipelineCancelledError(OperationAbortNotice):
    """Lanzada cuando el usuario cancela interactivamente una operación destructiva."""

    def __init__(self, message: str = "Operación cancelada por el usuario."):
        super().__init__(
            message=message,
            exit_code=0,
            severity="info",
        )


# Excepciones movidas de  totelegram.types


class PipelineExecutionError(ToTelegramError):
    """Excepción base para fallos en la resolución o ejecución del pipeline."""


class IncompatibleCapacityError(DomainError):
    """Lanzada cuando un Job previo exige capacidades superiores a la sesión actual (ej. 4GB vs 2GB)."""

    def __init__(self, job_chunk_size: int, account_max_size: int, is_premium: bool):
        self.job_chunk_size = job_chunk_size
        self.account_max_size = account_max_size
        self.is_premium = is_premium

        job_mb = job_chunk_size // (1024 * 1024)
        acc_mb = account_max_size // (1024 * 1024)
        account_type = "Premium" if is_premium else "Estándar"

        super().__init__(
            message=(
                f"Este archivo tiene una subida pendiente iniciada con bloques de {job_mb} MB, "
                f"pero la cuenta activa actual es {account_type} y solo puede transferir bloques de hasta {acc_mb} MB."
            ),
            title="Incompatibilidad de Capacidad",
            hint="Para mantener la integridad matemática de los fragmentos, no se pueden mezclar trozos de diferente tamaño.",
            suggested_commands=[
                "totelegram switch <cuenta_premium>    # Cambiar a una cuenta con capacidad suficiente",
                "totelegram send ... --force           # Descartar intento previo y reiniciar con la capacidad actual",
            ],
            exit_code=1,
            severity="error",
        )


class PermissionDeniedCleanupError(InfrastructureError):
    """Lanzada cuando se requiere eliminar o reubicar mensajes pero faltan permisos de administrador."""

    def __init__(
        self,
        chat_id: int | str,
        reason: str = "Permiso 'can_delete_messages' requerido",
    ):
        self.chat_id = chat_id
        self.reason = reason

        super().__init__(
            message=(
                f"Se requiere limpiar o reorganizar mensajes antiguos en el chat {chat_id}, "
                f"pero la cuenta activa carece del permiso de administrador 'Eliminar mensajes' ({reason})."
            ),
            title="Permisos Insuficientes en Telegram",
            hint="toTelegram se detiene para proteger la higiene del canal y no dejar trozos huérfanos abandonados.",
            suggested_commands=[
                "# Otorga a esta cuenta el permiso 'Eliminar mensajes' en los ajustes de administradores del canal.",
                "totelegram send ... --use <cuenta_admin>  # Usar una cuenta que tenga permisos de administración",
            ],
            exit_code=1,
            severity="error",
        )


# Excepciones movidas de  totelegram.engine.planner
class JobPlanningError(DomainError):
    """Excepción base para errores de planificación de Jobs."""


class PathLengthExceededError(JobPlanningError):
    """Lanzada cuando una carpeta contiene rutas que exceden el estándar TAR."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(
            message=f"Error al empaquetar la carpeta: '{path.name}'.",
            title="Nombres de archivo demasiado largos",
            hint=(
                "El formato TAR tiene un límite estricto para la longitud de los nombres y rutas de archivos.\n"
                "Tienes dos opciones para resolver esto:\n"
                "1. Renombrar manualmente los archivos con rutas largas.\n"
                "2. Dejar que toTelegram trunque los nombres automáticamente al subirlos."
            ),
            suggested_commands=[
                f'totelegram backup "{path}" --auto-truncate',
                "totelegram config set auto_truncate true",
            ],
            exit_code=1,
            severity="error",
        )


# Excepciones movidas de  totelegram.concurrency


class ConcurrencyError(InfrastructureError):
    """Excepción base para conflictos de concurrencia."""


class AccountBusyError(ConcurrencyError):
    """Lanzada cuando una cuenta de Telegram está en uso por otro nodo/terminal."""

    def __init__(self, account_id: int, holder_node_id: str | None = None):
        self.account_id = account_id
        self.holder_node_id = holder_node_id
        super().__init__(
            message=f"La cuenta de Telegram '{account_id}' está en uso activo por el nodo: {holder_node_id or 'desconocido'}.",
            title="Cuenta en uso",
            hint="Espera a que la otra subida finalice o que expire el lease de concurrencia.",
            exit_code=1,
            severity="error",
        )


class PayloadClaimError(ConcurrencyError):
    """Lanzada cuando ocurre un error al reclamar una pieza."""


class SystemSuspendedError(ConcurrencyError):
    """Lanzada cuando se detecta que el sistema entró en suspensión/hibernación."""
