import logging
from pathlib import Path
from typing import Tuple, cast

from totelegram.telegram.client import TelegramSession

logger = logging.getLogger(__name__)


class AuthLogic:
    def __init__(
        self,
        session_name: str,
        api_id: int,
        api_hash: str,
        workdir: Path | str,
    ) -> None:
        self.session_name = session_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.workdir = Path(workdir)

    def run_auth_flow(self) -> Tuple[Path, int]:
        """
        Inicia el flujo interactivo de autenticación de Pyrogram.
        Retorna la ruta del archivo de sesión temporal generado y el ID de la cuenta.
        """
        from pyrogram.types import User

        with TelegramSession(
            session_name=self.session_name,
            api_id=self.api_id,
            api_hash=self.api_hash,
            profiles_dir=self.workdir,
        ) as client:
            me = cast(User, client.get_me())
            account_id = me.id

        temp_session_path = self.workdir / f"{self.session_name}.session"
        return temp_session_path, account_id
