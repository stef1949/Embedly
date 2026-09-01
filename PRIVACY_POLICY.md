# Privacy Policy for VXTwitter Link Bot

**Last Updated:** January 7, 2026

## Introduction

This Privacy Policy describes how VXTwitter Link Bot ("the Bot", "we", "us", or "our") collects, uses, and protects information when you use our Discord bot service. By using the Bot, you agree to the collection and use of information in accordance with this policy.

## Information We Collect

### Automatically Collected Information

When you use the Bot, we automatically collect and process the following information:

1. **User Identifiers:**
   - Discord User ID
   - Discord Username
   - Discord Avatar (when user emulation is enabled)

2. **Message Content:**
   - URLs from messages containing Twitter/X or TikTok links
   - Message content is processed in real-time. For TikTok cards, the validated post details and downloaded caption text shown by the information/transcript buttons are stored temporarily with the replacement message's ownership record

3. **Guild (Server) Information:**
   - Guild ID
   - Guild Name
   - Channel IDs where the Bot is active
   - Server member count (for status displays)

4. **Interaction Data:**
   - Button clicks and command usage
   - Timestamps of interactions
   - User preferences for emulation settings

### User-Provided Information

1. **Configuration Settings:**
   - User emulation preferences (whether to post as you or as the Bot)
   - Server-specific settings configured by server administrators

2. **Administrative Actions:**
   - User bans and unbans (recorded for security)
   - Server blacklist changes (recorded for security)
   - Admin additions (recorded for security)

## How We Use Your Information

We use the collected information for the following purposes:

1. **Core Functionality:**
   - Converting Twitter/X links to vxtwitter.com format
   - Downloading and sharing TikTok videos
   - Emulating user identity when posting links (if enabled)

2. **Rate Limiting:**
   - Preventing abuse through per-user and global rate limits
   - Maintaining fair usage across all users and servers

3. **Security and Moderation:**
   - Enforcing user bans and server blacklists
   - Detecting and preventing abuse
   - Security event logging and auditing

4. **Service Improvement:**
   - Tracking basic statistics (e.g., links processed, server count)
   - Performance monitoring and troubleshooting
   - Understanding usage patterns

## Data Storage and Retention

### Temporary Data

- **Message Content:** Processed in real-time. TikTok post details and downloaded caption text used by persistent card buttons are stored in the local SQLite state database for the configured ownership-retention period
- **TikTok Videos:** Downloaded temporarily and deleted immediately after upload
- **Rate Limit Data:** Automatically purged after 1 hour of inactivity

### Persistent Data

The following data is stored for the duration of the Bot's operation:

- User emulation preferences
- User ban status
- Server blacklist status
- Administrator IDs
- Server configuration settings
- Replacement message, channel, guild and original-author IDs, plus TikTok callback text, for the configured ownership-retention period
- Security audit logs (stored in `bot.log` file)

**Data Retention:** Runtime preferences and settings remain in memory and reset when the Bot restarts. Trusted replacement-message ownership coordinates and TikTok callback text are stored in the local SQLite state database and pruned after `OWNERSHIP_RETENTION_DAYS` (30 days by default). Log files are retained for troubleshooting and security purposes.

## Data Sharing and Disclosure

We do not sell, trade, or rent your personal information to third parties.

### Third-Party Services

The Bot interacts with the following services:

1. **Discord API:**
   - All data processed by the Bot is transmitted through Discord's API
   - Subject to Discord's Privacy Policy and Terms of Service

2. **VXTwitter Service:**
   - Twitter/X URLs are converted to use the vxtwitter.com domain
   - No data is directly transmitted to VXTwitter; URL conversion happens locally

3. **yt-dlp (for TikTok downloads):**
   - TikTok URLs are processed to download videos
   - Subject to TikTok's Terms of Service and Privacy Policy

### Legal Requirements

We may disclose your information if required to do so by law or in response to valid requests by public authorities.

## Data Security

We implement appropriate security measures to protect your information:

- **Rate Limiting:** Prevents spam and abuse
- **Access Controls:** Administrative functions are restricted to authorized users
- **Audit Logging:** Security events are logged for review
- **URL Sanitization:** All URLs are sanitized to prevent injection attacks
- **Input Validation:** All user inputs are validated before processing

However, no method of transmission over the internet or electronic storage is 100% secure. We cannot guarantee absolute security.

## Your Rights and Choices

### User Preferences

- You can change your emulation preference at any time using the `/emulate` command or the "Toggle Emulation" button
- You can view the Bot's status and your settings using the `/status` command

### Data Deletion

- Your user preferences are reset when the Bot restarts
- Rate limit data is automatically purged after 1 hour
- To request removal of security logs or ban status, contact the Bot administrator

### Opting Out

- You can stop using the Bot at any time by not interacting with it
- Server administrators can disable the Bot for their entire server
- Server administrators can restrict the Bot to specific channels

## Children's Privacy

The Bot does not knowingly collect personal information from children under the age of 13. The Bot is designed for use in Discord servers, which require users to be at least 13 years old in accordance with Discord's Terms of Service.

## Changes to This Privacy Policy

We may update this Privacy Policy from time to time. Changes will be reflected by updating the "Last Updated" date at the top of this policy. We encourage you to review this Privacy Policy periodically for any changes.

Continued use of the Bot after changes to this Privacy Policy constitutes acceptance of those changes.

## International Data Transfers

The Bot may be hosted in various jurisdictions. By using the Bot, you consent to the transfer of your information to countries outside of your country of residence, which may have different data protection laws.

## Contact Information

If you have questions or concerns about this Privacy Policy or the Bot's data practices, please contact the Bot administrator through the appropriate channels:

- GitHub Issues: [VXtwitter Link Bot Repository](https://github.com/stef1949/VXtwitter-Link-Embedder)
- Discord: Contact the Bot administrator or team members

## Compliance

This Privacy Policy is designed to comply with:

- General Data Protection Regulation (GDPR)
- California Consumer Privacy Act (CCPA)
- Discord Developer Terms of Service
- Discord Developer Policy

## Your Consent

By using the Bot, you consent to this Privacy Policy and agree to its terms.
