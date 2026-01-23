import unittest
from pathlib import Path
from unittest.mock import patch

from totelegram.commands.upload import _print_skip_report


class TestUploadReport(unittest.TestCase):
    def setUp(self):
        self.p_snap = [Path("video1.mp4"), Path("video2.mp4")]
        self.p_size = [Path("iso_gigante.iso")]
        self.p_excl = [Path("debug.log")]
        self.p_tricky = [Path("[hack] archivo (1).zip")]

    @patch("totelegram.commands.upload.console")
    def test_report_silence_if_empty(self, mock_console):
        """Si no hay archivos omitidos, no debe imprimir nada."""
        _print_skip_report([], [], [], [])
        mock_console.print.assert_not_called()

    @patch("totelegram.commands.upload.console")
    def test_report_detailed_mode_low_density(self, mock_console):
        """Si hay menos de 5 archivos, imprime líneas individuales."""
        # Total 3 archivos
        _print_skip_report(self.p_snap, self.p_size, [], [])

        # Verificamos que NO se imprimió el encabezado de bloque consolidado
        output_text = self._get_all_printed_text(mock_console)
        self.assertNotIn("Contenido omitido:", output_text)

        # Verificamos formato detallado
        self.assertIn("Omitido (Ya tiene Snapshot)", output_text)
        self.assertIn("video1.mp4", output_text)

    @patch("totelegram.commands.upload.console")
    def test_report_summary_mode_structure(self, mock_console):
        """
        Si hay 5 o más archivos, debe usar el nuevo formato de BLOQUES indentados.
        NO debe usar Tablas.
        """
        many_snaps = [Path(f"video_{i}.mp4") for i in range(10)]

        _print_skip_report(many_snaps, [], [], [])

        text = self._get_all_printed_text(mock_console)

        self.assertIn("Contenido omitido:", text)
        self.assertIn("Ya tienen Snapshot:", text)
        self.assertIn("(10)", text)

        self.assertIn("video_0.mp4", text)

        # Verificar truncamiento ("archivos mas...")
        self.assertIn("archivos mas...", text)

    @patch("totelegram.commands.upload.console")
    def test_highlight_disabled_for_filenames(self, mock_console):
        """
        Verifica que highlight=False se pase a la consola para evitar
        colorear números o paréntesis en los nombres de archivo.
        """
        many_tricky = [Path(f"file ({i}).txt") for i in range(6)]

        _print_skip_report([], [], many_tricky, [])

        found_highlight_false = False

        for call in mock_console.print.call_args_list:
            args, kwargs = call
            printed_str = args[0] if args else ""

            if "\t    [dim]" in printed_str:  # Es una línea de archivo
                # Debe tener highlight=False explícitamente
                if kwargs.get("highlight") is False:
                    found_highlight_false = True

        self.assertTrue(
            found_highlight_false,
            "Se debió usar highlight=False para imprimir los nombres de archivo",
        )

    @patch("totelegram.commands.upload.console")
    def test_escape_brackets_logic(self, mock_console):
        r"""
        Verifica que [hack] se escape a \[hack] para que Rich no lo tome como estilo.
        """
        # Usamos modo detallado (<5 archivos)
        _print_skip_report([], [], self.p_tricky, [])

        text = self._get_all_printed_text(mock_console)

        # Rich escape convierte '[' en '\['
        # Python necesita '\\' para representar '\' literal en strings.
        self.assertIn("\\[hack]", text)
        self.assertIn("archivo (1).zip", text)

    def _get_all_printed_text(self, mock_console) -> str:
        """Helper para juntar todo lo que se mandó a imprimir en un solo string."""
        buffer = []
        for call in mock_console.print.call_args_list:
            if call.args and isinstance(call.args[0], str):
                buffer.append(call.args[0])
        return "\n".join(buffer)


if __name__ == "__main__":
    unittest.main()
