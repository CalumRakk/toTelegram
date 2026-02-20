import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import typer

from totelegram.core.schemas import CLIState
from totelegram.telegram import TelegramSession

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore

from totelegram.console import UI
from totelegram.utils import VALUE_NOT_SET


class AuthLogic:
    def __init__(
        self, state: CLIState, api_id: int, api_hash: str, chat_id: Optional[str]
    ) -> None:
        self.state = state
        self.api_id = api_id
        self.api_hash = api_hash
        self.chat_id = chat_id

    def _validate_profile_exists(
        self,
    ):
        """Si el profile no existe, lanza error."""
        profile_name = self.state.settings_name
        assert profile_name is not None
        existing_profile = self.state.manager.get_profile(profile_name)
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

    def proccess(self) -> None:
        try:

            self._validate_profile_exists()

            manager = self.state.manager
            settings_name = self.state.settings_name
            assert settings_name is not None, "settings_name no puede ser None"

            final_session_path = manager.profiles_dir / f"{settings_name}.session"
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_session_path = Path(temp_dir) / f"{settings_name}.session"
                UI.info("\n[bold cyan]1. Autenticación con Telegram[/bold cyan]")
                UI.info(
                    "[dim]Se solicitará tu número telefónico y código (OTP) para vincular la cuenta.[/dim]\n"
                )

                with TelegramSession(
                    session_name=settings_name,
                    api_id=self.api_id,
                    api_hash=self.api_hash,
                    worktable=temp_session_path,
                ):
                    # Una vez dentro ya no necesitamos la session temp, salimos para liberar el archivo
                    pass

                if not temp_session_path.exists():
                    raise FileNotFoundError(
                        "Error crítico: No se generó el archivo de sesión."
                    )

                manager.profiles_dir.mkdir(parents=True, exist_ok=True)
                temp_session_path.rename(final_session_path)
                UI.success(
                    f"[green]Identidad salvada correctamente en {settings_name}.session[/green]"
                )

                settings_dict = {
                    "api_id": self.api_id,
                    "api_hash": self.api_hash,
                    "profile_name": settings_name,
                    "chat_id": VALUE_NOT_SET,
                }
                manager._write_all_settings(settings_name, settings_dict)

                UI.success(
                    f"[green]Identidad salvada correctamente en {settings_name}.session[/green]"
                )
        except Exception as e:
            UI.error(f"Operación abortada durante el login: {e}")
            raise typer.Exit(code=1)
