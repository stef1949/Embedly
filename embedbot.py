import asyncio
import logging
import os
import subprocess
import sys
import time

import discord

from config import load_config
from handlers.media import (
    MediaProcessingConfig,
    maybe_delete_original_message,
    process_native_media_links,
)
from handlers.tiktok import process_tiktok_links
from handlers.twitter import send_twitter_rewrite_message
from instagram_handler import download_instagram_media
from persistence import SQLiteStateStore
from runtime_state import RuntimeState
from services.transcode import compress_video_to_limit as compress_video_to_limit_safe
from services.worker import operation_timeout
from social_cards import (
    INSTAGRAM_FALLBACK_ICON,
    TWITTER_FALLBACK_ICON,
    YOUTUBE_FALLBACK_ICON,
    extract_instagram_post,
    extract_youtube_post,
    resolve_platform_icon,
)
from tiktok_handler import download_tiktok_video, resolve_tiktok_icon
from utils.urls import (
    extract_supported_links,
    parse_supported_url,
    contains_unhandled_youtube_link,
    RewriteResult,
)
from views import (
    InstagramCardView,
    InstagramControlView,
    MessageControlView,
    TikTokCardView,
    TikTokControlView,
    TwitterCardView,
    YouTubeCardView,
    YouTubeControlView,
    configure_view_context,
)
from youtube_handler import download_youtube_video

CONFIG = load_config()
TOKEN = CONFIG.discord_token

# Configure logging to show the time, logger name, level, and message.
logging.basicConfig(
    level=getattr(logging, CONFIG.log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Enable the message content intent (required to read messages)
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

# Rate limiting configuration (per user)
RATE_LIMIT_SECONDS = CONFIG.rate_limit_seconds
runtime_state = RuntimeState()
state = SQLiteStateStore(CONFIG.state_database_path)
TIKTOK_ICON = resolve_tiktok_icon(CONFIG.tiktok_emoji)
INSTAGRAM_ICON = resolve_platform_icon(CONFIG.instagram_emoji, INSTAGRAM_FALLBACK_ICON)
TWITTER_ICON = resolve_platform_icon(CONFIG.twitter_emoji, TWITTER_FALLBACK_ICON)
YOUTUBE_ICON = resolve_platform_icon(CONFIG.youtube_emoji, YOUTUBE_FALLBACK_ICON)

# User preferences for emulation (True = emulate user, False = post as bot)
user_emulation_preferences = {}  # Maps user ID to boolean preference
DEFAULT_EMULATION = CONFIG.default_emulation  # Default to emulating users
user_media_details_preferences = {}  # Maps user ID to boolean preference

# Bot statistics
bot_start_time = time.time()
links_processed = 0
version = "2.2.1"  # Bot version

# Security settings
GLOBAL_RATE_LIMIT = CONFIG.global_rate_limit_per_minute  # Maximum requests per minute across all users
BANNED_USERS = set()  # Set of banned user IDs
SERVER_BLACKLIST = set()  # Set of blacklisted server IDs
ADMIN_IDS = set()  # Set of bot admin user IDs

# Server-specific settings
server_settings = {}  # Maps server ID to settings dict

# Timeouts for blocking operations (seconds)
YTDLP_TIMEOUT_SECONDS = CONFIG.ytdlp_timeout_seconds
FFPROBE_TIMEOUT_SECONDS = CONFIG.ffprobe_timeout_seconds
FFMPEG_TIMEOUT_SECONDS = CONFIG.ffmpeg_timeout_seconds
UPLOAD_LIMIT_BYTES = CONFIG.upload_limit_bytes
media_semaphore = asyncio.Semaphore(CONFIG.media_concurrency)
media_config = MediaProcessingConfig(
    temp_directory=CONFIG.temp_directory,
    upload_limit_bytes=UPLOAD_LIMIT_BYTES,
    ytdlp_timeout_seconds=YTDLP_TIMEOUT_SECONDS,
    ffmpeg_timeout_seconds=FFMPEG_TIMEOUT_SECONDS,
    ffprobe_timeout_seconds=FFPROBE_TIMEOUT_SECONDS,
    ffmpeg_headroom_ratio=CONFIG.ffmpeg_headroom_ratio,
    use_nvidia_gpu=CONFIG.use_nvidia_gpu,
)

persistent_views_registered = False

# Utility functions for security
def check_global_rate_limit():
    """Check if the global rate limit has been exceeded"""
    return runtime_state.allow_global_request(GLOBAL_RATE_LIMIT)

def is_user_banned(user_id):
    """Check if a user is banned from using the bot"""
    return user_id in BANNED_USERS

def is_admin(user_id):
    """Check if a user is a bot admin"""
    return user_id in ADMIN_IDS

async def refresh_admin_status():
    """Refresh the admin status from application info"""
    try:
        application = await client.application_info()
        
        # Check if the bot is owned by a team
        if application.team:
            # Add all team members as admins
            for team_member in application.team.members:
                ADMIN_IDS.add(team_member.id)
                logger.info(f"Added team member {team_member.id} ({team_member.name}) as admin")
        else:
            # Add owner as admin for non-team bots
            ADMIN_IDS.add(application.owner.id)
    except Exception as e:
        logger.error(f"Failed to refresh admin status: {e}")

def is_server_blacklisted(server_id):
    """Check if a server is blacklisted"""
    return server_id in SERVER_BLACKLIST

def get_server_setting(server_id, key, default=None):
    """Get a server-specific setting with fallback to default"""
    if server_id not in server_settings:
        server_settings[server_id] = {}
    return server_settings[server_id].get(key, default)

def set_server_setting(server_id, key, value):
    """Set a server-specific setting"""
    if server_id not in server_settings:
        server_settings[server_id] = {}
    server_settings[server_id][key] = value

# Security event logging
def log_security_event(event_type, user_id, guild_id=None, details=None):
    """Log security-related events for auditing"""
    logger.warning(f"SECURITY: {event_type} - User: {user_id}, Guild: {guild_id}, Details: {details}")

# Slash command: /status
@tree.command(name="status", description="View detailed bot status information")
async def status(interaction: discord.Interaction):
    logger.info(f"Received /status command from {interaction.user} in guild {interaction.guild}")
    
    # Defer the response to avoid timeout
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Calculate uptime
        uptime_seconds = int(time.time() - bot_start_time)
        days, remainder = divmod(uptime_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
        
        # Get server count
        server_count = len(client.guilds)
        
        # Check webhook permissions in the current channel
        webhook_perm = "N/A"
        if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
            bot_permissions = interaction.channel.permissions_for(interaction.guild.me)
            webhook_perm = "✅ Yes" if bot_permissions.manage_webhooks else "❌ No"
        
        # Format the status embed
        embed = discord.Embed(
            title="VXTwitter Bot Status",
            description="Transforms Twitter/X links for better embeds",
            color=0x1DA1F2,  # Twitter blue color
            timestamp=discord.utils.utcnow()
        )
        
        # Bot info section
        embed.add_field(name="🤖 Bot Version", value=version, inline=True)
        embed.add_field(name="⏱️ Uptime", value=uptime_str, inline=True)
        embed.add_field(name="⚡ Status", value="Online", inline=True)
        
        # Statistics section
        embed.add_field(name="🔄 Links Processed", value=links_processed, inline=True)
        embed.add_field(name="🏠 Servers", value=server_count, inline=True)
        embed.add_field(name="⏳ Rate Limit", value=f"{RATE_LIMIT_SECONDS} seconds", inline=True)
        
        # Team and permissions section
        is_team_bot = False
        team_name = "N/A"
        try:
            application = await client.application_info()
            is_team_bot = application.team is not None
            if is_team_bot and application.team:
                team_name = application.team.name
        except Exception as e:
            logger.error(f"Failed to get application info: {e}")
        
        embed.add_field(name="👥 Team Bot", value=f"{'Yes' if is_team_bot else 'No'}", inline=True)
        if is_team_bot:
            embed.add_field(name="🏢 Team Name", value=team_name, inline=True)
        embed.add_field(name="🔐 Can Create Webhooks", value=webhook_perm, inline=True)
        
        # Show admin status
        is_admin = interaction.user.id in ADMIN_IDS
        embed.add_field(name="👑 Admin Status", value="✅ Admin" if is_admin else "❌ Not Admin", inline=True)
        
        # If in a guild, add guild-specific info
        if interaction.guild:
            guild_users_count = len(interaction.guild.members)
            # Check if the user is a server admin
            is_server_admin = False
            if interaction.guild.get_member(interaction.user.id):
                member = interaction.guild.get_member(interaction.user.id)
                is_server_admin = member.guild_permissions.administrator
            
            embed.add_field(
                name="📊 Server Info", 
                value=f"Name: {interaction.guild.name}\nMembers: {guild_users_count}\nYou are{' ' if is_server_admin else ' not '}a server admin", 
                inline=False
            )
        
        # Set footer with command help reminder
        embed.set_footer(text="Use /help for available commands")
        
        # Send the embed
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        logger.error(f"Error generating status: {e}")
        await interaction.followup.send("Error generating status information. Please try again later.", ephemeral=True)

# Slash command: /help
@tree.command(name="help", description="Show help information about the bot")
async def help_command(interaction: discord.Interaction):
    logger.info(f"Received /help command from {interaction.user} in guild {interaction.guild}")
    # Defer the response to avoid timeout
    await interaction.response.defer(ephemeral=True)
    
    # Check webhook permissions in this channel
    webhook_permissions = False
    if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
        bot_permissions = interaction.channel.permissions_for(interaction.guild.me)
        webhook_permissions = bot_permissions.manage_webhooks
    
    # Adjust help text based on permissions
    emulation_note = ""
    if not webhook_permissions:
        emulation_note = "\n⚠️ **Note:** User emulation requires webhook permissions, which the bot doesn't have in this channel."
    
    help_text = (
        "This bot replaces Twitter/X, TikTok, Instagram, and YouTube Shorts links with native Discord cards.\n\n"
        "**Commands:**\n"
        "`/status` - Check bot status and statistics.\n"
        "`/help` - Show this help message.\n"
        "`/emulate` - Choose whether media cards use your name and avatar or the bot's identity.\n"
        "`/media_details` - Add available date, duration, and dimensions to Instagram/YouTube cards.\n\n"
        "Use **Apps → Download links** on your own message for private download progress. Automatic processing is silent.\n\n"
        "**Post Controls:**\n"
        "- Native cards include private `Information` and `Transcript` controls\n"
        "- Media cards include owner-authorized `Delete`; legacy posts also retain `Toggle Emulation`\n\n"
        f"Share a supported link in any enabled channel, and the bot will handle the rest!{emulation_note}"
    )
    
    try:
        await interaction.followup.send(help_text, ephemeral=True)
    except Exception as e:
        logger.error(f"Error responding to help command: {e}")

# Slash command: /emulate
@tree.command(name="emulate", description="Choose whether media cards use your identity or the bot's")
async def emulate(interaction: discord.Interaction, enable: bool):
    """Set whether media cards should post as you or as the bot.
    
    Parameters:
    -----------
    enable: bool
        True to have the bot post cards with your name and avatar, False to have it post as itself.
    """
    logger.info(f"Received /emulate command from {interaction.user} with value {enable}")
    
    # Defer the response to avoid timeout
    await interaction.response.defer(ephemeral=True)
    
    # Check if the bot has webhook permissions in this channel (if enable is True)
    can_use_webhooks = False
    if enable and interaction.channel and isinstance(interaction.channel, discord.TextChannel):
        bot_permissions = interaction.channel.permissions_for(interaction.guild.me)
        can_use_webhooks = bot_permissions.manage_webhooks
    
    user_emulation_preferences[interaction.user.id] = enable
    
    if enable:
        if can_use_webhooks:
            message = "Media cards will now use your name and avatar when this channel allows it."
        else:
            message = ("The bot will try to post media cards with your name and avatar. However, it may not work in "
                       "some channels due to missing webhook permissions. In those cases, it will post as the bot with attribution.")
    else:
        message = "Media cards will now post as the bot with your attribution."
    
    try:
        await interaction.followup.send(message, ephemeral=True)
    except Exception as e:
        logger.error(f"Error responding to emulate command: {e}")

# Slash command: /media_details
@tree.command(name="media_details", description="Show extra details on Instagram and YouTube cards")
async def media_details(interaction: discord.Interaction, enable: bool):
    """Set whether Instagram and YouTube cards include an optional detail summary."""
    logger.info(f"Received /media_details command from {interaction.user} with value {enable}")

    await interaction.response.defer(ephemeral=True)

    user_media_details_preferences[interaction.user.id] = enable

    if enable:
        message = "Your future Instagram and YouTube cards will include date, duration, and size when available."
    else:
        message = "Your future Instagram and YouTube cards will keep optional details in the Information button."

    try:
        await interaction.followup.send(message, ephemeral=True)
    except Exception as e:
        logger.error(f"Error responding to media_details command: {e}")

# Admin only commands
@tree.command(name="listadmins", description="List all bot administrators")
@discord.app_commands.checks.cooldown(1, 5.0)  # 1 use per 5 seconds per user
async def list_admins(interaction: discord.Interaction):
    """List all bot administrators"""
    logger.info(f"Received /listadmins command from {interaction.user}")
    
    # Check if the user is an admin or server owner
    is_bot_admin = is_admin(interaction.user.id)
    is_server_owner = interaction.guild and interaction.guild.owner_id == interaction.user.id
    
    if not (is_bot_admin or is_server_owner):
        log_security_event("UNAUTHORIZED_ADMIN_COMMAND", interaction.user.id, 
                          interaction.guild_id if interaction.guild else None,
                          "Attempted to list admins")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    # Get admin details
    try:
        admin_details = []
        for admin_id in ADMIN_IDS:
            try:
                user = await client.fetch_user(admin_id)
                admin_details.append(f"• {user.name} (ID: {admin_id})")
            except discord.HTTPException:
                admin_details.append(f"• Unknown User (ID: {admin_id})")
        
        if admin_details:
            admin_list = "\n".join(admin_details)
            await interaction.response.send_message(f"**Bot Administrators:**\n{admin_list}", ephemeral=True)
        else:
            await interaction.response.send_message("No administrators configured.", ephemeral=True)
    except Exception as e:
        logger.error(f"Error listing admins: {e}")
        await interaction.response.send_message("An error occurred while listing administrators.", ephemeral=True)
@tree.command(name="ban", description="[ADMIN] Ban a user from using the bot")
@discord.app_commands.checks.cooldown(1, 5.0)  # 1 use per 5 seconds per user
async def ban_user(interaction: discord.Interaction, user: discord.User, reason: str = "No reason provided"):
    """Ban a user from using the bot (admin only)"""
    logger.info(f"Received /ban command from {interaction.user} for user {user.id}")
    
    # Only allow admins to use this command
    if not is_admin(interaction.user.id):
        log_security_event("UNAUTHORIZED_ADMIN_COMMAND", interaction.user.id, 
                          interaction.guild_id if interaction.guild else None,
                          f"Attempted to ban user {user.id}")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    # Add the user to the banned list
    BANNED_USERS.add(user.id)
    log_security_event("USER_BANNED", user.id, 
                      interaction.guild_id if interaction.guild else None,
                      f"Banned by {interaction.user.id}: {reason}")
    
    await interaction.response.send_message(f"User {user.mention} has been banned from using the bot.", ephemeral=True)

@tree.command(name="unban", description="[ADMIN] Unban a user from using the bot")
@discord.app_commands.checks.cooldown(1, 5.0)  # 1 use per 5 seconds per user
async def unban_user(interaction: discord.Interaction, user: discord.User):
    """Unban a user from using the bot (admin only)"""
    logger.info(f"Received /unban command from {interaction.user} for user {user.id}")
    
    # Only allow admins to use this command
    if not is_admin(interaction.user.id):
        log_security_event("UNAUTHORIZED_ADMIN_COMMAND", interaction.user.id, 
                          interaction.guild_id if interaction.guild else None,
                          f"Attempted to unban user {user.id}")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    # Remove the user from the banned list if they're in it
    if user.id in BANNED_USERS:
        BANNED_USERS.remove(user.id)
        log_security_event("USER_UNBANNED", user.id, 
                          interaction.guild_id if interaction.guild else None,
                          f"Unbanned by {interaction.user.id}")
        await interaction.response.send_message(f"User {user.mention} has been unbanned from using the bot.", ephemeral=True)
    else:
        await interaction.response.send_message(f"User {user.mention} was not banned.", ephemeral=True)

@tree.command(name="addadmin", description="[ADMIN] Add a bot administrator")
@discord.app_commands.checks.cooldown(1, 5.0)  # 1 use per 5 seconds per user
async def add_admin(interaction: discord.Interaction, user: discord.User):
    """Add a bot administrator (admin only)"""
    logger.info(f"Received /addadmin command from {interaction.user} for user {user.id}")
    
    # This command is restricted to existing admins
    if not is_admin(interaction.user.id):
        log_security_event("UNAUTHORIZED_ADMIN_COMMAND", interaction.user.id, 
                          interaction.guild_id if interaction.guild else None,
                          f"Attempted to add admin {user.id}")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    # Add the user to the admin list
    ADMIN_IDS.add(user.id)
    log_security_event("ADMIN_ADDED", user.id, 
                      interaction.guild_id if interaction.guild else None,
                      f"Added by {interaction.user.id}")
    
    await interaction.response.send_message(f"User {user.mention} has been added as a bot administrator.", ephemeral=True)

@tree.command(name="server_blacklist", description="[ADMIN] Add/remove a server from the blacklist")
@discord.app_commands.checks.cooldown(1, 5.0)  # 1 use per 5 seconds per user
async def server_blacklist(interaction: discord.Interaction, server_id: str, add_to_blacklist: bool):
    """Add or remove a server from the blacklist (admin only)"""
    logger.info(f"Received /server_blacklist command from {interaction.user} for server {server_id}")
    
    # Only allow admins to use this command
    if not is_admin(interaction.user.id):
        log_security_event("UNAUTHORIZED_ADMIN_COMMAND", interaction.user.id, 
                          interaction.guild_id if interaction.guild else None,
                          f"Attempted to modify server blacklist for {server_id}")
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    try:
        # Convert the server ID to an integer
        server_id_int = int(server_id)
        
        if add_to_blacklist:
            SERVER_BLACKLIST.add(server_id_int)
            log_security_event("SERVER_BLACKLISTED", interaction.user.id, server_id_int,
                              f"Server blacklisted by {interaction.user.id}")
            await interaction.response.send_message(f"Server ID {server_id} has been added to the blacklist.", ephemeral=True)
        else:
            if server_id_int in SERVER_BLACKLIST:
                SERVER_BLACKLIST.remove(server_id_int)
                log_security_event("SERVER_UNBLACKLISTED", interaction.user.id, server_id_int,
                                  f"Server removed from blacklist by {interaction.user.id}")
                await interaction.response.send_message(f"Server ID {server_id} has been removed from the blacklist.", ephemeral=True)
            else:
                await interaction.response.send_message(f"Server ID {server_id} was not in the blacklist.", ephemeral=True)
    except ValueError:
        await interaction.response.send_message("Invalid server ID format. Please provide a valid ID.", ephemeral=True)

# Server configuration commands (for server admins)
@tree.command(name="server_settings", description="Configure bot settings for this server (requires Manage Server permission)")
@discord.app_commands.checks.cooldown(1, 5.0)  # 1 use per 5 seconds per user
@discord.app_commands.checks.has_permissions(manage_guild=True)
@discord.app_commands.choices(source_behavior=[
    discord.app_commands.Choice(name="Delete original after all replacements succeed", value="delete"),
    discord.app_commands.Choice(name="Suppress original embeds after all replacements succeed", value="suppress"),
    discord.app_commands.Choice(name="Keep original message and embeds", value="keep"),
])
async def configure_server(
    interaction: discord.Interaction,
    enable_bot: bool | None = None,
    allowed_channels: bool | None = None,
    source_behavior: str | None = None,
):
    """Configure server-specific settings for the bot"""
    logger.info(f"Received /server_settings command from {interaction.user} in guild {interaction.guild}")
    
    # Make sure this is used in a server
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    
    # Initialize server settings if they don't exist
    if interaction.guild.id not in server_settings:
        server_settings[interaction.guild.id] = {
            "enabled": True,
            "restricted_to_channels": False
        }
    
    # Update settings if provided
    settings_updated = False
    if source_behavior is not None:
        if source_behavior not in {"delete", "suppress", "keep"}:
            await interaction.response.send_message("Unknown source behavior.", ephemeral=True)
            return
        set_server_setting(interaction.guild.id, "source_behavior", source_behavior)
        settings_updated = True
    if enable_bot is not None:
        set_server_setting(interaction.guild.id, "enabled", enable_bot)
        settings_updated = True
    
    if allowed_channels is not None:
        set_server_setting(interaction.guild.id, "restricted_to_channels", allowed_channels)
        settings_updated = True
    
    # Send current settings
    current_settings = server_settings[interaction.guild.id]
    embed = discord.Embed(
        title=f"Bot Settings for {interaction.guild.name}",
        color=discord.Color.blue(),
        description="Current configuration for this server"
    )
    
    embed.add_field(name="Bot Enabled", value="✅ Yes" if current_settings.get("enabled", True) else "❌ No", inline=True)
    embed.add_field(name="Channel Restriction", value="✅ Enabled" if current_settings.get("restricted_to_channels", False) else "❌ Disabled", inline=True)
    embed.add_field(name="After successful publication", value=current_settings.get("source_behavior", CONFIG.source_behavior), inline=True)
    
    # Add additional fields for other settings as needed
    
    await interaction.response.send_message(
        content="Settings updated." if settings_updated else "Current server settings:",
        embed=embed,
        ephemeral=True
    )
    
    # Log the configuration change
    if settings_updated:
        log_security_event("SERVER_SETTINGS_CHANGED", interaction.user.id, interaction.guild.id,
                         f"Settings changed by {interaction.user.id}")

@tree.command(name="channel_whitelist", description="Add/remove channels from the whitelist (requires Manage Server permission)")
@discord.app_commands.checks.cooldown(1, 5.0)  # 1 use per 5 seconds per user
@discord.app_commands.checks.has_permissions(manage_guild=True)
async def channel_whitelist(interaction: discord.Interaction, channel: discord.TextChannel, add_to_whitelist: bool):
    """Add or remove a channel from the server's whitelist"""
    logger.info(f"Received /channel_whitelist command from {interaction.user} for channel {channel.id}")
    
    # Make sure this is used in a server
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    
    # Initialize server settings if they don't exist
    if interaction.guild.id not in server_settings:
        server_settings[interaction.guild.id] = {
            "enabled": True,
            "restricted_to_channels": False,
            "whitelisted_channels": set()
        }
    
    # Initialize whitelisted channels if needed
    if "whitelisted_channels" not in server_settings[interaction.guild.id]:
        server_settings[interaction.guild.id]["whitelisted_channels"] = set()
    
    whitelist = server_settings[interaction.guild.id]["whitelisted_channels"]
    
    if add_to_whitelist:
        whitelist.add(channel.id)
        await interaction.response.send_message(f"Channel {channel.mention} has been added to the whitelist.", ephemeral=True)
    else:
        if channel.id in whitelist:
            whitelist.remove(channel.id)
            await interaction.response.send_message(f"Channel {channel.mention} has been removed from the whitelist.", ephemeral=True)
        else:
            await interaction.response.send_message(f"Channel {channel.mention} was not in the whitelist.", ephemeral=True)
    
    # Log the whitelist change
    log_security_event("CHANNEL_WHITELIST_CHANGED", interaction.user.id, interaction.guild.id,
                     f"Channel {channel.id} {'added to' if add_to_whitelist else 'removed from'} whitelist by {interaction.user.id}")

def register_persistent_views():
    global persistent_views_registered
    if persistent_views_registered:
        return
    client.add_view(MessageControlView(timeout=None))
    client.add_view(TikTokControlView(original_url="https://example.com", timeout=None))
    client.add_view(TikTokCardView.persistent_placeholder())
    client.add_view(InstagramControlView(original_url="https://example.com", timeout=None))
    client.add_view(InstagramCardView.persistent_placeholder())
    client.add_view(YouTubeControlView(original_url="https://example.com", timeout=None))
    client.add_view(YouTubeCardView.persistent_placeholder())
    client.add_view(TwitterCardView.persistent_placeholder())
    persistent_views_registered = True
    logger.info("Registered persistent views")

# Error handling for Discord.py
@tree.error
async def on_command_error(interaction: discord.Interaction, error):
    """Handle errors from slash commands"""
    if isinstance(error, discord.app_commands.errors.CommandOnCooldown):
        # Handle cooldown errors
        await interaction.response.send_message(
            f"This command is on cooldown. Please try again in {error.retry_after:.1f} seconds.",
            ephemeral=True
        )
        logger.warning(f"Command cooldown triggered by {interaction.user.id}: {error}")
    elif isinstance(error, discord.app_commands.errors.MissingPermissions):
        # Handle permission errors
        await interaction.response.send_message(
            "You don't have the required permissions to use this command.",
            ephemeral=True
        )
        log_security_event("PERMISSION_ERROR", interaction.user.id, 
                         interaction.guild_id if interaction.guild else None,
                         f"Missing permissions for command: {interaction.command.name}")
    else:
        # Handle other errors
        logger.error(f"Command error: {error}")
        try:
            await interaction.response.send_message(
                "An error occurred while processing this command. Please try again later.",
                ephemeral=True
            )
        except discord.errors.InteractionResponded:
            # If the interaction was already responded to
            pass

# Global exception handler
@client.event
async def on_error(event, *args, **kwargs):
    """Handle global errors"""
    logger.error(f"Discord error in {event}: {sys.exc_info()[1]}")

# Periodic security tasks
async def security_maintenance():
    """Perform periodic security-related maintenance tasks"""
    while True:
        try:
            # Log statistics
            logger.info(f"Bot Stats: {links_processed} links processed, {len(user_emulation_preferences)} user preferences stored")
            logger.info(f"Security: {len(BANNED_USERS)} banned users, {len(SERVER_BLACKLIST)} blacklisted servers")
            
            # Prune old rate limit data
            now = time.time()
            runtime_state.prune_user_entries(older_than_seconds=3600, now=now)
            state.prune_message_ownership(
                int(now) - (CONFIG.ownership_retention_days * 24 * 60 * 60)
            )
            
            # Wait for 1 hour before the next run
            await asyncio.sleep(3600)
        except Exception as e:
            logger.error(f"Error in security maintenance task: {e}")
            await asyncio.sleep(300)  # Wait for 5 minutes before trying again

@client.event
async def on_ready():
    logger.info(f"Logged in as {client.user}!")
    configure_view_context(
        is_admin=is_admin,
        user_emulation_preferences=user_emulation_preferences,
        default_emulation=DEFAULT_EMULATION,
        fetch_user=client.fetch_user,
        state=state,
    )
    register_persistent_views()
    if CONFIG.use_nvidia_gpu:
        cuda_visible = os.getenv("CUDA_VISIBLE_DEVICES")
        nvidia_visible = os.getenv("NVIDIA_VISIBLE_DEVICES")
        logger.info(f"CUDA_VISIBLE_DEVICES={cuda_visible if cuda_visible is not None else 'unset'}")
        logger.info(f"NVIDIA_VISIBLE_DEVICES={nvidia_visible if nvidia_visible is not None else 'unset'}")
        if os.name != "nt":
            nvidia_nodes = [p for p in ("/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-uvm") if os.path.exists(p)]
            logger.info(f"NVIDIA device nodes present: {', '.join(nvidia_nodes) if nvidia_nodes else 'none'}")
        try:
            result = subprocess.run(
                ["nvidia-smi", "-L"],
                capture_output=True,
                text=True,
                check=True,
            )
            logger.info(f"nvidia-smi -L output:\n{result.stdout.strip()}")
            if result.stderr.strip():
                logger.warning(f"nvidia-smi -L stderr:\n{result.stderr.strip()}")
        except FileNotFoundError:
            logger.warning("USE_NVIDIA_GPU is enabled, but nvidia-smi was not found in PATH")
        except subprocess.CalledProcessError as e:
            detail = e.stderr.strip() if e.stderr else str(e)
            logger.warning(f"USE_NVIDIA_GPU is enabled, but nvidia-smi -L failed: {detail}")
        except Exception as e:
            logger.warning(f"USE_NVIDIA_GPU is enabled, but nvidia-smi -L failed: {e}")
    
    # Initialize admins (bot owner or team members)
    try:
        application = await client.application_info()
        
        # Check if the bot is owned by a team
        if application.team:
            logger.info(f"Bot is owned by team: {application.team.name}")
            # Add all team members as admins
            for team_member in application.team.members:
                ADMIN_IDS.add(team_member.id)
                logger.info(f"Team member {team_member.id} ({team_member.name}) added as admin")
        else:
            # Add owner as admin for non-team bots
            ADMIN_IDS.add(application.owner.id)
            logger.info(f"Bot owner {application.owner.id} ({application.owner.name}) added as admin")
    except Exception as e:
        logger.error(f"Failed to initialize admins: {e}")
    
    # Sync the slash commands with Discord.
    try:
        await tree.sync()
        logger.info("Slash commands synced successfully.")
    except Exception as e:
        logger.error(f"Failed to sync slash commands: {e}")
        
    # Set up bot status
    await client.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Twitter/X links"))
    
    # Log startup security information
    logger.info(f"Bot started with {len(ADMIN_IDS)} admin(s), {len(BANNED_USERS)} banned user(s), and {len(SERVER_BLACKLIST)} blacklisted server(s)")
    logger.info(f"Global rate limit set to {GLOBAL_RATE_LIMIT} requests per minute")
    
    # Start background tasks
    client.loop.create_task(security_maintenance())

async def process_message(message) -> str:
    global links_processed
    if message.author == client.user or getattr(message.author, "bot", False) or getattr(message, "webhook_id", None):
        return "ignored"
    if is_user_banned(message.author.id) or (message.guild and is_server_blacklisted(message.guild.id)):
        return "ignored"
    if message.guild:
        if not get_server_setting(message.guild.id, "enabled", True):
            return "ignored"
        if get_server_setting(message.guild.id, "restricted_to_channels", False):
            if message.channel.id not in get_server_setting(message.guild.id, "whitelisted_channels", set()):
                return "ignored"
    links = extract_supported_links(message.content)
    if not links:
        return "no_links"
    if message.id in runtime_state.active_sources:
        return "busy"
    if not check_global_rate_limit():
        return "rate_limited"
    runtime_state.active_sources.add(message.id)
    completion = asyncio.get_running_loop().create_future()
    runtime_state.active_jobs[message.id] = completion
    outcome = "incomplete"
    try:
        completed = 0
        allowed = {}
        for link in links:
            status = state.publication_status(message.id, link.url)
            if status == "published":
                completed += 1
                continue
            if status is not None:
                continue
            if link.platform not in allowed:
                allowed[link.platform] = runtime_state.allow_user_action(message.author.id, link.platform, RATE_LIMIT_SECONDS)
            if not allowed[link.platform]:
                continue

            def record(**values):
                # Ownership and the publication outcome commit in one transaction.
                state.record_message_ownership(**values, source_id=message.id, link_key=link.url)

            common = dict(
                message=message, ownership_recorder=record, config=media_config,
                semaphore=media_semaphore, publication_store=state,
                should_emulate=user_emulation_preferences.get(message.author.id, DEFAULT_EMULATION),
            )
            try:
                if link.platform == "twitter":
                    result = await send_twitter_rewrite_message(
                        rewrite_result=RewriteResult([] if link.spoiler else [link.url], [link.url] if link.spoiler else []),
                        icon=TWITTER_ICON, **common,
                    )
                else:
                    common.update(
                        urls=[link.url], url_validator=lambda url: parse_supported_url(url).url,
                        compressor=compress_video_to_limit_safe, delete_source=False, spoiler=link.spoiler,
                    )
                    if link.platform == "tiktok":
                        result = await process_tiktok_links(downloader=download_tiktok_video, icon=TIKTOK_ICON, **common)
                    else:
                        instagram = link.platform == "instagram"
                        result = await process_native_media_links(
                            source_name="Instagram" if instagram else "YouTube", platform_key=link.platform,
                            icon=INSTAGRAM_ICON if instagram else YOUTUBE_ICON,
                            downloader=download_instagram_media if instagram else download_youtube_video,
                            post_factory=extract_instagram_post if instagram else extract_youtube_post,
                            card_view_factory=InstagramCardView if instagram else YouTubeCardView,
                            include_details=user_media_details_preferences.get(message.author.id, False), **common,
                        )
                # Never trust a handler's count without its durable publication record.
                if result and state.publication_status(message.id, link.url) == "published":
                    completed += 1
                    links_processed += 1
            except Exception as exc:
                logger.warning("Link processing failed (%s); preserving source", type(exc).__name__)
        if completed != len(links):
            return "incomplete"
        if contains_unhandled_youtube_link(message.content):
            outcome = "published_source_retained"
            return outcome
        # Do not remove new text/links that arrived while downloads were running.
        try:
            current = await asyncio.wait_for(message.channel.fetch_message(message.id), 30)
        except (discord.HTTPException, asyncio.TimeoutError):
            outcome = "published_source_retained"
            return outcome
        if current.content != message.content or current.author.id != message.author.id:
            outcome = "published_source_retained"
            return outcome
        behavior = get_server_setting(message.guild.id, "source_behavior", CONFIG.source_behavior) if message.guild else CONFIG.source_behavior
        if behavior == "suppress":
            try:
                await asyncio.wait_for(message.edit(suppress=True), 30)
            except (discord.HTTPException, asyncio.TimeoutError):
                logger.warning("Could not suppress source embed; source retained")
        elif behavior == "delete":
            await maybe_delete_original_message(message, "social media")
        outcome = "complete"
        return outcome
    finally:
        runtime_state.active_sources.discard(message.id)
        runtime_state.active_jobs.pop(message.id, None)
        if not completion.done():
            completion.set_result(outcome)


@client.event
async def on_message(message):
    try:
        async with operation_timeout(600):
            await process_message(message)
    except Exception as exc:
        logger.warning("Message processing failed (%s); source retained", type(exc).__name__)


@tree.context_menu(name="Download links")
async def download_links_privately(interaction: discord.Interaction, message: discord.Message):
    if interaction.user.id != message.author.id:
        await interaction.response.send_message("Only the submitting user can download this message's links.", ephemeral=True)
        return
    await interaction.response.send_message("Your link is being downloaded", ephemeral=True)
    outcome = "incomplete"
    try:
        async with operation_timeout(600):
            running = runtime_state.active_jobs.get(message.id)
            if running is not None:
                outcome = await asyncio.shield(running)
            else:
                outcome = await process_message(message)
    except Exception as exc:
        logger.warning("Private download failed (%s)", type(exc).__name__)
    finally:
        text = {
            "complete": "Your media replacements have been published.",
            "published_source_retained": "Your media replacements were published. Your original message and embeds were preserved.",
            "busy": "Your message is already being processed. Its original will be preserved unless every replacement succeeds.",
            "no_links": "No supported media links were found. Your original message was preserved.",
            "ignored": "Downloads are disabled here or unavailable for this message.",
            "rate_limited": "Please try again later. Your original message was preserved.",
        }.get(outcome, "Not every replacement could be published. Your original message and embeds were preserved.")
        try:
            await interaction.edit_original_response(content=text)
        except discord.HTTPException:
            logger.warning("Could not resolve private progress response")


if __name__ == "__main__":
    client.run(TOKEN)
