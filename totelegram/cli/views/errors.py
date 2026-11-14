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
            clean_msg = err.message.strip()
            clean_hint = err.hint.strip() if err.hint else None

            if clean_hint and clean_hint != clean_msg:
                content = f"{clean_msg}\n\n{clean_hint}"
            else:
                content = clean_hint or clean_msg

            UI.educational_tip(
                title=err.title,
                message=content,
                commands=err.suggested_commands or None,
                border_style=border_styles.get(err.severity, "red"),
                spacing="block",
            )
        else:
            clean_msg = err.message.strip()
            if err.severity == "warning":
                UI.warn(clean_msg, spacing="top")
            elif err.severity == "info":
                UI.info(clean_msg, spacing="top")
            else:
                UI.error(clean_msg, spacing="top")
