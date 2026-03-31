import logging
import discord
import re
import os
import time
import sys
import asyncio
import subprocess
from typing import Callable
from tiktok_handler import download_tiktok_video
from instagram_handler import download_instagram_video
from config import load_config
from utils.urls import rewrite_twitter_urls, validate_tiktok_url as validate_tiktok_url_safe, validate_instagram_url as validate_instagram_url_safe
from services.transcode import compress_video_to_limit as compress_video_to_limit_safe
from views import MessageControlView, TikTokControlView, InstagramControlView, configure_view_context
from handlers.media import MediaProcessingConfig, process_media_links as process_media_links_shared
from handlers.twitter import send_twitter_rewrite_message
from runtime_state import RuntimeState

# Configure logging to show the time, logger name, level, and message.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

CONFIG = load_config()
TOKEN = CONFIG.discord_token

# Enable the message content intent (required to read messages)
intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

# Regex to match TikTok URLs
TIKTOK_URL_REGEX = re.compile(r'(https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com)/\S+)', re.IGNORECASE)

# Regex to match Instagram URLs (posts, reels, stories, and short URLs)
INSTAGRAM_URL_REGEX = re.compile(r'(https?://(?:www\.)?(?:instagram\.com|instagr\.am)/(?:p|reels?|tv|stories)/\S+)', re.IGNORECASE)

# Rate limiting configuration (per user)
RATE_LIMIT_SECONDS = CONFIG.rate_limit_seconds
runtime_state = RuntimeState()

# User preferences for emulation (True = emulate user, False = post as bot)
user_emulation_preferences = {}  # Maps user ID to boolean preference
DEFAULT_EMULATION = CONFIG.default_emulation  # Default to emulating users

# Bot statistics
bot_start_time = time.time()
links_processed = 0
version = "1.2.2"  # Bot version

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
    # First check if the user is in the admin set
    if user_id in ADMIN_IDS:
        return True
    
    # Return a default value - will be updated on the next is_admin check
    return False

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

async def check_team_membership(user_id):
    """Check if a user is a member of the bot's team"""
    try:
        application = await client.application_info()
        
        # Check if the bot is owned by a team
        if application.team:
            for team_member in application.team.members:
                if team_member.id == user_id:
                    logger.info(f"User {user_id} is a member of team {application.team.name}")
                    return True
        
        # If not a team bot or user not in team
        return False
    except Exception as e:
        logger.error(f"Failed to check team membership: {e}")
        return False

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

def sanitize_url(url):
    """Sanitize a URL to prevent potential injection attacks"""
    # For Twitter/X URLs, use basic sanitization
    # Note: # (fragment identifier) is excluded for security
    return re.sub(r'[^\w\.\/\:\-\?\&\=\%]', '', url)

def validate_tiktok_url(url):
    """
    Validate and sanitize a TikTok URL.
    Returns the validated/sanitized URL. Logs a warning if URL doesn't match expected patterns.
    """
    # TikTok URL patterns we expect (checked with re.IGNORECASE)
    case_insensitive_patterns = [
        r'^https?://(?:www\.)?tiktok\.com/@[\w\.]+/video/\d+',
        r'^https?://(?:www\.)?tiktok\.com/t/[\w]+',
        r'^https?://vm\.tiktok\.com/[\w]+',
    ]
    
    # Short URL pattern (case-sensitive path check to avoid matching common lowercase paths)
    # TikTok short URLs are 8-12 characters total and start with uppercase letter or digit (e.g., ZNRrFcTFL)
    # Pattern breakdown: [A-Z0-9] (1 char) + [A-Za-z0-9]{7,11} (7-11 chars) = 8-12 chars total
    # This excludes common paths like "trending", "foryou", "following" which are all lowercase
    # If a capitalized common path is matched (e.g., "Trending"), yt-dlp will handle it gracefully
    short_url_pattern = r'^https?://(?:www\.)?tiktok\.com/[A-Z0-9][A-Za-z0-9]{7,11}/?$'
    
    # Check if URL matches any valid pattern
    matched = False
    for pattern in case_insensitive_patterns:
        if re.match(pattern, url, re.IGNORECASE):
            matched = True
            break
    
    # Check short URL pattern without IGNORECASE for the path part
    if not matched and re.match(short_url_pattern, url):
        matched = True
    
    if not matched:
        logger.warning(f"TikTok URL doesn't match expected patterns: {url}")
    
    # Basic sanitization - remove any trailing fragments or suspicious characters
    # Keep only the base URL components, including @ symbol for TikTok usernames
    return re.sub(r'[^\w\.\/\:\-\?\&\=\%\@]', '', url)

def validate_instagram_url(url):
    """
    Validate and sanitize an Instagram URL.
    Returns the validated/sanitized URL. Logs a warning if URL doesn't match expected patterns.
    """
    # Instagram URL patterns we expect
    patterns = [
        r'^https?://(?:www\.)?instagram\.com/p/[\w\-]+',  # Posts
        r'^https?://(?:www\.)?instagram\.com/reels?/[\w\-]+',  # Reels (reel or reels)
        r'^https?://(?:www\.)?instagram\.com/tv/[\w\-]+',  # IGTV
        r'^https?://(?:www\.)?instagram\.com/stories/[\w\.]+/\d+',  # Stories
        r'^https?://(?:www\.)?instagr\.am/p/[\w\-]+',  # Short URL posts
        r'^https?://(?:www\.)?instagr\.am/reels?/[\w\-]+',  # Short URL reels
    ]
    
    # Check if URL matches any valid pattern
    matched = False
    for pattern in patterns:
        if re.match(pattern, url, re.IGNORECASE):
            matched = True
            break
    
    if not matched:
        logger.warning(f"Instagram URL doesn't match expected patterns: {url}")
    
    # Basic sanitization - remove any trailing fragments or suspicious characters
    # Keep only the base URL components
    return re.sub(r'[^\w\.\/\:\-\?\&\=\%]', '', url)

def cleanup_file(filepath):
    """Clean up a temporary file with proper error handling"""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"Cleaned up temporary file: {filepath}")
    except OSError as e:
        logger.warning(f"Failed to clean up file {filepath}: {e}")

async def run_blocking(func, *args, timeout_seconds=None, **kwargs):
    if timeout_seconds:
        return await asyncio.wait_for(asyncio.to_thread(func, *args, **kwargs), timeout=timeout_seconds)
    return await asyncio.to_thread(func, *args, **kwargs)

def get_video_duration_seconds(filepath):
    """Return video duration in seconds using ffprobe, or None on failure"""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                filepath,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
        duration_str = result.stdout.strip()
        if not duration_str:
            return None
        duration = float(duration_str)
        if duration <= 0:
            return None
        return duration
    except subprocess.TimeoutExpired as e:
        logger.warning(f"ffprobe timed out for {filepath}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to get video duration for {filepath}: {e}")
        return None

def compress_video_to_limit(filepath, max_size_bytes):
    """
    Compress a video using ffmpeg to fit within max_size_bytes.
    Returns the compressed filepath, or None on failure.
    """
    duration = get_video_duration_seconds(filepath)
    if duration is None:
        return None

    # Reserve some headroom for container overhead and Discord metadata
    target_total_bits = int(max_size_bytes * 8 * 0.95)
    # Use a conservative audio bitrate and allocate the rest to video
    audio_bitrate = 96_000
    total_bitrate = max(int(target_total_bits / duration), audio_bitrate + 50_000)
    video_bitrate = max(total_bitrate - audio_bitrate, 300_000)

    output_dir = os.path.dirname(filepath) or "."
    base_name, _ = os.path.splitext(os.path.basename(filepath))
    compressed_path = os.path.join(output_dir, f"{base_name}_compressed.mp4")

    use_nvidia_gpu = os.getenv('USE_NVIDIA_GPU', 'false').lower() in ('true', '1', 'yes')
    if use_nvidia_gpu and os.name != "nt":
        if not (os.path.exists("/dev/nvidia0") or os.path.exists("/dev/nvidiactl")):
            logger.warning("NVIDIA device nodes not found; skipping NVENC and using libx264")
            use_nvidia_gpu = False

    def run_ffmpeg(video_codec, preset, extra_args=None):
        if extra_args is None:
            extra_args = []
        ffmpeg_args = [
            "ffmpeg",
            "-y",
            "-i", filepath,
            "-c:v", video_codec,
            *extra_args,
            "-b:v", str(video_bitrate),
            "-maxrate", str(video_bitrate),
            "-bufsize", str(video_bitrate * 2),
            "-preset", preset,
            "-c:a", "aac",
            "-b:a", str(audio_bitrate),
            compressed_path,
        ]
        return subprocess.run(
            ffmpeg_args,
            capture_output=True,
            text=True,
            check=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )

    try:
        if use_nvidia_gpu:
            try:
                run_ffmpeg("h264_nvenc", "p4", ["-gpu", "0"])
            except Exception as e:
                logger.warning(f"NVENC compression failed, falling back to libx264: {e}")
                run_ffmpeg("libx264", "veryfast")
        else:
            run_ffmpeg("libx264", "veryfast")
    except subprocess.TimeoutExpired as e:
        logger.error(f"FFmpeg compression timed out for {filepath}: {e}")
        return None
    except Exception as e:
        logger.error(f"FFmpeg compression failed for {filepath}: {e}")
        return None

    if not os.path.exists(compressed_path):
        logger.error(f"Compressed file not created: {compressed_path}")
        return None

    return compressed_path

async def delete_message_silently(message):
    """Delete a Discord message silently without raising errors"""
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        logger.debug(f"Could not delete message {message.id}: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error deleting message {message.id}: {e}")


async def maybe_delete_original_message(message: discord.Message, context: str) -> None:
    """Delete a source message with consistent error handling."""
    try:
        await message.delete()
        logger.info(f"Deleted original {context} message {message.id} from {message.author}")
    except discord.Forbidden:
        logger.warning(f"Missing permissions to delete {context} message {message.id} from {message.author}")
    except discord.HTTPException as e:
        logger.error(f"Failed to delete {context} message {message.id}: {e}")


async def process_media_links(
    *,
    message: discord.Message,
    urls: list[str],
    source_name: str,
    icon: str,
    url_validator: Callable[[str], str],
    downloader: Callable,
    view_factory: Callable[[str], discord.ui.View],
) -> int:
    """Shared media processing flow for TikTok/Instagram links."""
    processed = 0
    for source_url in urls:
        validated_url = url_validator(source_url)
        processing_msg = await message.channel.send(f"⏳ Downloading {source_name} video from <@{message.author.id}>...")
        result = None
        filepath = None
        original_filepath = None
        try:
            async with media_semaphore:
                result = await run_blocking(
                    downloader,
                    validated_url,
                    output_folder=CONFIG.temp_directory,
                    timeout_seconds=YTDLP_TIMEOUT_SECONDS,
                )
            if not result or not result.get("success"):
                logger.error("%s download failed: %s", source_name, result.get("error", "Unknown error") if result else "Unknown error")
                continue

            original_filepath = result["filepath"]
            filepath = original_filepath
            file_size = os.path.getsize(filepath)
            if file_size > UPLOAD_LIMIT_BYTES:
                compressed_path = await run_blocking(
                    compress_video_to_limit_safe,
                    filepath,
                    UPLOAD_LIMIT_BYTES,
                    ffprobe_timeout_seconds=FFPROBE_TIMEOUT_SECONDS,
                    ffmpeg_timeout_seconds=FFMPEG_TIMEOUT_SECONDS,
                    headroom_ratio=CONFIG.ffmpeg_headroom_ratio,
                    use_nvidia_gpu=CONFIG.use_nvidia_gpu,
                    timeout_seconds=FFMPEG_TIMEOUT_SECONDS,
                )
                if not compressed_path:
                    logger.warning("%s compression failed for %s", source_name, filepath)
                    continue
                filepath = compressed_path
                if os.path.getsize(filepath) > UPLOAD_LIMIT_BYTES:
                    logger.warning("Compressed %s video still exceeds upload limit: %s", source_name, filepath)
                    continue

            media_view = view_factory(validated_url)
            media_view.original_author_id = message.author.id
            with open(filepath, "rb") as media_file:
                file = discord.File(media_file, filename=os.path.basename(filepath))
                await delete_message_silently(processing_msg)
                sent_message = await message.channel.send(
                    content=f"{icon} **{source_name} video shared by <@{message.author.id}>:**\n{result.get('title', 'Unknown Title')}",
                    file=file,
                    view=media_view,
                )
                media_view.message = sent_message
            processed += 1
            await maybe_delete_original_message(message, source_name)
        except asyncio.TimeoutError:
            logger.error("%s operation timed out for URL: %s", source_name, validated_url)
        except (discord.HTTPException, discord.Forbidden, OSError, IOError) as e:
            logger.error("Error processing %s video %s: %s", source_name, validated_url, e)
        finally:
            await delete_message_silently(processing_msg)
            if filepath:
                cleanup_file(filepath)
            if original_filepath and original_filepath != filepath:
                cleanup_file(original_filepath)
    return processed

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
        "This bot replaces `twitter.com` or `x.com` links with `vxtwitter.com` for better embeds.\n\n"
        "**Commands:**\n"
        "`/status` - Check bot status and statistics.\n"
        "`/help` - Show this help message.\n"
        "`/emulate` - Choose whether the bot posts links as you or as itself.\n\n"
        "**Post Controls:**\n"
        "When you share a Twitter/X link, it will be automatically converted, and you'll see:\n"
        "- A `Delete` button - Remove your posted link\n"
        "- A `Toggle Emulation` button - Quickly switch between posting styles\n\n"
        f"Just share a Twitter/X link in any channel, and the bot will handle the rest!{emulation_note}"
    )
    
    try:
        await interaction.followup.send(help_text, ephemeral=True)
    except Exception as e:
        logger.error(f"Error responding to help command: {e}")

# Slash command: /emulate
@tree.command(name="emulate", description="Choose whether the bot should emulate your identity when posting links")
async def emulate(interaction: discord.Interaction, enable: bool):
    """Set whether the bot should post as you or as itself.
    
    Parameters:
    -----------
    enable: bool
        True to have the bot post links with your name and avatar, False to have it post as itself.
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
            message = "The bot will now post Twitter/X links with your name and avatar."
        else:
            message = ("The bot will try to post Twitter/X links with your name and avatar. However, it may not work in "
                       "some channels due to missing webhook permissions. In those cases, it will mention you instead.")
    else:
        message = "The bot will now post Twitter/X links as itself and mention you."
    
    try:
        await interaction.followup.send(message, ephemeral=True)
    except Exception as e:
        logger.error(f"Error responding to emulate command: {e}")

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
            except:
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
async def configure_server(interaction: discord.Interaction, enable_bot: bool = None, allowed_channels: bool = None):
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
    client.add_view(InstagramControlView(original_url="https://example.com", timeout=None))
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
    )
    register_persistent_views()
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
    except Exception as e:
        logger.warning(f"nvidia-smi -L failed: {e}")
    
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

@client.event
async def on_message(message):
    global links_processed  # Declare global at the start of the function
    # Avoid processing the bot's own messages.
    
    if message.author == client.user:
        return
        
    # Check if the user is banned
    if is_user_banned(message.author.id):
        logger.info(f"Ignoring message from banned user {message.author.id}")
        return
        
    # Check if in a blacklisted server
    if message.guild and is_server_blacklisted(message.guild.id):
        logger.info(f"Ignoring message from blacklisted server {message.guild.id}")
        return
        
    # Check server-specific settings
    if message.guild:
        # Check if the bot is enabled for this server
        if not get_server_setting(message.guild.id, "enabled", True):
            logger.info(f"Bot is disabled in server {message.guild.id}")
            return
            
        # Check if the channel is whitelisted (if channel restriction is enabled)
        if get_server_setting(message.guild.id, "restricted_to_channels", False):
            whitelisted_channels = get_server_setting(message.guild.id, "whitelisted_channels", set())
            if message.channel.id not in whitelisted_channels:
                logger.info(f"Ignoring message in non-whitelisted channel {message.channel.id}")
                return
    
    # Check global rate limit
    if not check_global_rate_limit():
        logger.warning("Global rate limit exceeded, ignoring message")
        return

    rewrite_result = rewrite_twitter_urls(message.content)
    if rewrite_result.rewritten_urls or rewrite_result.spoiler_urls:
        if not runtime_state.allow_user_action(message.author.id, "twitter", RATE_LIMIT_SECONDS):
            logger.info(f"User {message.author} is rate limited for Twitter/X processing.")
            return

        await maybe_delete_original_message(message, "twitter")

        should_emulate = user_emulation_preferences.get(message.author.id, DEFAULT_EMULATION)
        try:
            links_processed += await send_twitter_rewrite_message(
                message=message,
                rewrite_result=rewrite_result,
                should_emulate=should_emulate,
            )
        except Exception as e:
            logger.error(f"Error sending rewritten Twitter/X message for {message.id}: {e}")

    # Process TikTok links
    tiktok_matches = list(TIKTOK_URL_REGEX.finditer(message.content))
    if tiktok_matches:
        if not runtime_state.allow_user_action(message.author.id, "tiktok", RATE_LIMIT_SECONDS):
            logger.info(f"User {message.author} is rate limited for TikTok link.")
            return
        tiktok_urls = [match.group(0) for match in tiktok_matches]
        logger.info(f"Processing TikTok links from {message.author} (ID: {message.id}) with URLs: {tiktok_urls}")
        links_processed += await process_media_links_shared(
            message=message,
            urls=tiktok_urls,
            source_name="TikTok",
            icon="🎵",
            url_validator=validate_tiktok_url_safe,
            downloader=download_tiktok_video,
            view_factory=lambda url: TikTokControlView(original_url=url, timeout=604800),
            compressor=compress_video_to_limit_safe,
            semaphore=media_semaphore,
            config=media_config,
        )

    # Process Instagram links
    instagram_matches = list(INSTAGRAM_URL_REGEX.finditer(message.content))
    if instagram_matches:
        if not runtime_state.allow_user_action(message.author.id, "instagram", RATE_LIMIT_SECONDS):
            logger.info(f"User {message.author} is rate limited for Instagram link.")
            return
        instagram_urls = [match.group(0) for match in instagram_matches]
        logger.info(f"Processing Instagram links from {message.author} (ID: {message.id}) with URLs: {instagram_urls}")
        links_processed += await process_media_links_shared(
            message=message,
            urls=instagram_urls,
            source_name="Instagram",
            icon="📸",
            url_validator=validate_instagram_url_safe,
            downloader=download_instagram_video,
            view_factory=lambda url: InstagramControlView(original_url=url, timeout=604800),
            compressor=compress_video_to_limit_safe,
            semaphore=media_semaphore,
            config=media_config,
        )

# Run the bot
client.run(TOKEN)
