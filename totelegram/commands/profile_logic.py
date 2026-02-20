import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, cast

import typer
from rich.rule import Rule
from rich.table import Table

if TYPE_CHECKING:
    from pyrogram import Client # type: ignore

from totelegram.console import UI, console
from totelegram.core.registry import Profile, SettingsManager
from totelegram.services.chat_resolver import (
    ChatMatch,
    ChatResolution,
    ChatResolverService,
)
from totelegram.services.validator import ValidationService
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
    def __init__(self, manager: SettingsManager, profile_name: str) -> None:
        self.manager = manager
        self.profile_name = profile_name

    def proccess_login(self, profile_name:str, api_id:int, api_hash:str) -> None:
        try:
            final_session_path = self.manager.profiles_dir / f"{profile_name}.session"
            validator = ValidationService()

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_session_path = Path(temp_dir) / f"{profile_name}.session"
                UI.info("\n[bold cyan]1. Autenticación con Telegram[/bold cyan]")
                UI.info(
                    "[dim]Se solicitará tu número telefónico y código (OTP) para vincular la cuenta.[/dim]\n"
                )

                with validator.validate_session(
                    temp_dir, profile_name, api_id, api_hash
                ):
                    # Una vez dentro ya no necesitamos la session temp, salimos para liberar el archivo
                    pass


                if not temp_session_path.exists():
                    raise FileNotFoundError(
                        "Error crítico: No se generó el archivo de sesión."
                    )

                self.manager.profiles_dir.mkdir(parents=True, exist_ok=True)
                temp_session_path.rename(final_session_path)
                UI.success(
                    f"[green]Identidad salvada correctamente en {profile_name}.session[/green]"
                )

                settings_dict = {
                    "api_id": api_id,
                    "api_hash": api_hash,
                    "profile_name": profile_name,
                    "chat_id": VALUE_NOT_SET,
                }
                self.manager._write_all_settings(profile_name, settings_dict)

                UI.success(
                    f"[green]Identidad salvada correctamente en {profile_name}.session[/green]"
                )
        except Exception as e:
            UI.error(f"Operación abortada durante el login: {e}")
            raise typer.Exit(code=1)

    def _procces_winner(self, client:"Client", validator:ValidationService, result:ChatResolution):
        winner= cast(ChatMatch, result.winner)

        final_chat_id = str(winner.id)
        UI.success(f"Destino encontrado: [bold]{winner.title}[/]")

        with UI.loading("Verificando permisos..."):
            has_perms = validator.validate_send_action(client, winner.id)

        if not has_perms:
            UI.warn(
                "Destino guardado, pero actualmente NO TIENES permisos de escritura."
            )
            UI.info(
                "[dim]Las subidas fallarán hasta que obtengas permisos en ese chat.[/dim]"
            )
        return final_chat_id

    def procces_dest(self,
            validator: ValidationService,
            manager: SettingsManager,
            profile_name:str,
            api_id:int,
            api_hash:str,
            chat_id: Optional[str]
    ) -> str:

        console.print(Rule(style="dim"))
        UI.info("[bold cyan]2. Configuración del Destino[/bold cyan]")

        final_chat_id = VALUE_NOT_SET
        try:
            with validator.validate_session(
                manager.profiles_dir, profile_name, api_id, api_hash
            ) as client:
                resolver = ChatResolverService(client)

                if chat_id != VALUE_NOT_SET:
                    with UI.loading(f"Resolviendo '{chat_id}'..."):
                        result = resolver.resolve(chat_id)  # type: ignore

                    if result.is_resolved and result.winner:
                        final_chat_id= self._procces_winner(client, validator, result)
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

    def validate_profile_exists(self, profile_name: str):
        """Si el profile no existe, lanza error."""
        existing_profile = self.manager.get_profile(profile_name)
        if existing_profile is not None:
            UI.error(f"No se puede crear el perfil '{profile_name}'.")

            if existing_profile.is_trinity:
                UI.warn("El perfil existe.")
            elif existing_profile.has_session:
                UI.warn("Existe una sesión de Telegram huérfana con este nombre.")
            elif existing_profile.has_env:
                UI.warn(
                    "Existe un archivo de configuración (.env) sin sesión asociada."
                )

            UI.info("\nPara empezar de cero, elimina los rastros primero usando:")
            UI.info(f"  [cyan]totelegram profile delete {profile_name}[/cyan]")
            raise typer.Exit(code=1)

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
