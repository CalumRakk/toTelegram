from pathlib import Path
from typing import TYPE_CHECKING, Optional

from totelegram.core.registry import Profile, SettingsManager
from totelegram.telegram import TelegramSession

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore

from totelegram.utils import VALUE_NOT_SET


class AuthLogic:
    def __init__(
        self,
        profile_name: str,
        temp_dir: Path | str,
        api_id: int,
        api_hash: str,
        manager: SettingsManager,
        chat_id: Optional[str] = None,
    ) -> None:
        self.profile_name = profile_name
        self.temp_dir = Path(temp_dir)
        self.api_id = api_id
        self.api_hash = api_hash
        self.manager = manager
        self.chat_id = chat_id

    def _create_session(self) -> Path:
        """
        Genera una sesión autenticada en el directorio temporal (self.temp_dir).

        Verifica que no exista previamente una sesión definitiva para el perfil
        y crea el archivo `.session` usando las credenciales proporcionadas.

        Returns:
            Path: La ruta del archivo de sesión generado.

        Raises:
            FileExistsError: Si el archivo de sesión ya existe.
        """
        final_session_path = self.manager.get_session_path(self.profile_name)
        if final_session_path.exists():
            raise FileExistsError(
                f"El archivo de sesión {final_session_path} ya existe."
            )

        temp_session_path = self.temp_dir / f"{self.profile_name}.session"
        with TelegramSession(
            session_name=self.profile_name,
            api_id=self.api_id,
            api_hash=self.api_hash,
            profiles_dir=self.temp_dir,
        ):
            # Una vez dentro ya no necesitamos la session temp, salimos para liberar el archivo
            pass
        return temp_session_path

    def _persist_session(self, temp_session_path: Path):
        """
        Mueve la sesión desde el directorio temporal al directorio definitivo
        de perfiles.

        Valida que el archivo temporal exista antes de persistirlo.

        Args:
            temp_session_path (Path): La ruta del archivo de sesión temporal.

        Returns:
            Path: La ruta del archivo de sesión definitivo.

        Raises:
            FileNotFoundError: Si el archivo de sesión temporal no existe.
        """
        final_session_path = self.manager.get_session_path(self.profile_name)
        if not temp_session_path.exists():
            raise FileNotFoundError("No se encontró el archivo de sesión.")

        self.manager.profiles_dir.mkdir(parents=True, exist_ok=True)
        temp_session_path.rename(final_session_path)
        return final_session_path

    def _write_profile_settings(self):
        """Crea y guarda el archivo de configuración asociado al perfil."""
        settings_dict = {
            "api_id": self.api_id,
            "api_hash": self.api_hash,
            "profile_name": self.profile_name,
            "chat_id": self.chat_id or VALUE_NOT_SET,
        }
        self.manager._write_all_settings(self.profile_name, settings_dict)

    def initialize_profile(self) -> Profile:
        """
        Inicializa completamente un perfil.

        Orquesta la creación de un Profile asegurando su trinidad.

        Returns:
            Profile: El perfil inicializado.
        """
        temp_path = self._create_session()
        self._persist_session(temp_path)
        self._write_profile_settings()
        profile = self.manager.get_profile(self.profile_name)
        assert profile is not None, "Perfil inconsistente tras inicialización"
        return profile
