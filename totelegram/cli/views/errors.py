"""
Presentador visual para excepciones de dominio en la CLI.
Traduce las excepciones a paneles de UI, warnings o mensajes de error estilizados.
"""

from totelegram.cli.ui import UI
from totelegram.exceptions import ToTelegramError


class CLIExceptionHandler:
    """Manejador centralizado que renderiza excepciones de dominio en la terminal."""

    @staticmethod
    def render(err: ToTelegramError) -> None:
        """
        Inspecciona los atributos de la excepción y utiliza el componente visual adecuado.
        """
        border_styles = {
            "info": "cyan",
            "warning": "yellow",
            "error": "red",
        }

        # Si la excepción incluye contexto enriquecido (título, hint o comandos sugeridos)
        # la renderizamos en un panel educativo estructurado
        if err.title or err.suggested_commands or err.hint:
            content = (
                f"{err.message}\n\n{err.hint}"
                if err.hint and err.hint != err.message
                else (err.hint or err.message)
            )

            UI.educational_tip(
                title=err.title,
                message=content,
                commands=err.suggested_commands or None,
                border_style=border_styles.get(err.severity, "red"),
                spacing="block",
            )
        else:
            # Mensajes planos según severidad
            if err.severity == "warning":
                UI.warn(err.message, spacing="top")
            elif err.severity == "info":
                UI.info(err.message, spacing="top")
            else:
                UI.error(err.message, spacing="top")
