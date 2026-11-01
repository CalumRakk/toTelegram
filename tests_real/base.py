import hashlib
import logging
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import cast

import peewee
from pyrogram.client import Client
from pyrogram.errors import RPCError
from pyrogram.types import Chat, Message, User
from rich.panel import Panel

from totelegram.cli.ui import console
from totelegram.concurrency import LOCK_ROOT_TAG, ConcurrencyCoordinator
from totelegram.database import DatabaseSession
from totelegram.discovery import DiscoveryService
from totelegram.engine.events import UploadObserver
from totelegram.identity import Settings, SettingsManager
from totelegram.models import TelegramUser
from totelegram.telegram.client import TelegramSession
from totelegram.types import UploadSession

logger = logging.getLogger(__name__)

TEST_WORKSPACE = Path.home() / ".config" / "toTelegram_test"
TEST_PROFILE_NAME = "test_runner"


class RealTelegramTestCase(unittest.TestCase):
    """
    TestCase base que encapsula la sesión real de Telegram y la base de datos de prueba.
    """

    manager: SettingsManager
    settings: Settings
    client: Client
    db_session: DatabaseSession
    db: peewee.Database
    tg_chat: Chat
    owner: TelegramUser
    test_chat_id: int

    @classmethod
    def _skip_with_panel(cls, reason: str, details: str, command: str | None = None):
        """Muestra un panel visual explicativo antes de registrar el SkipTest en unittest."""
        lines = [f"[yellow]{reason}[/yellow]\n", f"[dim]{details}[/dim]"]
        if command:
            lines.append("\nEjecuta el siguiente comando para aprovisionar el entorno:")
            lines.append(f"  [bold cyan]> {command}[/bold cyan]")

        panel = Panel(
            "\n".join(lines),
            title="[bold yellow]TESTS REALES OMITIDOS (ENTORNO PENDIENTE)[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
        console.print()
        console.print(panel)
        console.print()
        raise unittest.SkipTest(reason)

    @classmethod
    def setUpClass(cls):
        """Inicializa la sesión real de Telegram y la base de datos aislada."""
        # Comprobar si el workspace existe
        if not TEST_WORKSPACE.exists():
            cls._skip_with_panel(
                reason=f"Entorno de pruebas no configurado en: {TEST_WORKSPACE}",
                details="Estos tests realizan transferencias reales y requieren una sesión y canal dedicados.",
                command="python3 tests_real/setup_test_env.py",
            )

        cls.manager = SettingsManager(TEST_WORKSPACE)
        profile = cls.manager.get_profile(TEST_PROFILE_NAME)

        # Comprobar si el perfil está completo
        if not profile or not profile.is_trinity:
            cls._skip_with_panel(
                reason=f"El perfil '{TEST_PROFILE_NAME}' no existe o está incompleto en {TEST_WORKSPACE}.",
                details="Falta el archivo de sesión .session o la configuración .json.",
                command="python3 tests_real/setup_test_env.py",
            )

        cls.settings = cls.manager.get_settings(TEST_PROFILE_NAME)

        # Comprobar si hay chat destino
        if not cls.settings.chat_id or cls.settings.chat_id == "NOT-SET":
            cls._skip_with_panel(
                reason="No hay un chat_id de pruebas configurado.",
                details="Se necesita un canal o grupo privado dedicado para verificar subidas.",
                command="python3 tests_real/setup_test_env.py",
            )

        # Iniciar cliente Pyrogram
        try:
            cls._tg_session_ctx = TelegramSession(
                session_name=TEST_PROFILE_NAME,
                api_id=cls.settings.api_id,
                api_hash=cls.settings.api_hash,
                profiles_dir=cls.manager.profiles_dir,
            )
            cls.client = cls._tg_session_ctx.__enter__()
        except Exception as e:
            cls._skip_with_panel(
                reason=f"No se pudo conectar a Telegram: {e}",
                details="Verifica tu conexión a Internet o que la sesión no haya sido revocada.",
                command="python3 tests_real/setup_test_env.py",
            )

        # Iniciar sesión de base de datos aislada
        cls.db_session = DatabaseSession(
            cls.manager.database_path, auto_init_schema=True
        )
        cls.db = cls.db_session.__enter__()

        # Obtener información del usuario y del chat de pruebas
        try:
            cls.test_chat_id = int(cls.settings.chat_id)
            cls.tg_chat = cast(Chat, cls.client.get_chat(cls.test_chat_id))
            time.sleep(
                1
            )  # La secuencia de obtener chats y obtener el get_me sin espera genera un FloodWait de más de 10 segundos.
            me = cast(User, cls.client.get_me())
            cls.owner = TelegramUser.get_or_create_from_tg(me)
        except RPCError as e:
            cls.tearDownClass()
            cls._skip_with_panel(
                reason=f"Error validando el chat o cuenta de pruebas en Telegram: {e}",
                details="Asegúrate de que la cuenta tenga acceso al chat configurado.",
                command="python3 tests_real/setup_test_env.py",
            )

    @classmethod
    def tearDownClass(cls):
        """Cierra de forma segura el cliente de Telegram y la base de datos."""
        if hasattr(cls, "db_session"):
            try:
                cls.db_session.__exit__(None, None, None)
            except Exception as e:
                logger.debug(f"Aviso cerrando DB de test: {e}")

        if hasattr(cls, "_tg_session_ctx"):
            try:
                cls._tg_session_ctx.__exit__(None, None, None)
            except Exception as e:
                logger.debug(f"Aviso cerrando cliente Telegram: {e}")

    def setUp(self):
        """Preparación antes de cada método de test."""
        self.created_message_ids: list[int] = []
        self._temp_dirs_to_clean: list[Path] = []

        # Auto-sanación: Limpiar posibles locks residuales en 'me' de tests previos abortados
        self._heal_dangling_account_locks()

    def tearDown(self):
        """
        Limpieza post-test: Garantiza la política de 'Basura Cero'
        eliminando todos los mensajes creados en Telegram y carpetas temporales.
        """
        # Eliminar mensajes subidos a Telegram
        if self.created_message_ids:
            try:
                # Telegram permite borrar hasta 100 mensajes por llamada
                unique_ids = list(set(self.created_message_ids))
                for i in range(0, len(unique_ids), 100):
                    batch = unique_ids[i : i + 100]
                    self.client.delete_messages(
                        chat_id=self.test_chat_id,
                        message_ids=batch,
                    )
                logger.debug(
                    f"Eliminados {len(unique_ids)} mensajes de test en chat {self.test_chat_id}"
                )
            except Exception as e:
                logger.warning(f"Aviso limpiando mensajes en tearDown: {e}")

        # Limpiar directorios temporales en disco
        for temp_dir in self._temp_dirs_to_clean:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # HELPERS DE RECOLECCIÓN Y GESTIÓN

    def track_message(self, message_or_id: Message | int | None):
        """Registra un mensaje para su eliminación automática en tearDown."""
        if message_or_id is None:
            return
        if isinstance(message_or_id, int):
            self.created_message_ids.append(message_or_id)
        elif hasattr(message_or_id, "id"):
            self.created_message_ids.append(message_or_id.id)

    def track_messages(self, messages_or_ids: list[Message | int]):
        """Registra una lista de mensajes para eliminación automática."""
        for m in messages_or_ids:
            self.track_message(m)

    def _heal_dangling_account_locks(self):
        """Elimina locks residuales en 'Mensajes Guardados' (me) para evitar falsos AccountBusyError."""
        try:
            query = f"{LOCK_ROOT_TAG} #acc_{self.owner.id}"
            locks = list(self.client.search_messages("me", query=query, limit=5))
            lock_ids = [m.id for m in locks if m and not getattr(m, "empty", False)]
            if lock_ids:
                self.client.delete_messages("me", lock_ids)
                logger.debug(f"Auto-sanados {len(lock_ids)} locks residuales en 'me'.")
        except Exception as e:
            logger.debug(f"Aviso durante auto-sanación de locks: {e}")

    # FACTORÍAS DE DATOS Y ENTORNO

    def create_temp_dir(self) -> Path:
        """Crea un directorio temporal que se auto-eliminará en tearDown."""
        temp_dir = Path(tempfile.mkdtemp(prefix="totelegram_test_"))
        self._temp_dirs_to_clean.append(temp_dir)
        return temp_dir

    def create_dummy_file(
        self,
        filename: str,
        size_bytes: int,
        parent_dir: Path | None = None,
    ) -> Path:
        """
        Crea un archivo binario con contenido pseudoaleatorio determinista y tamaño exacto.
        """
        target_dir = parent_dir or self.create_temp_dir()
        file_path = target_dir / filename

        # Generar bloques de datos con os.urandom
        chunk_size = 64 * 1024
        written = 0
        with open(file_path, "wb") as f:
            while written < size_bytes:
                to_write = min(chunk_size, size_bytes - written)
                f.write(os.urandom(to_write))
                written += to_write

        return file_path

    @staticmethod
    def calculate_md5(file_path: Path) -> str:
        """Calcula el hash MD5 exacto de un archivo en disco."""
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            while chunk := f.read(64 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()

    def build_upload_session(
        self,
        observer: UploadObserver | None = None,
    ) -> UploadSession:
        """Construye una instancia de UploadSession conectada al arnés de testing."""
        coordinator = ConcurrencyCoordinator(
            db=self.db,
            node_id="test_runner_node",
            client=self.client,
        )
        discovery = DiscoveryService(
            client=self.client,
            db=self.db,
        )

        return UploadSession(
            client=self.client,
            db=self.db,
            tg_chat=self.tg_chat,
            owner=self.owner,
            settings=self.settings,
            coordinator=coordinator,
            discovery=discovery,
        )
