from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List

import peewee

if TYPE_CHECKING:
    from pyrogram import Client  # type: ignore
    from pyrogram.types import Chat, Message, User

    from totelegram.discovery import DiscoveryService
    from totelegram.identity import Settings
    from totelegram.models import (
        RemotePayload,
        TelegramUser,
    )
    from totelegram.packaging import Chunker
    from totelegram.schemas import AvailabilityState
    from totelegram.uploader import UploadService


@dataclass
class UploadContext:
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
        from totelegram.schemas import AvailabilityState

        return self.state == AvailabilityState.CAN_FORWARD and len(self.remotes) > 0
