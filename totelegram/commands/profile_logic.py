from typing import TYPE_CHECKING, List, Optional, cast

from rich.rule import Rule
from rich.table import Table

from totelegram.core.schemas import ChatMatch, ChatResolution, CLIState
from totelegram.services.chat_access import ChatAccessService
from totelegram.telegram import TelegramSession

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore

from totelegram.console import UI, console
from totelegram.core.registry import Profile, SettingsManager
from totelegram.utils import VALUE_NOT_SET


def render_profiles_table(
    manager: SettingsManager,
    active: Optional[str],
    profiles: List[Profile],
    quiet: bool = False,
):
    console.print()
    if quiet:
        console.print("Perfiles disponibles de toTelegram:")
        for profile in profiles:
            console.print(" - " + profile.name)
        return

    table = Table(
        title="Perfiles disponibles de toTelegram",
        title_style="bold magenta",
    )
    table.add_column("Estado", style="cyan", no_wrap=True)
    table.add_column("Perfil", style="magenta")
    table.add_column("Session (.session)", style="green")
    table.add_column("Config (.env)", style="green")
    table.add_column("Destino (Chat ID)", style="green")

    was_orphan = False
    for profile in profiles:
        is_active = profile.name == active

        active_marker = "[bold green]*[/]" if is_active else ""
        auth_status = (
            "[green][ OK ][/]" if profile.has_session else "[red][ MISSING ][/]"
        )

        if profile.has_env:
            settings = manager.get_settings(profile.name)
            chat_id = settings.chat_id

            config_status = "[green][ OK ][/]"
            target_desc = (
                f"[white]{chat_id}[/]"
                if chat_id != VALUE_NOT_SET
                else "[yellow]Pendiente[/]"
            )
        else:
            config_status = "[red][ MISSING ][/]"
            target_desc = "[dim]--[/]"

        if not profile.is_trinity:
            was_orphan = True

        table.add_row(
            active_marker, profile.name, auth_status, config_status, target_desc
        )

    console.print(table)

    if was_orphan:
        console.print()
        UI.warn("Se detecto al menos un perfil huérfano.")
        UI.info(
            f"Usa 'totelegram profile delete <PERFIL>' para limpiar archivos huérfanos"
        )


class ProfileCreateLogic:
    def __init__(
        self, state: CLIState, api_id: int, api_hash: str, chat_id: Optional[str]
    ) -> None:
        self.state = state
        self.api_id = api_id
        self.api_hash = api_hash
        self.chat_id = chat_id

    # def proccess_login(self) -> None:
    #     try:
    #         manager = self.state.manager
    #         settings_name = self.state.settings_name
    #         assert settings_name is not None, "settings_name no puede ser None"

    #         final_session_path = manager.profiles_dir / f"{settings_name}.session"
    #         with tempfile.TemporaryDirectory() as temp_dir:
    #             temp_session_path = Path(temp_dir) / f"{settings_name}.session"
    #             UI.info("\n[bold cyan]1. Autenticación con Telegram[/bold cyan]")
    #             UI.info(
    #                 "[dim]Se solicitará tu número telefónico y código (OTP) para vincular la cuenta.[/dim]\n"
    #             )

    #             with TelegramSession(
    #                 session_name=settings_name,
    #                 api_id=self.api_id,
    #                 api_hash=self.api_hash,
    #                 worktable=temp_session_path,
    #             ):
    #                 # Una vez dentro ya no necesitamos la session temp, salimos para liberar el archivo
    #                 pass

    #             if not temp_session_path.exists():
    #                 raise FileNotFoundError(
    #                     "Error crítico: No se generó el archivo de sesión."
    #                 )

    #             manager.profiles_dir.mkdir(parents=True, exist_ok=True)
    #             temp_session_path.rename(final_session_path)
    #             UI.success(
    #                 f"[green]Identidad salvada correctamente en {settings_name}.session[/green]"
    #             )

    #             settings_dict = {
    #                 "api_id": self.api_id,
    #                 "api_hash": self.api_hash,
    #                 "profile_name": settings_name,
    #                 "chat_id": VALUE_NOT_SET,
    #             }
    #             manager._write_all_settings(settings_name, settings_dict)

    #             UI.success(
    #                 f"[green]Identidad salvada correctamente en {settings_name}.session[/green]"
    #             )
    #     except Exception as e:
    #         UI.error(f"Operación abortada durante el login: {e}")
    #         raise typer.Exit(code=1)

    def _procces_winner(self, chat_access: ChatAccessService, result: ChatResolution):
        winner = cast(ChatMatch, result.winner)

        final_chat_id = str(winner.id)
        UI.success(f"Destino encontrado: [bold]{winner.title}[/]")

        accces_report = chat_access.verify_access(winner.id)

        if not accces_report.is_ready:
            UI.warn(
                "Destino guardado, pero actualmente NO TIENES permisos de escritura."
            )
            UI.info(
                "[dim]Las subidas fallarán hasta que obtengas permisos en ese chat.[/dim]"
            )
        return final_chat_id

    def procces_dest(
        self,
    ) -> str:

        console.print(Rule(style="dim"))
        UI.info("[bold cyan]2. Configuración del Destino[/bold cyan]")

        final_chat_id = VALUE_NOT_SET
        try:
            settings_name = self.state.settings_name
            manager = self.state.manager
            assert settings_name is not None, "settings_name no puede ser None"

            with TelegramSession(
                session_name=settings_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                worktable=manager.profiles_dir,
            ) as client:

                resolver = ChatAccessService(client)

                if self.chat_id != VALUE_NOT_SET:
                    with UI.loading(f"Resolviendo '{self.chat_id}'..."):
                        result = resolver.resolve(chat_id)  # type: ignore

                    if result.is_resolved and result.winner:
                        final_chat_id = self._procces_winner(client, validator, result)
                    else:
                        UI.warn(
                            f"No se pudo resolver '{chat_id}' de forma exacta (posible ambigüedad o no existe)."
                        )
                else:
                    # POR IMPLEMENTAR
                    final_chat_id = _capture_chat_id_wizard(validator, client)

        except Exception as e:
            UI.error(f"Error consultando a Telegram: {e}")
            UI.info("El destino no pudo ser configurado ahora.")

        return final_chat_id

    def store_chat_id_and_active_profile(self, final_chat_id: str):

        console.print(Rule(style="dim"))

        if final_chat_id != VALUE_NOT_SET:
            self.manager.set_setting(self.profile_name, "chat_id", final_chat_id)
            UI.success(f"Destino configurado exitosamente.")
        else:
            UI.warn("Perfil creado sin destino.")
            UI.info(
                "Usa [cyan]totelegram config set chat_id <ID>[/cyan] cuando estés listo."
            )

        self.manager.set_settings_name_as_active(self.profile_name)
        UI.success(
            f"[bold green]¡Perfil '{self.profile_name}' activado y listo para usar![/bold green]\n"
        )
