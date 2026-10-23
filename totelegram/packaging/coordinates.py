from dataclasses import dataclass


@dataclass(frozen=True)
class PartCoordinate:
    index: int  # 1-based (1, 2, 3...)
    total: int  # Total de partes

    def __post_init__(self):
        if self.total < 1:
            raise ValueError(
                f"El total de partes debe ser al menos 1 (recibido: {self.total})"
            )
        if not (1 <= self.index <= self.total):
            raise ValueError(
                f"El índice debe estar entre 1 y {self.total} (recibido: {self.index})"
            )

    @property
    def padding(self) -> int:
        """Calcula el ancho de ceros a la izquierda (mínimo 2 dígitos)."""
        return max(2, len(str(self.total)))

    @property
    def cur_str(self) -> str:
        """Índice formateado con ceros (ej: '01', '005')."""
        return str(self.index).zfill(self.padding)

    @property
    def tot_str(self) -> str:
        """Total formateado con ceros (ej: '05', '100')."""
        return str(self.total).zfill(self.padding)

    @property
    def filename_suffix(self) -> str:
        """
        Sufijo físico para el archivo en disco (ej: '.01-05').
        Devuelve cadena vacía si el conjunto es de una sola parte.
        """
        return f".{self.cur_str}-{self.tot_str}" if self.total > 1 else ""

    @property
    def tag(self) -> str:
        """
        Hashtag seguro para Telegram (sin guiones medios): '#part_01_05'.
        """
        return f"#part_{self.cur_str}_{self.tot_str}"

    @property
    def display(self) -> str:
        """Representación visual compacta para el caption: '[1/5]'."""
        return f"[{self.index}/{self.total}]"
