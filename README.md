<div align="center">
  <img src="IMG_3072.png" alt="Embedly Bot logo" width="200">
  <h1>Embedly Bot</h1>
</div>

This Discord bot replaces supported social links with native Discord Components V2 cards. It downloads and validates Twitter/X, TikTok, Instagram, and YouTube media before publishing attachment-backed Media Galleries. It includes interactive post details, caption-backed transcripts where available, restart-safe authorization, legacy controls, and comprehensive admin commands.

## Features

### Core Functionality
* **Native social cards:** Twitter/X, TikTok, Instagram, and YouTube use Discord Components V2 containers with creator attribution, original-post and Embedly links, compact engagement data when available, information, transcript, and owner-authorized Delete controls
* **Attachment-backed media:** Twitter/X, TikTok, Instagram, and YouTube downloads are uploaded once and referenced by the card's native Media Gallery
* **Fail-closed publication:** Failed downloads, invalid media, timeouts, and rejected uploads never generate fallback links or public errors
* **Safe Original Deletion:** Deletes the original post only after every detected supported link has a successful replacement and its ownership has been saved. Failed or partially completed posts remain available.
* **User Emulation:** Can post media cards either as the original user (with their name and avatar) or as the bot with attribution
* **Interactive Buttons:**
   * **Information / Transcript:** Native cards return trusted post details and available captions ephemerally
   * **Legacy Delete / Toggle Emulation:** Existing fallback views retain their owner-authorized controls
* **Restart-Proof Design:** Trusted message ownership and available card detail/transcript data are stored in SQLite so persistent buttons remain fail-closed and usable after restart

### Security & Administration
* **Rate Limiting:** Per-user and global rate limits to prevent abuse
* **Admin Controls:** Ban users, blacklist servers, and add administrators
* **Server Settings:** Server-specific configuration options
* **Team Support:** Fully compatible with team-owned bots, with all team members recognized as admins
* **Permission Hierarchy:** Server owners, bot admins, and team members all have appropriate permissions
* **Comprehensive Logging:** Detailed logging for troubleshooting and security auditing
* **Webhook Permission Detection:** Automatically detects if emulation is possible in each channel

### Slash Commands
* **User Commands:**
   * `/status` - View detailed bot status information
   * `/help` - Show help information about available commands
   * `/emulate` - Set whether the bot should post as you or as itself
   * `/media_details` - Set whether your media embeds include extra details
* **Admin Commands:**
   * `/ban` - Ban a user from using the bot
   * `/unban` - Unban a previously banned user
   * `/addadmin` - Add a bot administrator
   * `/listadmins` - List all bot administrators
   * `/server_blacklist` - Add or remove a server from the blacklist
* **Server Admin Commands:**
   * `/server_settings` - Configure bot settings for the server
   * `/channel_whitelist` - Add or remove channels to the whitelist

## Prerequisites
* Python 3.10+
* discord.py 2.7.1+ (Components V2 support; the dependency is constrained to `<3`)
* yt-dlp (for TikTok, Instagram, and YouTube media downloads)
* FFmpeg (for video processing)
* A Discord bot token
* *Optional:* NVIDIA GPU with NVENC support for hardware-accelerated video encoding

## Setup

1. **Clone the repository:**

```sh
git clone https://github.com/stef1949/Embedly.git
cd Embedly
```

2. **Create and activate a virtual environment:**

```sh
python -m venv .venv
```

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```sh
source .venv/bin/activate
```

3. **Install dependencies:**

```sh
python -m pip install -r requirements.txt
```

4. **Set the Discord bot token in the current shell:**

PowerShell:

```powershell
$env:DISCORD_BOT_TOKEN = "your-token-here"
```

macOS/Linux:

```sh
export DISCORD_BOT_TOKEN=your_token_here
```

5. **Optional: enable NVIDIA GPU encoding:**

PowerShell:

```powershell
$env:USE_NVIDIA_GPU = "true"
```

macOS/Linux:

```sh
export USE_NVIDIA_GPU=true
```

**Note:** NVIDIA GPU encoding requires:
- An NVIDIA GPU with NVENC support (most modern NVIDIA GPUs have it)
- FFmpeg compiled with NVENC support (`--enable-nvenc`)
- NVIDIA drivers installed

To verify FFmpeg has NVENC support, run `ffmpeg -encoders` and look for `h264_nvenc`.

6. **Run the bot:**

```sh
python embedbot.py
```

## Project layout

The codebase is organized into focused modules:

* `embedbot.py` - bot entrypoint and Discord event/command wiring
* `handlers/twitter.py` - Twitter/X rewrite message send flow
* `handlers/media.py` - shared legacy and native Instagram/YouTube processing pipelines
* `instagram_handler.py`, `tiktok_handler.py`, and `youtube_handler.py` - platform download and embed adapters
* `social_cards.py` - validated Instagram, YouTube, and Twitter/X card metadata
* `services/downloaders.py` - yt-dlp download abstraction
* `services/media_embeds.py` - shared metadata embed formatting
* `services/transcode.py` - ffmpeg/ffprobe helpers and compression
* `utils/urls.py` - URL parsing/validation/rewriting logic
* `views.py` - persistent Discord UI control views
* `runtime_state.py` - in-memory rate limiting state
* `persistence.py` - trusted SQLite message ownership and card callback state
* `security.py` - authorization helpers for message controls

## Tests

After installing the dependencies, run the complete unit test suite with:

```sh
python -m unittest discover -s tests -v
```

## Environment variables

The bot supports these environment variables (with defaults):

* `DISCORD_BOT_TOKEN` (required)
* `RATE_LIMIT_SECONDS` (`10`)
* `GLOBAL_RATE_LIMIT` (`30`)
* `YTDLP_TIMEOUT_SECONDS` (`120`)
* `FFPROBE_TIMEOUT_SECONDS` (`15`)
* `FFMPEG_TIMEOUT_SECONDS` (`120`)
* `UPLOAD_LIMIT_BYTES` (`8388608`)
* `DEFAULT_EMULATION` (`true`)
* `LOG_LEVEL` (`INFO`)
* `TEMP_DIRECTORY` (the operating system's temporary directory)
* `USE_NVIDIA_GPU` (`false`)
* `FFMPEG_HEADROOM_RATIO` (`0.95`)
* `MEDIA_CONCURRENCY` (`3`)
* `STATE_DATABASE_PATH` (`embedly_state.sqlite3`)
* `TWITTER_EMOJI` (defaults to `<:twitter:1544400500290486373>`; invalid or empty values fall back to `𝕏`)
* `INSTAGRAM_EMOJI` (defaults to `<:instagram:1544047809169195039>`; invalid or empty values fall back to `📸`)
* `TIKTOK_EMOJI` (defaults to `<:tiktok:1544047807596597248>`; invalid or empty values fall back to `:tiktok:`)
* `YOUTUBE_EMOJI` (optional full Discord custom emoji; invalid or empty values fall back to `▶️`)
* `OWNERSHIP_RETENTION_DAYS` (`30`; minimum `1`)

## User Guide

### Native Components V2 cards
When you share a supported social link in a channel where the bot is active:

* Cards use `discord.ui.LayoutView`, `Container`, `TextDisplay`, `MediaGallery`, `Separator`, and `ActionRow`
* All four platforms require a validated attachment before a Media Gallery card is published. Image decoding and video probing/decoding require Pillow and FFmpeg/ffprobe. Missing validators preserve the original.
* H.264, HEVC/H.265, VP8, VP9, and AV1 video streams are accepted after decode validation. HEVC media within the upload limit is uploaded unchanged; oversized video still uses the configured compression path. Playback depends on the receiving Discord client.
* Cards include the available creator name and linked handle/profile, original-post and Embedly links, and compact platform statistics such as `♥ 1.2K   💬 6.2K   ▶ 1M`
* The information button shows validated metadata that is available, such as description, post date, duration, and dimensions
* `/media_details enable:true` adds an inline date/duration/dimensions summary to Instagram and YouTube cards when those values are available; the information button remains available either way
* TikTok, Instagram, and YouTube request an existing subtitle/caption track from yt-dlp. Embedly does not use speech-to-text, a paid cloud API, bundled speech models, or automatic model downloads. Twitter/X displays available downloaded metadata. Missing captions produce `Transcript unavailable for this post`
* Bot-authored native cards are sent as replies with `mention_author=False`, so Discord can retain the reply reference and show its original-message-deleted indicator. Emulated Twitter/X cards use a webhook with your name and avatar; webhook posts do not include a reply reference
* When one source message contains several supported links, all replacements are sent and recorded before the source is deleted once. This avoids duplicate deletion attempts and preserves the source if any replacement cannot be secured
* Native Components V2 sends never mix `view=` with legacy `content=` or `embed=`. Existing legacy post controls remain registered, but no new fallback posts are created.
* Ownership recording uses Discord-issued message, channel, guild, and user IDs. If persistence fails, Embedly removes the unrecorded replacement when possible and preserves the source rather than inferring ownership from text, mentions, URLs, or handles

**Download failures:** No replacement, fallback link, or public error is sent. The original message and embeds remain unchanged unless every required replacement was validated, published, and ownership-recorded. Oversized images, unsupported codecs, live streams, and multi-item downloader results are preserved rather than publishing an incomplete media set.

**Private progress:** Ordinary incoming messages are not interactions and cannot receive ephemeral responses. Automatic processing is silent. The submitting user can choose **Apps → Download links** on their message to receive “Your link is being downloaded” ephemerally. This command joins an existing in-flight job when possible and resolves the private response with the outcome. A ten-minute deadline keeps completion within Discord's interaction-token lifetime; failures never become public notifications. Media replacements themselves are published in the original channel.

**Source behavior:** `SOURCE_BEHAVIOR=delete` (default), `suppress`, or `keep` selects what happens after complete publication. `/server_settings source_behavior:…` overrides it for the current server, using the existing in-memory settings lifecycle. Neither deletion nor suppression runs after partial failure. The source is re-fetched before cleanup to avoid deleting newly edited content.

**Retries:** Canonical duplicate links are processed once. SQLite publication records are committed together with ownership, so retries and restarts skip successful links. A send timeout, uncertain server error, or failed ownership rollback leaves a pending record and preserves the original. Such records deliberately block automatic retries: an operator must first inspect the channel and reconcile/remove any unrecorded replacement before clearing its `link_publications` pending record. Do not clear pending records blindly. Publication records are retained separately from expiring button-ownership records.

**Resource limits:** Each media job uses its own temporary directory. Download/probe/transcode work runs in a subprocess; timeout or cancellation kills the process group before directory cleanup and capacity release on POSIX. Windows uses `taskkill /T /F` for timed-out jobs. Downloads have a 256 MiB transfer budget, bounded network retries, and a separate configurable upload limit. Validation and publication have deadlines as well.


### Instagram Media Downloads
When you share an Instagram link (posts, reels, IGTV) in a channel where the bot is active:
* The bot automatically downloads the image or video using yt-dlp
* The media is uploaded directly to Discord and referenced by the native card's Media Gallery
* Available Instagram likes, comments, views, reposts, creator data, and caption metadata are shown compactly or through the information button
* Existing downloadable captions are exposed through the Transcript button
* A failed download/card safely falls back to the validated Instagram link

**Supported Instagram URL formats:**
* Posts: `https://www.instagram.com/p/...`
* Reels: `https://www.instagram.com/reel/...` or `https://www.instagram.com/reels/...`
* IGTV: `https://www.instagram.com/tv/...`
* Stories: `https://www.instagram.com/stories/...`

**Note:** Media larger than 8MB cannot be uploaded due to Discord's file size limits. Videos may be compressed before upload; images are uploaded as downloaded.

### YouTube Video Downloads
When you share a YouTube link in a channel where the bot is active:
* The bot automatically downloads the video using yt-dlp
* The video is uploaded directly to Discord and referenced by the native card's Media Gallery
* Available views, likes, comments, creator data, date, duration, size, and captions are presented by the card
* A failed download/card safely falls back to the validated YouTube link

**Supported YouTube URL formats:**
* Videos: `https://www.youtube.com/watch?v=...`
* Shorts: `https://www.youtube.com/shorts/...`
* Live videos: `https://www.youtube.com/live/...`
* Short links: `https://youtu.be/...`

**Note:** Videos larger than 8MB cannot be uploaded due to Discord's file size limits.

### Hardware-Accelerated Video Encoding
The bot supports NVIDIA GPU hardware acceleration for video encoding using NVENC. This feature can significantly improve video processing performance when enabled.

**Benefits:**
* Faster video processing
* Lower CPU usage
* Better performance when handling multiple video downloads simultaneously

**Requirements:**
* NVIDIA GPU with NVENC support (GeForce GTX 600 series or newer, most modern cards)
* FFmpeg compiled with NVENC support
* NVIDIA drivers installed on the system

**How to Enable:**
Set the `USE_NVIDIA_GPU` environment variable to `true`:
```sh
export USE_NVIDIA_GPU=true
```

**Note:** If hardware encoding fails (e.g., GPU not available or FFmpeg lacks NVENC support), the bot will fall back to CPU-based encoding. Check the bot logs for encoding status messages.

### User Emulation
The preference applies to attachment-backed cards for all four supported platforms:
* **Emulation Enabled:** Posts use your name and avatar through a webhook
* **Emulation Disabled:** Cards appear as bot replies with explicit submitter attribution

Both identities save trusted ownership before source cleanup. If a webhook send fails, the bot preserves the source instead of attempting a second send under another identity.

You can toggle your preference with:
* The `/emulate` command
* The "Toggle Emulation" button on older legacy posts

**Note:** Emulation requires the bot to have webhook permissions in the channel. The bot reuses a usable webhook it owns, including renamed webhooks, and serializes creation per channel. If these permissions are missing or the channel has reached its webhook limit with no reusable bot-owned webhook, validated media is posted with bot attribution. A full channel is checked again after 60 seconds. Other owners’ webhooks are never reused or deleted. Emulation requires an available bot-owned webhook; a failed media send preserves the source.

### Managing Posts
Native cards include information, transcript, and Delete controls. Delete acknowledges the click privately before removing the card, and clears ownership only after confirmed deletion. Emulated cards use their webhook when ordinary message deletion is forbidden. New cards have persistent controls without the previous seven-day timeout. Already-posted cards are not automatically edited to add missing buttons. Legacy fallback views retain these owner-authorized controls:
* **Delete:** Removes the card or legacy post (only works for your own posts or if you're an admin)
* **Toggle Emulation:** Switches your preference for future media cards

### Server Administration
Server administrators can:
* Enable/disable the bot for the entire server
* Restrict the bot to specific channels
* Whitelist or blacklist channels

### Bot Administration
Bot administrators (team members and added admins) can:
* Ban/unban users
* Add new administrators
* Blacklist problematic servers
* View all current administrators with `/listadmins`
* Override controls on any message

### Team Ownership Support
When the bot is owned by a Discord team:
* All team members are automatically recognized as bot administrators
* Team members have full access to all admin commands
* Team ownership status is visible in the `/status` command

### Logging
The bot logs to both the console and a `bot.log` file, including:
* Message conversions
* Button interactions
* Security events
* Error information
* Team and permissions info

## Troubleshooting
If button controls aren't working:
* Check that you're the original poster of the message
* Confirm your Discord client is up-to-date
* Server admins and bot owners can always use the controls
* You can always use direct slash commands as an alternative

For native social cards:

* Confirm `python -m pip show discord.py` reports 2.7.1 or later and rerun `python -m pip install -r requirements.txt` after upgrading
* Give the bot permission to attach files, send messages, read message history, and manage messages if source replacement is desired
* Ensure `STATE_DATABASE_PATH` points to a writable location; ownership persistence failures deliberately preserve the source message
* Emoji environment variables must contain a complete custom emoji mention and the bot must be able to use the emoji; malformed or empty configuration uses the platform's Unicode fallback
* Check the log for yt-dlp, validation, upload-limit, FFmpeg, timeout, or Components V2 errors when a source remains unchanged
* Transcript availability depends on the source and yt-dlp exposing a downloadable caption track. Embedly does not synthesize a transcript from audio
* Twitter/X requires downloadable, valid media; text-only or unavailable posts retain their original message and embed

`STATE_DATABASE_PATH` stores Discord ownership coordinates plus rendered card information/transcript text needed by persistent callbacks. Protect this file as application data. Records older than `OWNERSHIP_RETENTION_DAYS` are removed by the hourly maintenance task. Existing bot messages created before this database was enabled have no trusted ownership row, so ordinary users are denied after a restart; server administrators and verified bot administrators retain their existing override behavior.

## Contributing
Feel free to fork this repository and open issues or pull requests with improvements.

## Legal & Privacy

By using this bot, you agree to our:
* [Privacy Policy](PRIVACY_POLICY.md) - How we collect, use, and protect your data
* [Terms of Service](TERMS_OF_SERVICE.md) - Rules and guidelines for using the bot
* [Security Policy](SECURITY.md) - How to report a vulnerability privately

Please review these documents to understand your rights and responsibilities when using the bot.

### Workflow regression tests

Run `python -W error::ResourceWarning -m unittest discover -s tests` after installing requirements. Publication tests mock Discord and downloader responses but validate actual image bytes. Worker and video tests exercise local subprocesses and FFmpeg. These checks do not establish live Discord rendering, permissions, webhook delivery, or rate-limit behavior.
