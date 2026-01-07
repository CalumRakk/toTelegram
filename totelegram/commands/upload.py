# @app.command("upload")
# def upload_file(
#     target: Path = typer.Argument(
#         ..., exists=True, help="Archivo o directorio a subir"
#     ),
#     profile: Optional[str] = typer.Option(
#         None, "--profile", "-p", help="Usar un perfil específico temporalmente"
#     ),
#     verbose: bool = typer.Option(
#         False, "--verbose", "-v", help="Mostrar logs detallados"
#     ),
# ):
#     """Sube archivos a Telegram usando la configuración activa."""

#     # 1. Configurar Logging
#     log_level = logging.DEBUG if verbose else logging.INFO
#     # Usamos una ruta temporal o la del sistema para logs inmediatos
#     setup_logging("totelegram_cli.log", log_level)
#     logger = logging.getLogger(__name__)

#     try:
#         # 2. Obtener el path del .env
#         if profile:
#             # Si el usuario fuerza un perfil con -p
#             env_path = pm.get_profile_path(profile)
#             console.print(f"[blue]Usando perfil forzado: {profile}[/blue]")
#         else:
#             # Usar el activo por defecto
#             try:
#                 env_path = pm.get_profile_path()
#             except ValueError:
#                 console.print("[bold red]No hay perfil activo.[/bold red]")
#                 console.print("Ejecuta 'totelegram profile create' primero.")
#                 raise typer.Exit(code=1)

#         # 3. Cargar settings
#         settings = get_settings(env_path)
#         console.print(
#             f"Iniciando subida usando configuración de: [bold]{settings.chat_id}[/bold]"
#         )

#         # 4. Ejecutar orquestador
#         # Convertimos el generador a lista para consumir el proceso
#         snapshots = list(orchestrator_upload(target, settings))

#         if snapshots:
#             console.print(
#                 f"[bold green]✔ Proceso finalizado. {len(snapshots)} archivos procesados.[/bold green]"
#             )
#         else:
#             console.print(
#                 "[yellow]No se generaron snapshots (¿archivos excluidos o vacíos?).[/yellow]"
#             )

#     except Exception as e:
#         console.print(f"[bold red]Fallo crítico:[/bold red] {e}")
#         if verbose:
#             logger.exception("Traceback completo:")
#         raise typer.Exit(code=1)
