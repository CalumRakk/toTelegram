import logging
import tempfile
import uuid
from pathlib import Path
from typing import cast

import typer
from pyrogram.errors import RPCError
from rich.panel import Panel

from totelegram.cli.state import CLIState
from totelegram.cli.ui import UI, console
from totelegram.database import DatabaseSession
from totelegram.exceptions import UnsupportedTargetTypeError
from totelegram.i18n import _
from totelegram.identity import (
    DEFAULT_TG_API_HASH,
    DEFAULT_TG_API_ID,
    generate_unique_alias,
    sanitize_alias,
)
from totelegram.models import TelegramUser
from totelegram.telegram.auth import AuthLogic
from totelegram.telegram.resolver import TargetResolver
from totelegram.telegram.types import TargetResolutionStatus

logger = logging.getLogger(__name__)

app = typer.Typer(help=_("Gestión de autenticación e identidades de Telegram."))


def _resolve_target_account(state: CLIState, name: str | None) -> str:
    """Resuelve el nombre del perfil objetivo (el indicado o el activo)."""
    if name:
        return name
    active = state.manager.get_active_profile_name()
    if not active:
        UI.error(_("No hay una sesión activa configurada."))
        UI.tip(_("Inicia sesión primero con: [bold]totelegram login[/]"))
        raise typer.Exit(1)
    return active


@app.command("login")
def login_command(ctx: typer.Context):
    """
    Inicia sesión con tu cuenta de Telegram de forma guiada e interactiva.
    Configura y activa automáticamente la sesión para su uso inmediato.
    """
    state: CLIState = ctx.obj
    manager = state.manager

    UI.separator()
    UI.print(_("[bold cyan]Inicio de Sesión en Telegram[/bold cyan]"), indent=False)
    UI.print(
        _(
            "[dim]Presiona [bold]Enter[/bold] en cada paso para usar los valores predeterminados.[/dim]\n"
        ),
        indent=False,
    )

    # Solicitar API ID
    api_id_raw = typer.prompt(
        _("1. Telegram API ID (my.telegram.org) [Enter para default]"),
        default="",
        show_default=False,
    )
    if api_id_raw.strip():
        try:
            final_api_id = int(api_id_raw.strip())
        except ValueError:
            UI.error(_("El API ID debe ser un número entero."))
            raise typer.Exit(1)
    else:
        final_api_id = DEFAULT_TG_API_ID

    # Solicitar API HASH
    api_hash_raw = typer.prompt(
        _("2. Telegram API HASH [Enter para default]"),
        default="",
        show_default=False,
        hide_input=True,
    )
    final_api_hash = (
        api_hash_raw.strip() if api_hash_raw.strip() else DEFAULT_TG_API_HASH
    )

    # Comprobación estricta por valor
    is_default_creds = (
        int(final_api_id) == DEFAULT_TG_API_ID
        and str(final_api_hash).strip() == DEFAULT_TG_API_HASH
    )
    has_custom_app = not is_default_creds

    # Si usa credenciales públicas, advertir de forma visible
    if is_default_creds:
        warning_text = _(
            "[bold cyan]ℹ️ Aviso sobre el uso de api_id y api_hash compartidas[/bold cyan]\n\n"
            "Estás a punto de iniciar sesión utilizando credenciales compartidas con otros usuarios.\n\n"
            "Para los servidores de Telegram, existe una relación técnica entre las sesiones que utilizan las mismas credenciales. "
            "Si alguien realiza un uso abusivo con las mismas credenciales, Telegram podría aplicar límites "
            "de velocidad temporales ([bold]FloodWait[/bold]) sobre las cuentas relacionadas.\n\n"
            "[bold]¿Alguien puede acceder a mis archivos al utilizar api_id y api_hash compartidas?[/bold]\n\n"
            "[green]No, absolutamente nadie.[/green] Las credenciales son solo el identificador de la aplicación desde la que te conectas "
            "(exactamente igual que cuando usas un cliente de terceros). "
            "Tus archivos en chats privados siguen siendo exclusivos para ti y tu sesión no se comparte con nadie.\n\n"
            "Para pruebas y uso rápido está bien, pero si planeas utilizar toTelegram para respaldos masivos, "
            "es altamente recomendable registrar tu propia app gratuita en [bold cyan]https://my.telegram.org[/bold cyan]."
        )
        console.print(Panel(warning_text, border_style="cyan", padding=(1, 2)))

        proceed = typer.confirm(
            _("\n¿Deseas continuar con estas credenciales?"), default=True
        )
        if not proceed:
            UI.info(
                _(
                    "Operación cancelada. Crea tu propia app en https://my.telegram.org y vuelve a intentar."
                )
            )
            raise typer.Exit(0)

    # Flujo interactivo de Pyrogram (Teléfono + OTP + 2FA)
    temp_session_name = f"temp_auth_{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory() as temp_dir:
        auth = AuthLogic(
            session_name=temp_session_name,
            api_id=final_api_id,
            api_hash=final_api_hash,
            workdir=Path(temp_dir),
        )
        try:
            console.print(_("\n[dim]Conectando con Telegram...[/dim]"))
            temp_session_path, account_id, first_name = auth.run_auth_flow()
        except Exception as e:
            UI.error(_("Error durante el inicio de sesión: {error}").format(error=e))
            raise typer.Exit(1)

        # Resolver Alias a partir del first_name obtenido de Telegram
        existing_profiles = manager._get_all_profile_names()
        suggested_alias = generate_unique_alias(first_name, existing_profiles)

        console.print()
        UI.success(
            _("Conexión verificada con éxito como [bold]{first_name}[/].").format(
                first_name=first_name
            )
        )
        alias_raw = typer.prompt(
            _("3. Alias o nombre para identificar esta cuenta en tu equipo"),
            default=suggested_alias,
        )
        final_alias = sanitize_alias(alias_raw)

        # Prevenir colisión si el usuario escribió manualmente un nombre ya existente
        if final_alias in existing_profiles:
            UI.warn(
                _("El alias '[bold]{alias}[/]' ya existe.").format(alias=final_alias)
            )
            final_alias = generate_unique_alias(final_alias, existing_profiles)
            UI.info(
                _("Se asignará automáticamente: [bold cyan]{alias}[/]").format(
                    alias=final_alias
                )
            )

        # Mover archivo de sesión (.session) y persistir configuración
        final_session_path = manager.get_session_path(final_alias)
        manager.profiles_dir.mkdir(parents=True, exist_ok=True)
        if temp_session_path.exists():
            temp_session_path.rename(final_session_path)

        settings_dict = {
            "api_id": final_api_id,
            "api_hash": final_api_hash,
            "profile_name": final_alias,
            "telegram_account_id": account_id,
            "has_custom_app": has_custom_app,
        }
        manager._write_all_settings(final_alias, settings_dict)

    # Auto-activación inmediata
    manager.set_profile_name_as_active(final_alias)

    UI.separator()
    UI.success(
        _("¡Sesión iniciada con éxito! Cuenta [bold green]{alias}[/] activada.").format(
            alias=final_alias
        )
    )
    UI.educational_tip(
        title=_("Próximo paso sugerido"),
        message=_(
            "Tu cuenta ya está lista para operar.\n"
            "Puedes enviar o respaldar archivos indicando el destino con [bold]-t[/]:"
        ),
        commands=[
            "totelegram send video.mp4 -t me               # "
            + _("Enviar a tus Mensajes Guardados"),
            'totelegram send video.mp4 -t "Mi Canal"       # '
            + _("Enviar a un canal de difusión"),
            'totelegram backup ./mis_datos -t "Backups"    # '
            + _("Respaldar carpeta completa"),
        ],
        spacing="block",
        border_style="green",
    )


@app.command("status")
def status_command(
    ctx: typer.Context,
    path: Path | None = typer.Argument(
        None,
        help=_(
            "Ruta a una carpeta o archivo para auditar su estado de respaldo local. "
            "Si se omite, muestra el estado de la cuenta activa."
        ),
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        "-t",
        help=_("Filtrar auditoría por un destino de Telegram específico."),
    ),
    recursive: bool = typer.Option(
        False,
        "--recursive",
        "-r",
        help=_("Auditar subdirectorios recursivamente mostrando rutas relativas."),
    ),
):
    """
    Muestra el estado de la cuenta activa o audita los archivos respaldados de una carpeta local.
    """
    state: CLIState = ctx.obj
    manager = state.manager

    # MODO A: Auditoría de archivos locales si se proporcionó una ruta
    if path is not None:
        target_path = path.resolve()
        resolved_chat_id: int | str | None = None

        if target:
            with state.scope() as (client, db):
                resolver = TargetResolver(client, db)
                res = resolver.resolve(target)
                if res.is_resolved:
                    resolved_chat_id = res.chat_id
                elif res.status == TargetResolutionStatus.UNSUPPORTED_TYPE:
                    raise UnsupportedTargetTypeError(
                        chat_id=res.chat_id or target,
                        chat_title=res.title,
                        chat_type=res.match.type if res.match else None,
                    )
                else:
                    UI.error(
                        res.error_message
                        or _("Destino '{target}' inválido.").format(target=target)
                    )
                    raise typer.Exit(1)

        from totelegram.cli.views.ledger import DisplayLedger
        from totelegram.packaging.auditor import LocalAuditService

        if not target_path.exists():
            UI.error(_("La ruta '{path}' no existe.").format(path=path))
            raise typer.Exit(1)

        with DatabaseSession(manager.database_path, auto_init_schema=True):
            audit_list = LocalAuditService.audit_path(
                target_path, target_chat_id=resolved_chat_id, recursive=recursive
            )

        display_dir = target_path if target_path.is_dir() else target_path.parent
        UI.separator()
        DisplayLedger.show_directory_status(
            display_dir, audit_list, target_chat_id=resolved_chat_id
        )
        return

    # MODO B: Estado de la cuenta de Telegram (Comportamiento habitual)
    active_name = manager.get_active_profile_name()
    if not active_name or not manager.settings_exists(active_name):
        UI.warn(_("No hay una cuenta activa configurada."))
        UI.tip(_("Inicia sesión ejecutando:"), commands=["totelegram login"])
        raise typer.Exit(0)

    settings = manager.get_settings(active_name)

    UI.separator()
    with (
        state.get_telegram_session(active_name) as client,
        UI.loading(_("Consultando estado de cuenta en Telegram...")),
    ):
        me = cast("TelegramUser", client.get_me())

    tier_label = (
        _("[bold cyan]Premium ★[/]")
        if getattr(me, "is_premium", False)
        else _("Estándar")
    )
    creds_label = (
        _("[bold green]Propias (Custom App)[/]")
        if settings.has_custom_app
        else _("[bold yellow]Públicas compartidas (Default)[/]")
    )

    content = [
        f"• {_('Cuenta activa')}: [bold green]{active_name}[/]",
        f"• {_('Usuario')}:       [bold]{me.first_name}[/] [dim](@{me.username or _('sin_alias')})[/]",
        f"• {_('Account ID')}:    [dim]{me.id}[/]",
        f"• {_('Telegram')}:      {tier_label}",
        f"• {_('Credenciales')}:  {creds_label}",
    ]

    panel_title = _("[bold cyan]Estado de toTelegram — {name}[/]").format(
        name=active_name
    )
    panel = Panel(
        "\n".join(content),
        title=panel_title,
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)


@app.command("switch")
def switch_command(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None,
        help=_(
            "Nombre de la cuenta a la cual cambiar. Si se omite, se muestra un selector interactivo."
        ),
    ),
):
    """
    Cambia la cuenta activa del sistema entre las cuentas guardadas.
    """
    state: CLIState = ctx.obj
    manager = state.manager

    profiles = manager.get_all_profiles()
    valid_profiles = [p for p in profiles if p.is_trinity]

    if not valid_profiles:
        UI.warn(_("No hay cuentas guardadas en el sistema."))
        UI.tip(_("Inicia sesión con:"), commands=["totelegram login"])
        raise typer.Exit(0)

    target_name = name
    if not target_name:
        console.print(_("\n[bold cyan]Cuentas disponibles:[/bold cyan]"))
        active = manager.get_active_profile_name()
        for idx, p in enumerate(valid_profiles, 1):
            marker = "[bold green]*[/]" if p.name == active else " "
            console.print(f" {marker} [{idx}] [bold]{p.name}[/]")

        while True:
            choice = typer.prompt(
                _("\nSelecciona el número de cuenta (o '0' para cancelar)"),
                default="1",
            )
            if choice.strip() == "0":
                UI.info(_("Operación cancelada."))
                raise typer.Exit(0)
            try:
                sel_idx = int(choice) - 1
                if 0 <= sel_idx < len(valid_profiles):
                    target_name = valid_profiles[sel_idx].name
                    break
            except ValueError:
                pass
            console.print(_("[red]Opción inválida.[/]"))

    assert target_name is not None
    profile = manager.get_profile(target_name)
    if not profile or not profile.is_trinity:
        UI.error(
            _("La cuenta '[bold]{name}[/]' no existe o está incompleta.").format(
                name=target_name
            )
        )
        raise typer.Exit(1)

    manager.set_profile_name_as_active(target_name)
    UI.success(
        _("Cuenta activa cambiada a: [bold green]{name}[/]").format(name=target_name)
    )


@app.command("logout")
def logout_command(
    ctx: typer.Context,
    name: str | None = typer.Argument(
        None,
        help=_("Nombre de la cuenta a cerrar sesión (por defecto: la cuenta activa)."),
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help=_("Confirma el cierre de sesión sin solicitar confirmación interactiva."),
    ),
    local_only: bool = typer.Option(
        False,
        "--local-only",
        help=_(
            "Elimina la sesión localmente sin revocar la clave en los servidores de Telegram."
        ),
    ),
):
    """
    Cierra la sesión y elimina los archivos de credenciales del perfil.
    Por defecto, revoca la sesión remota en Telegram.
    """
    state: CLIState = ctx.obj
    manager = state.manager

    target_account = _resolve_target_account(state, name)
    profile = manager.get_profile(target_account)
    if not profile:
        UI.error(
            _("No se encontró la cuenta '{account}'.").format(account=target_account)
        )
        raise typer.Exit(1)

    if not yes:
        console.print()
        action_desc = (
            _("eliminar localmente")
            if local_only
            else _("cerrar sesión en Telegram y eliminar")
        )
        confirmed = typer.confirm(
            _(
                "¿Estás seguro de que deseas {action} la cuenta '[bold]{account}[/]?'"
            ).format(action=action_desc, account=target_account),
            default=False,
        )
        if not confirmed:
            UI.info(_("Operación cancelada."))
            raise typer.Exit(0)

    # Revocación remota en Telegram (salvo --local-only)
    if not local_only and profile.has_session:
        with UI.loading(_("Revocando sesión en los servidores de Telegram...")):
            try:
                with state.get_telegram_session(target_account) as client:
                    client.log_out()  # type: ignore
                    UI.info(_("Sesión remota invalidada exitosamente."))
            except RPCError as e:
                logger.warning(f"Aviso al revocar sesión en Telegram: {e}")
            except Exception as e:
                logger.debug(f"Error cerrando cliente en logout: {e}")

    # Eliminación física local
    deleted_files = manager.delete_profile(profile)
    UI.success(
        _("Cuenta '[bold]{account}[/]' eliminada del equipo.").format(
            account=target_account
        )
    )
    for f in deleted_files:
        UI.print(_("  [dim]- Archivo eliminado: {name}[/]").format(name=f.name))

    # Notificar siguiente estado si era la cuenta activa
    active = manager.get_active_profile_name()
    if active == target_account or not active:
        remaining = manager.get_all_profiles()
        valid = [p for p in remaining if p.is_trinity]
        if valid:
            manager.set_profile_name_as_active(valid[0].name)
            UI.info(
                _(
                    "Cuenta activa reasignada automáticamente a: [bold cyan]{name}[/]"
                ).format(name=valid[0].name)
            )
        else:
            UI.info(_("No quedan más cuentas activas configuradas."))


@app.command("list")
def list_accounts(
    ctx: typer.Context,
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help=_("Muestra solo los nombres de cuentas")
    ),
):
    """
    Lista todas las cuentas registradas en este equipo.
    """
    from totelegram.cli.views.settings import DisplayProfile

    state: CLIState = ctx.obj
    manager = state.manager

    profiles = manager.get_all_profiles()
    if not profiles:
        UI.warn(_("No hay cuentas configuradas en el sistema."))
        UI.tip(_("Inicia sesión con:"), commands=["totelegram login"])
        return

    active = manager.get_active_profile_name()
    DisplayProfile.render_profiles_table(manager, active, profiles, quiet=quiet)
