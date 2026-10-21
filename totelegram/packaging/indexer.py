import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List

import peewee
from pyrogram import enums

from totelegram.models import Source, TapeMember

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Message

logger = logging.getLogger(__name__)

# Regex para extraer el hash del caption sin descargar el archivo
SRC_HASH_REGEX = re.compile(r"#src_([a-fA-F0-9]{32,64})")


@dataclass
class UnindexedTapesReport:
    total_remote_tapes: int
    unindexed_count: int
    unindexed_messages: list["Message"] = field(default_factory=list)
    auto_healed: bool = False
    healed_count: int = 0

    @property
    def needs_manual_sync(self) -> bool:
        return self.unindexed_count > 0 and not self.auto_healed


class TapeIndexService:
    THRESHOLD_AUTO_HEAL = 3  # Si hay <= 3 se curan silenciosamente

    def __init__(self, client: "Client", db: peewee.Database):
        self.client = client
        self.db = db

    def check_unindexed_tapes(self, chat_id: int | str) -> UnindexedTapesReport:
        """
        Escanea Telegram buscando mensajes con #type_tape y detecta
        cuántos no tienen sus archivos indexados en la tabla TapeMember.
        """
        try:
            # 1. Búsqueda nativa de solo carpetas/cintas en Telegram
            messages: List["Message"] = list(
                self.client.search_messages(
                    chat_id=int(chat_id),
                    query="#type_tape",
                    filter=enums.MessagesFilter.DOCUMENT,
                    limit=100,
                )  # type: ignore
            )
        except Exception as e:
            logger.warning(f"No se pudo consultar cintas remotas en {chat_id}: {e}")
            return UnindexedTapesReport(total_remote_tapes=0, unindexed_count=0)

        unindexed_messages = []

        # 2. Obtener los MD5 que ya tienen TapeMembers registrados en local
        indexed_sources = set(
            Source.select(Source.md5sum).join(TapeMember).distinct().scalars()
        )

        for msg in messages:
            if not msg.caption:
                continue

            match = SRC_HASH_REGEX.search(msg.caption)
            if not match:
                continue

            md5 = match.group(1)
            if md5 not in indexed_sources:
                unindexed_messages.append(msg)

        return UnindexedTapesReport(
            total_remote_tapes=len(messages),
            unindexed_count=len(unindexed_messages),
            unindexed_messages=unindexed_messages,
        )

    def auto_heal_or_report(self, chat_id: int) -> UnindexedTapesReport:
        """
        Aplica la heurística adaptativa:
        - Si <= 3: Descarga silenciosa, indexa en SQLite y auto-sana.
        - Si > 3: Reporta para que la UI invite a 'totelegram index'.
        """
        report = self.check_unindexed_tapes(chat_id)

        if 0 < report.unindexed_count <= self.THRESHOLD_AUTO_HEAL:
            logger.info(
                f"Detectadas {report.unindexed_count} cintas no indexadas. "
                "Aplicando auto-sanación silenciosa..."
            )
            healed = self._silent_heal(report.unindexed_messages)
            report.auto_healed = True
            report.healed_count = healed

        return report

    def _silent_heal(self, messages: List["Message"]) -> int:
        """Descarga e indexa silenciosamente un grupo pequeño de mensajes .ttape."""
        import tempfile
        from pathlib import Path

        from totelegram.packaging.importer import (
            SnapshotImporter,
            SnapshotImportPlanner,
            SnapshotInspector,
        )

        healed = 0
        with tempfile.TemporaryDirectory() as temp_dir:
            inspections = []
            for msg in messages:
                filename = msg.document.file_name if msg.document else "tape.ttape"
                temp_path = Path(temp_dir) / filename
                if self.client.download_media(msg, file_name=str(temp_path)):
                    inspections.append(SnapshotInspector.inspect_file(temp_path))

            if inspections:
                plan = SnapshotImportPlanner.plan(inspections)
                succeeded, _ = SnapshotImporter.apply_plan(plan, self.db)
                healed = succeeded

        return healed
