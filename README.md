# SuperDuperRaidBot

**A free, open-source Discord raid-signup bot for World of Warcraft guilds.**
Self-hosted, no subscription, no premium tier, no account to create. Install it in a few minutes, and your
guild's roster, characters and raid history stay on your own computer.

![License: MIT](https://img.shields.io/badge/license-MIT-blue)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![discord.py 2.4+](https://img.shields.io/badge/discord.py-2.4%2B-5865F2)
![Platform: Windows one-click, any OS from source](https://img.shields.io/badge/platform-Windows%20one--click%20%7C%20any%20OS-lightgrey)

Players save their characters, then sign up to a raid post with a button. The post shows the roster grouped by
**Tank / Healer / Melee / Ranged**, warns when you're missing a tank or healer, moves people off the bench when a
slot opens, and pings everyone before the raid starts. Officers get recurring raids, templates, attendance
stats and per-group permissions, so one bot can run several raid teams in one server.

> **Not affiliated with or endorsed by Blizzard Entertainment or Raid-Helper.** World of Warcraft is a trademark of
> Blizzard Entertainment. This is an independent hobby project.

---

## Why this one?

- **Genuinely free, genuinely yours.** MIT licensed. Fork it, change it, run it for your guild, rebrand it. Nothing is paywalled.
- **Brain-easy setup.** On Windows, double-click `SETUP.bat`. It installs what's missing, opens each Discord page for you, checks every step, fills in the settings and starts the bot. No config files to hand-edit.
- **Your data stays with you.** One small SQLite file on your machine. No cloud service, no tracking.
- **Built for real guild structure.** Separate signup channels per raid team, each with its own ping role and its own raid leaders who can only manage their own raids.
- **Friendly when something's wrong.** The bot checks its own setup at startup and tells you in plain English exactly what to fix.

## Features

**For players**
- One-click signup: pick a saved character, or just choose a class and spec on the spot
- Late / Tentative / Bench / Absent buttons, and a bench that **promotes you automatically** when a slot opens (you get a DM)
- Times shown in *your own* timezone with a live countdown
- `/myraids` lists the raids you've answered, with links; `/stats me` shows your attendance

**For raid leaders**
- Raid-Helper-style post with granular role columns, per-role limits, and live warnings like `⚠️ Need 1 more tank (1/2)`
- **Recurring raids** (weekly or every 2 weeks, daylight-saving safe), **templates**, and `/raid copy`
- `/raid edit` to change time or limits (everyone who signed up is told), `/raid roster` for a copy-friendly list
- Optional discussion thread under every post; optional automatic cleanup of old posts
- Reminder before the raid, plus an optional "who hasn't answered yet" ping
- **Attendance tracking:** mark no-shows and see per-person stats

**For admins**
- Several raid teams in one server: per-channel (or per-category) ping role and raid-leader role
- `/config` for timezone, reminders, officers and more, all from Discord
- Activity log channel, `/status` health check (permissions, backups, uptime)
- Automatic daily database backups (keeps the last 7)

## Quick start (Windows, about 3 minutes)

1. Download the latest **SuperDuperRaidBot zip** from the [Releases page](../../releases) and unzip it anywhere.
2. Double-click **`SETUP.bat`** and follow the prompts.
3. That's it. The bot starts, and from then on you launch it with **`start.bat`**. Leave the window open: closing it stops the bot.

What setup does for you: installs Python if needed; opens the Discord Developer Portal so you can create the app and
copy its token (it checks the token immediately); waits while you switch on *Server Members Intent*; opens the invite
link and waits until the bot is in your server; guesses your timezone; writes the settings file; then **deletes itself**.

> The bot only runs while its window is open. For 24/7, use an always-on PC or a small VPS.

### Any OS, from source

```bash
git clone https://github.com/zedhand/SuperDuperRaidBot.git
cd SuperDuperRaidBot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
.venv/bin/python setup.py                      # guided setup: token, server, timezone -> writes .env
.venv/bin/python bot.py
```

Prefer to do it by hand? Copy `.env.example` to `.env`, fill it in (see [Settings](#settings)), and run `bot.py`.
Raspberry Pi, Linux VPS, Docker or a Mac all work, because the bot is plain Python.

### Updating
Close the bot, then double-click **`Update.bat`**. It downloads the latest release, checks its checksum, saves your
current program files into `backups\code-<version>-<time>`, and replaces only the program files. Your settings
(`.env`), data (`raidbot.db`), backups and icons are never touched, and the database upgrades itself on the next start.

`Update.bat` first appears in v1.8.2. If you're on an older version, update once by hand: unzip the new version into a
**new folder**, copy your old `raidbot.db` and `.env` into it, and run `start.bat`. After that, `Update.bat` does it for you.

---

## Using it

**Players:** save your characters once (this makes signing up a single click):
```
/character add   name: Thrall   wowclass: Shaman   spec: Enhancement
```
Then press **Sign Up** on a raid post. `/character list`, `/character main` and `/character remove` manage your list.

**Officers:** post a raid in the signup channel:
```
/raid create   date: 2026-11-14   time: 20:00   title: Naxxramas   tanks: 2   healers: 6   dps: 17   ping: @Raiders
```
Add `repeat: Every week` and the next one posts itself when this one starts. Or save a **template** once
(`/raid template save`) and later run `/raid create date: ... time: ... template: Naxx`.

### Commands

| Who | Command | What it does |
|---|---|---|
| Everyone | `/character add / list / main / remove` | Manage your saved characters |
| Everyone | `/myraids` | Upcoming raids you've answered, with links |
| Everyone | `/stats me` | Your attendance over the last 90 days |
| Everyone | `/help` | Command list inside Discord |
| Officers | `/raid create` | Post a raid (optional ping, repeat, template, thread) |
| Officers | `/raid edit` | Change title, time, description or limits; signed-up people are told |
| Officers | `/raid copy` | Same raid on a new date, without signups |
| Officers | `/raid repeat` | Repeat weekly / every 2 weeks, or stop repeating |
| Officers | `/raid template save / list / delete` | Saved raid setups |
| Officers | `/raid roster` | Private copy-friendly list of who's coming |
| Officers | `/raid noshow` | Mark someone who signed up but didn't turn up |
| Officers | `/raid close / cancel / remove / unsigned` | Close signups, delete a raid, remove a signup, list who hasn't answered |
| Officers | `/stats team` | Everyone's attendance |
| Admins | `/config show` | Show all settings |
| Admins | `/config timezone / reminder / officer / pingrole` | Server-wide settings |
| Admins | `/config channel` | Per-channel or per-category ping role and raid-leader role |
| Admins | `/config threads / archive / nudge / logchannel` | Optional extras (all off until enabled) |
| Admins | `/status` | Health check: version, uptime, permissions, last backup |

**Who counts as an officer:** anyone with *Manage Events* or *Administrator*, or the role set with `/config officer`. A **channel
leader** (a role set with `/config channel`) can manage raids only in their own channel or category.

### Running several raid teams

Say you have categories **Raid 1**, **Raid 2** and **20 Mans**, each with a signup channel. Give the bot access to those
channels (View Channel, Send Messages, Embed Links, Read Message History), then in each signup channel run:

```
/config channel   ping: @Raid1   officer: @Raid1Leaders
```

Raid 1's leaders now post and manage Raid 1's raids, which ping `@Raid1`, and cannot touch Raid 2's. Settings on a channel
beat settings on its category, which beat the server-wide ones. Players share their saved characters across all teams.

**Pinging a role** needs the role to have *Allow anyone to @mention this role* turned on; the bot tells you if it doesn't.

## Settings

`SETUP.bat` / `setup.py` fill in `.env` for you. Everything else is changed live with `/config`.

| Setting | Meaning |
|---|---|
| `DISCORD_TOKEN` | The bot's password. **Never share it or commit it.** `.env` is git-ignored |
| `GUILD_ID` | Your server's ID. Optional: blank + bot in one server = it picks that one |
| `TIMEZONE` | Default timezone (`/config timezone` overrides it per server) |
| `WOW_VERSION` | `retail` or `classic_era` (the class/spec list) |
| `REMINDER_MINUTES` | Default reminder lead time (`/config reminder` overrides it) |

## Data, backups and privacy

Everything lives in `raidbot.db` (SQLite) next to the bot: characters, raids and signups. Once a day the bot copies it to
`backups/` and keeps the newest 7. To restore, stop the bot, copy a backup over `raidbot.db`, start it again. No data leaves
your machine except what Discord itself receives (the posts and messages the bot sends).

## Class icons

Class and spec data is in `wow_data.py` (add a line when a patch adds a spec). Class icons are **not included**, because
the artwork belongs to Blizzard. Without them the bot uses coloured squares. To use icons, drop images named after each
class into the `icons/` folder (`warrior.png`, `deathknight.png`, ...); the bot uploads them as its own emoji on startup.

## Troubleshooting

The bot checks itself at startup and prints the exact problem and fix. Read the last lines of its window.

| Message | Fix |
|---|---|
| `There is no file named exactly '.env'` | The file must be named `.env`, next to `bot.py`. In File Explorer turn on *View → Show → File name extensions* to spot names like `.env.txt` |
| `DISCORD_TOKEN is empty` | Run `SETUP.bat` (or `python setup.py`), or paste the token after `DISCORD_TOKEN=` in `.env` |
| `DISCORD_TOKEN ... looks wrong` | You pasted the Public Key, Client Secret or an ID. Use *Bot → Reset Token → Copy* |
| `Discord rejected this token` | It was reset or copied wrong. *Bot → Reset Token → Copy*, paste into `.env` |
| `Server Members Intent is switched off` | Developer Portal → Bot → Privileged Gateway Intents → turn it on → Save Changes |
| `The bot isn't in any server yet` | Open the link it prints, pick your server, click Authorize |
| `GUILD_ID ... isn't a server this bot is in` | Fix or blank out `GUILD_ID`, or add the bot with the printed link |
| Commands don't show up | Wait a minute, restart Discord (Ctrl+R) |
| `Only raid officers can use /raid` | You need Manage Events, the `/config officer` role, or a channel officer role |
| A post never appears or updates | Give the bot's role View Channel, Send Messages, Embed Links and Read Message History in that channel (check with `/status`) |
| "I couldn't start a thread" | Give the bot *Create Public Threads* in that channel |
| Next recurring raid didn't appear | It posts when the previous one *starts*; the bot must be running then and able to post in that channel |

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
python tests/run_all.py          # every suite, offline, no Discord token needed
```

| File | Role |
|---|---|
| `bot.py` | Commands, buttons, background loops |
| `db.py` | SQLite layer and migrations (older databases upgrade on startup) |
| `embeds.py` | Builds the raid post |
| `wow_data.py` | Class/spec/role data (Retail and Classic Era presets) |
| `checks.py` | Startup and setup checks against Discord (standard library only) |
| `setup.py` / `SETUP.bat` / `start.bat` | The one-click setup and launcher |
| `Update.bat` / `update.ps1` | One-click updater (downloads the latest release, keeps your settings and data) |
| `tests/` | Offline test suites with a faked Discord |

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Ideas and what's planned are in [ROADMAP.md](ROADMAP.md),
and the version history is in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE). Use it, fork it, sell it, rebrand it. No strings attached.
