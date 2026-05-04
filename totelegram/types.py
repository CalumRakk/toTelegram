from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List

import peewee

from totelegram.concurrency import LeaseManager

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Chat

    from totelegram.discovery import DiscoveryService
    from totelegram.identity import Settings
    from totelegram.models import (
        RemotePayload,
        TelegramUser,
    )
    from totelegram.schemas import AvailabilityState, CLIState


@dataclass
class UploadContext:
    tg_chat: "Chat"
    owner: "TelegramUser"
    client: "Client"
    db: peewee.SqliteDatabase
    discovery: "DiscoveryService"
    settings: "Settings"
    state: "CLIState"
    lease_manager: "LeaseManager"


@dataclass
class AvailabilityReport:
    state: "AvailabilityState"
    remotes: List["RemotePayload"] = field(default_factory=list)

    @property
    def can_forward(self) -> bool:
        from totelegram.schemas import AvailabilityState

        return self.state == AvailabilityState.CAN_FORWARD and len(self.remotes) > 0
