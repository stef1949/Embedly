from __future__ import annotations

from typing import Callable, Optional

import discord

def extract_author_id(message: discord.Message, fallback_author_id: Optional[int] = None) -> Optional[int]:
    """Return only an owner ID supplied by trusted sending code.

    ``message`` is retained for API compatibility. Message content, mentions,
    handles and URLs are deliberately never treated as ownership evidence.
    """

    del message
    if (
        isinstance(fallback_author_id, int)
        and not isinstance(fallback_author_id, bool)
        and fallback_author_id > 0
    ):
        return fallback_author_id
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
