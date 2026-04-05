<h1 align="center"> vxtwitter Link Bot </h1> <p align="center"> <img src="https://github.com/stef1949/Embedly/blob/6fb0f80cd5a015ea1fee0abdde3fc95e007ad1b6/IMG_3072.png?raw=true " width="200"> <p/>

This Discord bot looks for Twitter/X links in messages and automatically replaces them with `vxtwitter.com` links. It also supports downloading and sharing TikTok and Instagram videos directly in Discord. It includes user identity emulation, interactive buttons, and comprehensive admin controls.

## Features

### Core Functionality
* **URL Replacement:** Finds URLs containing `twitter.com` or `x.com` and replaces them with `vxtwitter.com`
* **TikTok Video Downloads:** Automatically downloads and shares TikTok videos when TikTok links are posted
* **Instagram Video Downloads:** Automatically downloads and shares Instagram videos/reels when Instagram links are posted
* **User Emulation:** Can post links either as the original user (with their name and avatar) or as the bot with attribution
* **Interactive Buttons:**
   * **Delete Button:** Lets the original message sender remove the bot's response
   * **Toggle Emulation Button:** Allows users to quickly switch their emulation preference
* **Restart-Proof Design:** Buttons continue to work even after the bot restarts

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
* Discord.py 2.0+
* yt-dlp (for TikTok and Instagram video downloads)
* FFmpeg (for video processing)
* A Discord bot token
* *Optional:* NVIDIA GPU with NVENC support for hardware-accelerated video encoding

## Setup
1. **Clone the repository**
2. **Install dependencies:** Run the following command to install required packages from requirements.txt:

```sh
pip install -r requirements.txt
```

3. **Set up your environment variables:** Make sure to set your Discord bot token in your environment variables.

```
export DISCORD_BOT_TOKEN=your_token_here
```

Or create a `.env` file with:

```
DISCORD_BOT_TOKEN=your_token_here
```

4. **Optional: Enable NVIDIA GPU Encoding:** If you have an NVIDIA GPU with NVENC support, you can enable hardware-accelerated encoding:

```
export USE_NVIDIA_GPU=true
```

Or add to your `.env` file:

```
USE_NVIDIA_GPU=true
```

**Note:** NVIDIA GPU encoding requires:
- An NVIDIA GPU with NVENC support (most modern NVIDIA GPUs have it)
- FFmpeg compiled with NVENC support (`--enable-nvenc`)
- NVIDIA drivers installed

To verify FFmpeg has NVENC support, run:
```sh
ffmpeg -encoders | grep nvenc
```

5. **Run the bot:** Launch the bot by running:

```sh
python embedbot.py
```


## Project layout

The codebase is organized into focused modules:

* `embedbot.py` - bot entrypoint and Discord event/command wiring
* `handlers/twitter.py` - Twitter/X rewrite message send flow
* `handlers/media.py` - shared TikTok/Instagram processing pipeline
* `services/downloaders.py` - yt-dlp download abstraction
* `services/transcode.py` - ffmpeg/ffprobe helpers and compression
* `utils/urls.py` - URL parsing/validation/rewriting logic
* `views.py` - persistent Discord UI control views
* `runtime_state.py` - in-memory rate limiting state
* `security.py` - authorization helpers for message controls

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
* `TEMP_DIRECTORY` (`/tmp`)
* `USE_NVIDIA_GPU` (`false`)
* `FFMPEG_HEADROOM_RATIO` (`0.95`)
* `MEDIA_CONCURRENCY` (`3`)

## User Guide

### TikTok Video Downloads
When you share a TikTok link in a channel where the bot is active:
* The bot automatically downloads the video using yt-dlp
* The video is uploaded directly to Discord (if under 8MB file size limit)
* The original message is deleted and replaced with the downloaded video
* The bot attributes the video to you with a mention

**Note:** Videos larger than 8MB cannot be uploaded due to Discord's file size limits.

### Instagram Video Downloads
When you share an Instagram link (posts, reels, IGTV) in a channel where the bot is active:
* The bot automatically downloads the video using yt-dlp
* The video is uploaded directly to Discord (if under 8MB file size limit)
* The original message is deleted and replaced with the downloaded video
* The bot attributes the video to you with a mention

**Supported Instagram URL formats:**
* Posts: `https://www.instagram.com/p/...`
* Reels: `https://www.instagram.com/reel/...` or `https://www.instagram.com/reels/...`
* IGTV: `https://www.instagram.com/tv/...`
* Stories: `https://www.instagram.com/stories/...`

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
The bot can post Twitter/X links in two ways:
* **Emulation Enabled:** Posts appear to come from you (with your name and avatar)
* **Emulation Disabled:** Posts come from the bot with a mention of who shared the link

You can toggle your preference with:
* The `/emulate` command
* The "Toggle Emulation" button on any of your posts

**Note:** Emulation requires the bot to have webhook permissions in the channel. The bot will automatically fall back to non-emulation mode if these permissions are missing.

### Managing Posts
Each converted link includes control buttons:
* **Delete:** Removes the post (only works for your own posts or if you're an admin)
* **Toggle Emulation:** Switches your emulation preference for future posts

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

## Contributing
Feel free to fork this repository and open issues or pull requests with improvements.

## Legal & Privacy

By using this bot, you agree to our:
* [Privacy Policy](PRIVACY_POLICY.md) - How we collect, use, and protect your data
* [Terms of Service](TERMS_OF_SERVICE.md) - Rules and guidelines for using the bot

Please review these documents to understand your rights and responsibilities when using the bot.

## License
This project is provided as-is under the terms in the LICENSE file.
