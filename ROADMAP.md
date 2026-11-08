# Roadmap

Guiding rule: **no new setup for the user.** No new required settings, no extra installs. Optional features get a sensible default
(usually "off") and a one-line `/config` switch.

Shipped versions are listed in [CHANGELOG.md](CHANGELOG.md). Ideas below are open for anyone to pick up.

## Ideas

| Idea | Why | Effort |
|---|---|---|
| Warn when the melee/ranged mix is lopsided | Optional composition hints | S |
| Docker image / systemd unit for always-on hosting | 24/7 without a PC | M |
| More WoW presets (new expansions, Season of Discovery, Cataclysm Classic) | Edit `wow_data.py` | S |
| Translations of bot messages | Non-English guilds | M |

## Parked (needs real setup, so it conflicts with the rule above)
- Web dashboard or calendar feed (needs hosting)
- Soft-reserve / loot tracking (large and game-specific)
- Google Calendar sync (needs the user's Google credentials)

## Release checklist
1. `python tests/run_all.py` passes.
2. Fresh unzip into an empty folder, run `SETUP.bat` with a test bot, confirm the setup files are gone and the bot is online.
3. Copy an old `raidbot.db` in and confirm the migration and that old posts' buttons still work.
4. In a test server: create a raid, sign up past a role limit, leave and confirm bench promotion; edit the time; try `/config channel` with a non-admin account.
5. Scan the release for secrets (`.env`, tokens, IDs, personal paths) before publishing.
