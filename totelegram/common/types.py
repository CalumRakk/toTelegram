from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, NamedTuple

import peewee

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Chat, Message, User

    from totelegram.common.enums import AvailabilityState
    from totelegram.logic.chunker import Chunker
    from totelegram.logic.discovery import DiscoveryService
    from totelegram.logic.uploader import UploadService
    from totelegram.manager.models import (
        RemotePayload,
        TelegramUser,
    )
    from totelegram.manager.setting import Settings


@dataclass
class UploadContext(NamedTuple):
    tg_chat: "Chat"
    owner: "TelegramUser"
    client: "Client"
    db: peewee.SqliteDatabase
    discovery: "DiscoveryService"
    settings: "Settings"


@dataclass
class AvailabilityReport:
    state: "AvailabilityState"
    remotes: List["RemotePayload"] = field(default_factory=list)

    @property
    def can_forward(self) -> bool:
        from totelegram.common.enums import AvailabilityState

        return self.state == AvailabilityState.CAN_FORWARD and len(self.remotes) > 0
