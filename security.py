from __future__ import annotations

import re
from typing import Callable, Optional

import discord

MENTION_PATTERNS = [
    r"<@!?(\d+)>",
    r"shared by <@!?(\d+)>",
    r"by <@!?(\d+)>",
    r"by <@!?(\d+)",
    r"<@!?(\d+)",
    r"(\d{17,20})",
]


def extract_author_id(message: discord.Message, fallback_author_id: Optional[int] = None) -> Optional[int]:
    if fallback_author_id:
        return fallback_author_id
    content = message.content or ""
    for pattern in MENTION_PATTERNS:
        match = re.search(pattern, content)
        if match:
            return int(match.group(1))
    return None


def can_manage_bot_message(
    interaction: discord.Interaction,
    original_author_id: Optional[int],
    *,
    is_bot_admin: Callable[[int], bool],
) -> bool:
    user_id = interaction.user.id
    guild = interaction.guild

    is_server_owner = bool(guild and guild.owner_id == user_id)
    is_server_admin = False
    if guild:
        member = guild.get_member(user_id)
        if member:
            is_server_admin = member.guild_permissions.administrator

    return bool(
        (original_author_id and original_author_id == user_id)
        or is_server_owner
        or is_server_admin
        or is_bot_admin(user_id)
    )
