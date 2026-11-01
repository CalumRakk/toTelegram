"""
Script interactivo para aprovisionar o limpiar el entorno de pruebas reales.
Crea la sesión de Telegram y los ajustes optimizados en: ~/.config/toTelegram_test/
"""

import shutil
import sys
import tempfile
import uuid
from pathlib import Path

# Asegurar que el paquete totelegram sea importable desde la raíz del repositorio
repo_root = Path(__file__).resolve().parent.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, repo_root)

from pyrogram.errors import RPCError

from totelegram.cli.ui import UI, console
from totelegram.database import DatabaseSession
from totelegram.identity import (
    DEFAULT_TG_API_HASH,
    DEFAULT_TG_API_ID,
    Settings,
    SettingsManager,
)
from totelegram.telegram.access import ChatAccessService
from totelegram.telegram.auth import AuthLogic
from totelegram.telegram.client import TelegramSession
from totelegram.telegram.resolver import TargetResolver
from totelegram.telegram.types import TargetResolutionStatus

TEST_WORKSPACE = Path.home() / ".config" / "toTelegram_test"
TEST_PROFILE_NAME = "test_runner"


def clean_test_env():
    """Cierra la sesión remotamente en los servidores de Telegram y elimina el workspace."""
    console.print(
        "\n[bold cyan]====================================================[/]"
    )
    console.print("[bold cyan]       Limpieza del Entorno de Pruebas              [/]")
    console.print(
        "[bold cyan]====================================================[/]\n"
    )

    if not TEST_WORKSPACE.exists():
        console.print(
            "[yellow]No existe un entorno de pruebas configurado en disco.[/]"
        )
        return

    manager = SettingsManager(TEST_WORKSPACE)
    profile = manager.get_profile(TEST_PROFILE_NAME)

    # Si hay una sesión física, cerramos sesión de forma remota en Telegram
    if profile and profile.has_session:
        with UI.loading("Revocando sesión remota en los servidores de Telegram..."):
            try:
                settings = manager.get_settings(TEST_PROFILE_NAME)
                with TelegramSession(
                    session_name=TEST_PROFILE_NAME,
                    api_id=settings.api_id,
                    api_hash=settings.api_hash,
                    profiles_dir=manager.profiles_dir,
                ) as client:
                    client.log_out()
                UI.success("Sesión remota invalidada exitosamente en Telegram.")
            except RPCError as e:
                logger_msg = f"Aviso al revocar sesión en Telegram: {e}"
                console.print(f"[dim]{logger_msg}[/dim]")
            except Exception as e:
                console.print(
                    f"[dim]No se pudo revocar la sesión remota ({e}), procediendo con limpieza local...[/dim]"
                )

    # Eliminar físicamente el directorio aislado
    with UI.loading(f"Eliminando workspace local en {TEST_WORKSPACE}..."):
        shutil.rmtree(TEST_WORKSPACE, ignore_errors=True)

    UI.success("Entorno de pruebas eliminado por completo.")
    console.print(
        "[dim]Se eliminaron sesiones locales, bases de datos y registros en Telegram.[/dim]\n"
    )


def setup_test_env():
    """Flujo interactivo de aprovisionamiento de cuenta y chat de test."""
    console.print(
        "\n[bold cyan]====================================================[/]"
    )
    console.print(
        "[bold cyan]   Configuración del Entorno de Pruebas de toTelegram [/]"
    )
    console.print(
        "[bold cyan]====================================================[/]\n"
    )

    console.print(
        f"Este asistente configurará una sesión aislada y una base de datos en:\n"
        f"[bold yellow]{TEST_WORKSPACE}[/]\n"
    )
    console.print(
        "[dim]Tu configuración y cuenta diaria en '~/.config/toTelegram' no se verán afectadas.[/dim]\n"
    )

    manager = SettingsManager(TEST_WORKSPACE)
    existing_profile = manager.get_profile(TEST_PROFILE_NAME)

    # Si ya existía una sesión, sugerir revocarla antes
    if existing_profile and existing_profile.has_session:
        UI.warn("Se detectó una sesión previa en el entorno de pruebas.")
        revoke = (
            console.input(
                "¿Deseas revocarla en Telegram antes de configurar la nueva? [S/n]: "
            )
            .strip()
            .lower()
        )
        if revoke in ("", "s", "si", "y", "yes"):
            clean_test_env()
            manager = SettingsManager(TEST_WORKSPACE)

    proceed = console.input("¿Deseas iniciar la configuración? [S/n]: ").strip().lower()
    if proceed not in ("", "s", "si", "y", "yes"):
        console.print("[yellow]Operación cancelada.[/]")
        return

    # Credenciales de la API de Telegram
    console.print("\n[bold green]1. Credenciales de Telegram[/]")
    console.print(
        "[dim]Presiona Enter para usar las credenciales compartidas por defecto.[/dim]"
    )

    api_id_raw = console.input(f"API ID [{DEFAULT_TG_API_ID}]: ").strip()
    api_id = int(api_id_raw) if api_id_raw else DEFAULT_TG_API_ID

    api_hash_raw = console.input("API HASH [default]: ").strip()
    api_hash = api_hash_raw if api_hash_raw else DEFAULT_TG_API_HASH

    has_custom_app = (api_id != DEFAULT_TG_API_ID) or (api_hash != DEFAULT_TG_API_HASH)

    # Flujo de Login interactivo de Pyrogram
    console.print("\n[bold green]2. Autenticación con Telegram[/]")
    console.print(
        "[dim]Se solicitará tu número de teléfono y el código OTP que recibirás en Telegram.[/dim]\n"
    )

    temp_session_name = f"temp_setup_{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory() as temp_dir:
        auth = AuthLogic(
            session_name=temp_session_name,
            api_id=api_id,
            api_hash=api_hash,
            workdir=Path(temp_dir),
        )
        try:
            temp_session_path, account_id, first_name = auth.run_auth_flow()
        except Exception as e:
            UI.error(f"Fallo durante la autenticación: {e}")
            sys.exit(1)

        # Mover la sesión al workspace aislado
        manager.profiles_dir.mkdir(parents=True, exist_ok=True)
        final_session_path = manager.get_session_path(TEST_PROFILE_NAME)

        if final_session_path.exists():
            final_session_path.unlink()

        temp_session_path.rename(final_session_path)

    UI.success(
        f"Sesión autenticada con éxito como: [bold]{first_name}[/] (ID: {account_id})"
    )

    # Resolución y Validación del Chat de Pruebas
    console.print("\n[bold green]3. Chat Dedicado para Pruebas[/]")
    console.print(
        "Indica el canal o grupo privado exclusivo para testing.\n"
        "[bold red]IMPORTANTE:[/] La cuenta debe ser [bold]Administrador[/] con permiso "
        "[bold]'Eliminar mensajes'[/] para que los tests limpien la basura automáticamente.\n"
    )

    resolved_chat_id = None
    resolved_chat_title = None

    with (
        TelegramSession(
            session_name=TEST_PROFILE_NAME,
            api_id=api_id,
            api_hash=api_hash,
            profiles_dir=manager.profiles_dir,
        ) as client,
        DatabaseSession(manager.database_path, auto_init_schema=True) as db,
    ):
        resolver = TargetResolver(client, db)
        access_service = ChatAccessService(client)

        while True:
            chat_query = console.input(
                "Nombre, @username o ID del chat de pruebas: "
            ).strip()
            if not chat_query:
                console.print("[red]Debes especificar un chat de destino válido.[/]")
                continue

            with UI.loading("Verificando destino..."):
                resolution = resolver.resolve(chat_query)

            if (
                resolution.status != TargetResolutionStatus.RESOLVED
                or not resolution.chat_id
            ):
                UI.error(resolution.error_message or "No se pudo encontrar el chat.")
                if resolution.hint:
                    console.print(f"[dim]{resolution.hint}[/dim]")
                continue

            chat_id = resolution.chat_id

            # Verificar permisos de escritura y borrado
            with UI.loading("Validando permisos de administrador..."):
                access_report = access_service.verify_access(chat_id)

            if not access_report.is_ready:
                UI.error(f"Problema de acceso: {access_report.reason}")
                if access_report.hint:
                    console.print(f"[dim]{access_report.hint}[/dim]")
                continue

            # Comprobación estricta de can_delete_messages
            can_delete = access_service.can_delete_messages(chat_id)
            if not can_delete:
                UI.warn(
                    f"La cuenta tiene acceso a '{resolution.title}', pero [bold red]carece del permiso "
                    f"'Eliminar mensajes' (can_delete_messages)[/].\n"
                    "Otorga permisos de administrador a la cuenta en los ajustes del canal/grupo y presiona Enter para reintentar."
                )
                console.input("\nPresiona Enter tras otorgar los permisos de admin...")
                continue

            resolved_chat_id = chat_id
            resolved_chat_title = resolution.title
            UI.success(
                f"Chat validado: [bold cyan]{resolved_chat_title}[/] [dim](ID: {resolved_chat_id})[/]"
            )
            break

    # Guardar configuración optimizada para testing
    console.print("\n[bold green]4. Guardando configuración de testing...[/]")

    test_settings = Settings(
        profile_name=TEST_PROFILE_NAME,
        chat_id=resolved_chat_id,
        telegram_account_id=account_id,
        api_id=api_id,
        api_hash=api_hash,
        has_custom_app=has_custom_app,
        # Parámetros optimizados para pruebas ultra-rápidas
        tg_max_size_normal=2 * 1024 * 1024,  # 2 MB (fuerza chunking en archivos > 2MB)
        tg_max_size_premium=2 * 1024 * 1024,  # 2 MB
        upload_pause_range=[0, 0],  # 0 segundos de pausa
        upload_limit_rate_kbps=0,  # Sin límite de velocidad
        auto_truncate=True,
    )

    manager.save_settings(TEST_PROFILE_NAME, test_settings)
    manager.set_profile_name_as_active(TEST_PROFILE_NAME)

    console.print(
        "\n[bold cyan]====================================================[/]"
    )
    console.print("[bold green]✔ ¡Entorno de pruebas configurado exitosamente![/]")
    console.print("[bold cyan]====================================================[/]")
    console.print(f"• Workspace: [bold]{TEST_WORKSPACE}[/]")
    console.print(f"• Perfil:    [bold]{TEST_PROFILE_NAME}[/]")
    console.print(
        f"• Chat ID:   [bold cyan]{resolved_chat_title} ({resolved_chat_id})[/]"
    )
    console.print("• Corte:     [dim]2 MB (simulado para tests)[/dim]\n")

    console.print("Ya puedes ejecutar los tests de integración ejecutando:")
    console.print(
        '  [bold yellow]python3 -m unittest discover -s tests/manual -p "test_real_*.py" -v[/]\n'
    )


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--clean", "--reset", "-c"):
        clean_test_env()
    else:
        setup_test_env()


if __name__ == "__main__":
    main()
