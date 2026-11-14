"""
Jerarquía centralizada de excepciones de dominio para toTelegram.
Permite encapsular metadatos de presentación (título, hint, comandos sugeridos y severidad)
adaptados al sistema internacional de localización (i18n).
"""

from pathlib import Path
from typing import Literal

from totelegram.i18n import _

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
        suggested_commands: list[str] | None = None,
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
        suggested_commands: list[str] | None = None,
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
        is_folder = str(item_type).lower() in ("carpeta", "folder")

        if destination_title:
            if is_folder:
                msg = _(
                    "No se encontraron carpetas pendientes para subir para el destino '{destination}'."
                ).format(destination=destination_title)
            else:
                msg = _(
                    "No se encontraron archivos pendientes para subir para el destino '{destination}'."
                ).format(destination=destination_title)
        else:
            if is_folder:
                msg = _("No se encontraron carpetas pendientes para subir.")
            else:
                msg = _("No se encontraron archivos pendientes para subir.")

        super().__init__(
            message=msg,
            exit_code=0,
            severity="warning",
            hint=_(
                "Todos los elementos ya están respaldados en este destino o fueron omitidos por reglas de exclusión/integridad."
            ),
        )


class TargetNotSetError(ConfigurationError):
    """Lanzada cuando se intenta operar sin un chat de destino especificado."""

    def __init__(self):
        super().__init__(
            message=_(
                "No indicaste el destino. Usa [bold cyan]-t <chat>[/] o [bold cyan]-t me[/] para el chat de Mensajes Guardados.\n"
            ),
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
            comment = _('Para: "{title}"').format(title=c.title)
            commands.append(f"--target {c.id}  # {comment}")

        super().__init__(
            message=_(
                "El destino '{query}' es ambiguo ({count} coincidencias encontradas)."
            ).format(query=query, count=len(candidates)),
            title=_("Destino Ambiguo"),
            hint=_("Especifica el ID directo del chat para resolver la ambigüedad:"),
            suggested_commands=commands,
            exit_code=1,
            severity="error",
        )


class PipelineCancelledError(OperationAbortNotice):
    """Lanzada cuando el usuario cancela interactivamente una operación destructiva."""

    def __init__(self, message: str | None = None):
        super().__init__(
            message=message or _("Operación cancelada por el usuario."),
            exit_code=0,
            severity="info",
        )


class PipelineExecutionError(ToTelegramError):
    """Excepción base para fallos en la resolución o ejecución del pipeline."""


class IncompatibleCapacityError(DomainError):
    """Lanzada cuando un Job previo exige capacidades superiores a la sesión actual (ej. 4GB vs 2GB)."""

    def __init__(
        self,
        job_chunk_size: int,
        account_max_size: int,
        is_premium: bool,
        pending_payload_size: int | None = None,
    ):
        self.job_chunk_size = job_chunk_size
        self.account_max_size = account_max_size
        self.is_premium = is_premium
        self.pending_payload_size = pending_payload_size

        size_to_report = pending_payload_size or job_chunk_size
        part_mb = size_to_report // (1024 * 1024)
        acc_mb = account_max_size // (1024 * 1024)
        account_type = _("Premium") if is_premium else _("Estándar")

        super().__init__(
            message=_(
                "Este archivo tiene una parte pendiente de {part_mb} MB, "
                "pero la cuenta activa actual es {account_type} y solo puede transferir bloques de hasta {acc_mb} MB."
            ).format(part_mb=part_mb, account_type=account_type, acc_mb=acc_mb),
            title=_("Incompatibilidad de Capacidad"),
            hint=_(
                "Para mantener la integridad matemática de los fragmentos, no se pueden mezclar trozos de diferente tamaño."
            ),
            suggested_commands=[
                "totelegram switch <cuenta_premium>    # "
                + _("Cambiar a una cuenta con capacidad suficiente"),
                "totelegram send ... --force           # "
                + _("Descartar intento previo y reiniciar con la capacidad actual"),
            ],
            exit_code=1,
            severity="error",
        )


class PermissionDeniedCleanupError(InfrastructureError):
    """Lanzada cuando se requiere eliminar o reubicar mensajes pero faltan permisos de administrador."""

    def __init__(
        self,
        chat_id: int | str,
        reason: str | None = None,
    ):
        self.chat_id = chat_id
        resolved_reason = reason or _("Permiso 'can_delete_messages' requerido")
        self.reason = resolved_reason

        super().__init__(
            message=_(
                "Se requiere limpiar o reorganizar mensajes antiguos en el chat {chat_id}, "
                "pero la cuenta activa carece del permiso de administrador 'Eliminar mensajes' ({reason})."
            ).format(chat_id=chat_id, reason=resolved_reason),
            title=_("Permisos Insuficientes en Telegram"),
            hint=_(
                "toTelegram se detiene para proteger la higiene del canal y no dejar trozos huérfanos abandonados."
            ),
            suggested_commands=[
                "# "
                + _(
                    "Otorga a esta cuenta el permiso 'Eliminar mensajes' en los ajustes de administradores del canal."
                ),
                "totelegram send ... --use <cuenta_admin>  # "
                + _("Usar una cuenta que tenga permisos de administración"),
            ],
            exit_code=1,
            severity="error",
        )


class JobPlanningError(DomainError):
    """Excepción base para errores de planificación de Jobs."""


class PathLengthExceededError(JobPlanningError):
    """Lanzada cuando una carpeta contiene rutas que exceden el estándar TAR."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(
            message=_("Error al empaquetar la carpeta: '{name}'.").format(
                name=path.name
            ),
            title=_("Nombres de archivo demasiado largos"),
            hint=_(
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


class ConcurrencyError(InfrastructureError):
    """Excepción base para conflictos de concurrencia."""


class AccountBusyError(ConcurrencyError):
    """Lanzada cuando una cuenta de Telegram está en uso por otro nodo/terminal."""

    def __init__(self, account_id: int, holder_node_id: str | None = None):
        self.account_id = account_id
        self.holder_node_id = holder_node_id
        holder = holder_node_id or _("desconocido")
        super().__init__(
            message=_(
                "La cuenta de Telegram '{account_id}' está en uso activo por el nodo: {holder}."
            ).format(account_id=account_id, holder=holder),
            title=_("Cuenta en uso"),
            hint=_(
                "Espera a que la otra subida finalice o que expire el lease de concurrencia."
            ),
            exit_code=1,
            severity="error",
        )


class PayloadClaimError(ConcurrencyError):
    """Lanzada cuando ocurre un error al reclamar una pieza."""


class SystemSuspendedError(ConcurrencyError):
    """Lanzada cuando se detecta que el sistema entró en suspensión/hibernación."""

    def __init__(self, message: str | None = None):
        super().__init__(
            message=message
            or _(
                "El sistema operativo se suspendió o hibernó durante la ejecución. "
                "Abortando para proteger la integridad de los datos y reconectar limpiamente."
            ),
            title=_("Sistema Suspendido"),
            severity="error",
        )


class UnsupportedTargetTypeError(ConfigurationError):
    """Lanzada cuando el destino resuelto no es un Canal de difusión ni Mensajes Guardados ('me')."""

    def __init__(
        self,
        chat_id: int | str,
        chat_title: str | None = None,
        chat_type: str | None = None,
    ):
        self.chat_id = chat_id
        self.chat_title = chat_title or str(chat_id)
        self.chat_type = str(chat_type).lower() if chat_type else "desconocido"

        type_labels = {
            "group": _("Grupo"),
            "supergroup": _("Supergrupo"),
            "private": _("Chat privado"),
            "bot": _("Bot"),
        }
        type_label = type_labels.get(self.chat_type, self.chat_type.capitalize())

        commands = [
            "# "
            + _(
                "1. Crea un canal privado en Telegram y añade tu cuenta como Administrador."
            ),
            "# "
            + _(
                "2. Asegúrate de conceder permisos de publicación y eliminación de mensajes."
            ),
            'totelegram target "Nombre de tu Canal"  # '
            + _("Para verificar sus permisos y acceso"),
            "totelegram send ... -t me              # "
            + _("Para enviar a tu chat de Mensajes Guardados"),
        ]

        super().__init__(
            message=_(
                "El destino '[bold]{chat_title}[/]' es de tipo [bold magenta]{type_label}[/], "
                "pero toTelegram solo admite [bold cyan]Canales de difusión[/] o [bold cyan]Mensajes Guardados ('me')[/]."
            ).format(chat_title=self.chat_title, type_label=type_label),
            title=_("Destino No Compatible"),
            hint=_(
                "Los canales operan como un almacén de objetos y evitan la mezcla visual "
                "entre cuentas y permiten que cualquier cuenta edite o reorganice metadatos. "
                "Los grupos de Telegram no soportan estas capacidades."
            ),
            suggested_commands=commands,
            exit_code=1,
            severity="error",
        )
