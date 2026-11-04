# Security policy

## Reporting a problem

If you find a security problem (for example a way to make the bot expose someone's token, run unwanted code, or let a
regular member use officer commands), please report it **privately** rather than in a public issue:

- Use **Security → Report a vulnerability** on this repository's GitHub page, if you see that button, or
- open an issue that says only that you have a security report (no details), and the maintainer will arrange a private channel.

Please include the bot version (shown by `/status` or on the first line when the bot starts) and the steps to reproduce.

## Please never post

Your Discord **bot token**, your `.env` file, or `raidbot.db`. If you posted a token by accident, reset it immediately:
Discord Developer Portal → your application → **Bot** → **Reset Token**.

## Supported versions

Only the latest release gets fixes. Update with `Update.bat` (Windows) or by pulling the latest release.
