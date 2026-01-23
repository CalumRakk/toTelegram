import unittest
from pathlib import Path
from unittest.mock import patch

from rich.table import Table

from totelegram.commands.upload import _print_skip_report


class TestUploadReport(unittest.TestCase):
    def setUp(self):
        self.p_snap = [Path("video1.mp4"), Path("video2.mp4")]
        self.p_size = [Path("iso_gigante.iso")]
        self.p_excl = [Path("debug.log")]
        self.p_tricky = [Path("[hack] archivo.zip")]

    @patch("totelegram.commands.upload.console")
    def test_report_silence_if_empty(self, mock_console):
        """Si no hay archivos omitidos, no debe imprimir nada."""
        _print_skip_report([], [], [])
        mock_console.print.assert_not_called()

    @patch("totelegram.commands.upload.console")
    def test_report_detailed_mode_low_density(self, mock_console):
        """Si hay menos de 5 archivos, imprime líneas individuales."""
        # Total: 2 (snapshot) + 1 (size) = 3 archivos. Menos de los 5 requeridos para imprimer una tabla.
        _print_skip_report(self.p_snap, self.p_size, [])

        # Verificamos que NO se imprimió una tabla
        for call_args in mock_console.print.call_args_list:
            arg = call_args[0][0] if call_args[0] else ""
            self.assertNotIsInstance(
                arg, Table, "No debería imprimir una Tabla con pocos archivos"
            )

        # Verificamos que se imprimieron los nombres
        printed_text = ""
        for call in mock_console.print.call_args_list:
            if call.args and isinstance(call.args[0], str):
                printed_text += call.args[0]

        self.assertIn("video1.mp4", printed_text)
        self.assertIn("iso_gigante.iso", printed_text)

    @patch("totelegram.commands.upload.console")
    def test_report_summary_mode_high_density(self, mock_console):
        """Si hay 5 o más archivos, imprime una Tabla."""
        # Generamos listas grandes
        many_snaps = [Path(f"vid{i}.mp4") for i in range(10)]

        _print_skip_report(many_snaps, [], [])

        # Buscamos si alguna llamada a print fue con un objeto Table
        table_printed = False
        for call in mock_console.print.call_args_list:
            if call.args and isinstance(call.args[0], Table):
                table_printed = True

                table = call.args[0]
                # Verificar columna cantidad
                self.assertIn("10", str(table.columns[1]._cells))
                break

        self.assertTrue(table_printed, "Debería haber impreso una Tabla resumen")

    @patch("totelegram.commands.upload.console")
    def test_escape_brackets_logic(self, mock_console):
        r"""
        Verifica que los corchetes se escapan correctamente antes de imprimirse.

        Lógica:
        1. Input: "[hack] archivo.zip"
        2. Procesamiento: escape("[hack]...") -> "\[hack]..."
        3. console.print recibe: "\[hack]..."

        Si el mock recibe la barra invertida, significa que el escape funcionó
        y el usuario verá el texto literal en su pantalla.

        Nota: El docstring se entiende mejor cuando se lee directamente, en vez de usar la previsualización del editor de código.
        """
        # Entra en modo Detallado (impresión línea a línea).
        _print_skip_report([], [], self.p_tricky)

        found_escaped = False

        # Revisamos todas las llamadas a print
        for call in mock_console.print.call_args_list:
            if call.args and isinstance(call.args[0], str):
                txt = call.args[0]

                # Buscamos nuestro archivo problemático
                if "archivo.zip" in txt:
                    # Buscamos "\[hack]" (el string escapado).
                    if "\\[hack]" in txt:
                        found_escaped = True

        self.assertTrue(
            found_escaped,
            r"El nombre del archivo debió enviarse a la consola con los corchetes escapados (\[hack])",
        )


if __name__ == "__main__":
    unittest.main()
