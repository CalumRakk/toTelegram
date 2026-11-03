"""
Script para aprovisionar, actualizar el canal o limpiar el entorno de pruebas reales.
Guarda la sesión y ajustes en: ~/.config/toTelegram_test/
"""

import argparse
import json
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
TEST_TARGET_FILE = TEST_WORKSPACE / "test_chat.json"


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
                console.print(f"[dim]Aviso al revocar sesión en Telegram: {e}[/dim]")
            except Exception as e:
                console.print(
                    f"[dim]No se pudo revocar la sesión remota ({e}), procediendo con limpieza local...[/dim]"
                )

    with UI.loading(f"Eliminando workspace local en {TEST_WORKSPACE}..."):
        shutil.rmtree(TEST_WORKSPACE, ignore_errors=True)

    UI.success("Entorno de pruebas eliminado por completo.\n")


def update_test_channel(query: str | None = None):
    """
    Actualiza exclusivamente el canal de pruebas utilizando la sesión autenticada existente,
    sin requerir volver a ingresar teléfono ni código OTP.
    """
    console.print(
        "\n[bold cyan]====================================================[/]"
    )
    console.print("[bold cyan]    Actualización del Canal de Pruebas              [/]")
    console.print(
        "[bold cyan]====================================================[/]\n"
    )

    if not TEST_WORKSPACE.exists():
        UI.error(
            f"No existe un entorno de pruebas en '{TEST_WORKSPACE}'. "
            "Debes realizar la configuración inicial primero."
        )
        sys.exit(1)

    manager = SettingsManager(TEST_WORKSPACE)
    profile = manager.get_profile(TEST_PROFILE_NAME)
    if not profile or not profile.is_trinity:
        UI.error(
            "El perfil de pruebas no está autenticado. "
            "Ejecuta 'python tests_real/setup_test_env.py' para iniciar sesión."
        )
        sys.exit(1)

    settings = manager.get_settings(TEST_PROFILE_NAME)

    with (
        TelegramSession(
            session_name=TEST_PROFILE_NAME,
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            profiles_dir=manager.profiles_dir,
        ) as client,
        DatabaseSession(manager.database_path, auto_init_schema=True) as db,
    ):
        resolver = TargetResolver(client, db)
        access_service = ChatAccessService(client)

        target_query = query
        while True:
            if not target_query:
                target_query = console.input(
                    "Nombre, @username o ID del nuevo canal de pruebas: "
                ).strip()

            if not target_query:
                console.print("[red]Debes especificar un canal válido.[/]")
                continue

            with UI.loading(f"Verificando canal '{target_query}'..."):
                resolution = resolver.resolve(target_query)

            if (
                resolution.status != TargetResolutionStatus.RESOLVED
                or not resolution.chat_id
            ):
                UI.error(resolution.error_message or "No se pudo encontrar el canal.")
                if resolution.hint:
                    console.print(f"[dim]{resolution.hint}[/dim]")
                target_query = None
                continue

            chat_id = resolution.chat_id

            # Validar permisos de administrador y borrado (requerido para limpieza)
            with UI.loading("Validando permisos de administrador..."):
                access_report = access_service.verify_access(chat_id)

            if not access_report.is_ready:
                UI.error(f"Problema de acceso: {access_report.reason}")
                if access_report.hint:
                    console.print(f"[dim]{access_report.hint}[/dim]")
                target_query = None
                continue

            can_delete = access_service.can_delete_messages(chat_id)
            if not can_delete:
                UI.warn(
                    f"La cuenta tiene acceso a '{resolution.title}', pero [bold red]carece del permiso "
                    f"'Eliminar mensajes' (can_delete_messages)[/].\n"
                    "Otorga permisos de administrador a la cuenta en los ajustes del canal y presiona Enter para reintentar."
                )
                console.input("\nPresiona Enter tras otorgar los permisos de admin...")
                target_query = None
                continue

            # Persistir canal en test_chat.json
            TEST_TARGET_FILE.parent.mkdir(parents=True, exist_ok=True)
            TEST_TARGET_FILE.write_text(
                json.dumps(
                    {
                        "chat_id": chat_id,
                        "title": resolution.title,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            UI.separator()
            UI.success(
                f"Canal de pruebas actualizado a: [bold cyan]{resolution.title}[/] [dim]({chat_id})[/]"
            )
            console.print(f"[dim]Guardado en: {TEST_TARGET_FILE}[/dim]\n")
            console.print("Ya puedes ejecutar las pruebas:")
            console.print(
                '  [bold yellow]python3 -m unittest discover -s tests_real -p "test_real_*.py" -v[/]\n'
            )
            break


def setup_test_env():
    """Flujo interactivo de aprovisionamiento de cuenta y canal de pruebas."""
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
        f"Este asistente configurará una sesión aislada en:\n"
        f"[bold yellow]{TEST_WORKSPACE}[/]\n"
    )

    manager = SettingsManager(TEST_WORKSPACE)
    existing_profile = manager.get_profile(TEST_PROFILE_NAME)

    # Si ya existía una sesión válida, ofrecer cambiar solo el canal
    if existing_profile and existing_profile.is_trinity:
        console.print("[green]✔ Se detectó una sesión activa válida para pruebas.[/]")
        console.print("\n¿Qué deseas hacer?")
        console.print(
            "  [1] [bold cyan]Actualizar solo el canal de pruebas[/] (Recomendado)"
        )
        console.print("  [2] Reconfigurar cuenta completa (nuevo login OTP)")
        console.print("  [0] Salir")

        opcion = console.input("\nElige una opción [1]: ").strip()
        if opcion in ("", "1"):
            update_test_channel()
            return
        elif opcion == "0":
            console.print("[yellow]Operación cancelada.[/]")
            return

    # Credenciales de API
    console.print("\n[bold green]1. Credenciales de Telegram[/]")
    console.print(
        "[dim]Presiona Enter para usar las credenciales compartidas por defecto.[/dim]"
    )

    api_id_raw = console.input(f"API ID [{DEFAULT_TG_API_ID}]: ").strip()
    api_id = int(api_id_raw) if api_id_raw else DEFAULT_TG_API_ID

    api_hash_raw = console.input("API HASH [default]: ").strip()
    api_hash = api_hash_raw if api_hash_raw else DEFAULT_TG_API_HASH

    has_custom_app = (api_id != DEFAULT_TG_API_ID) or (api_hash != DEFAULT_TG_API_HASH)

    # Login Pyrogram (OTP)
    console.print("\n[bold green]2. Autenticación con Telegram[/]")
    console.print(
        "[dim]Se solicitará tu número de teléfono y el código que recibirás en Telegram.[/dim]\n"
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

        manager.profiles_dir.mkdir(parents=True, exist_ok=True)
        final_session_path = manager.get_session_path(TEST_PROFILE_NAME)

        if final_session_path.exists():
            final_session_path.unlink()

        temp_session_path.rename(final_session_path)

    UI.success(
        f"Sesión autenticada con éxito como: [bold]{first_name}[/] (ID: {account_id})"
    )

    # Guardar configuración base para testing
    test_settings = Settings(
        profile_name=TEST_PROFILE_NAME,
        telegram_account_id=account_id,
        api_id=api_id,
        api_hash=api_hash,
        has_custom_app=has_custom_app,
        tg_max_size_normal=2 * 1024 * 1024,  # 2 MB simulados para pruebas rápidas
        tg_max_size_premium=2 * 1024 * 1024,
        upload_pause_range=[0, 0],
        upload_limit_rate_kbps=0,
        auto_truncate=True,
    )
    manager.save_settings(TEST_PROFILE_NAME, test_settings)
    manager.set_profile_name_as_active(TEST_PROFILE_NAME)

    # Paso 3: Selección de canal
    update_test_channel()


def main():
    parser = argparse.ArgumentParser(
        description="Aprovisionamiento del entorno de pruebas reales de toTelegram"
    )
    parser.add_argument(
        "-t",
        "--target",
        "--channel",
        dest="target",
        nargs="?",
        const="",
        help="Actualiza únicamente el canal de pruebas sin requerir login ni OTP.",
    )
    parser.add_argument(
        "-c",
        "--clean",
        "--reset",
        action="store_true",
        help="Revoca la sesión remota y elimina el workspace de pruebas.",
    )

    args = parser.parse_args()

    if args.clean:
        clean_test_env()
    elif args.target is not None:
        target_value = args.target.strip() if args.target else None
        update_test_channel(target_value)
    else:
        setup_test_env()


if __name__ == "__main__":
    main()
