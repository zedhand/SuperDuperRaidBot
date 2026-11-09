# Changelog

## 1.13.3
Fixes from a full audit of 1.13.2. Nothing new to set up; every change keeps today's settings working.
- **Reminders on closed raids.** A raid closed for sign-ups (by `/config autoclose` or `/raid close`) still gets its reminder. Before, turning on autoclose quietly stopped every reminder.
- **Private channels stay private.**
  - `/always join` only lists and accepts repeating raids you can see.
  - The board, the low-signup alert and Discord events never show a raid in a private thread, and they now check members as well as roles.
  - A channel's raid leaders see only their own raids in `/stats team` and `/stats export`.
  - `/raid add` won't sign up someone who can't see the raid's channel.
  - Reminders aren't sent by DM to people who left or lost access to the channel.
- **Safer roles.** `/config officer` and `/config channel officer:` refuse @everyone. `/raid create ping:` needs **Mention Everyone** yourself to ping @everyone or a role that isn't mentionable.
- **More reliable.**
  - A repeating raid that can't be posted (an outage, a missing permission) keeps trying for about a day and a half, then says so in the log channel, instead of stopping for good after 5 minutes.
  - Moving a raid sends the DM reminders again for the new time.
  - Only one copy of the bot can run on the same database.
  - Settings that redraw every raid post answer straight away.
  - The automatic no-show check is skipped, not run hours late, if the bot was offline when it was due.
  - The bench, Discord events and closed posts no longer trip over two updates arriving at once.
- **Raid posts.** A very full roster now ends with "…and N more" instead of cutting names off, and the healers always keep their place next to the tanks.
- **Docker and Raspberry Pi.**
  - `docker stop` shuts the bot down cleanly.
  - `docker compose ps` shows whether it's healthy.
  - The image is built from pinned versions, so every build is the one that was tested.
  - `./docker-update.sh` keeps the previous version and prints how to go back to it.
- **Updater (`Update.bat`).**
  - It refuses while the bot is still running.
  - It puts the old files back if copying fails.
  - It never installs a release whose version number it can't read.
- **Smaller fixes.**
  - `/help` lists every setting.
  - The invite link also asks for Discord's newer **Pin Messages** and **Create Events** permissions.
  - `/status` warns when class icons can't show.
  - `/forgetme` overwrites the deleted data in the database file.
  - Backups no longer fail when the bot's folder has a `%` in its name.
  - Cataclysm Classic counts Warlocks as a battle res.
  - The bot warns at startup if anyone can add it to their server ("Public Bot").
  - Docs fixed to match the bot.

## 1.13.2
A small Docker follow-up: the Raspberry Pi now downloads a ready-made image instead of building the bot on every update.
- **Ready-made Docker image.** Every release now comes with an image for the Raspberry Pi (arm64) and PCs (amd64) at `ghcr.io/zedhand/superduperraidbot`, so `./docker-update.sh` just downloads it instead of building the bot on the Pi. Building it yourself still works: `./docker-update.sh --build` (see [DOCKER.md](DOCKER.md)), and the script falls back to that by itself if the download fails.

## 1.13.1
A small follow-up to 1.13.0: run the bot 24/7 on a Raspberry Pi, and an invite link that covers the 1.13 extras.
- **Run it 24/7 on a Raspberry Pi with Docker.** New `Dockerfile`, `docker-compose.yml` and a step-by-step guide, [DOCKER.md](DOCKER.md): from a blank Pi to a bot that starts again by itself after a crash, reboot or power cut, including moving your `.env` and `raidbot.db` over from Windows, backups and restoring. `./docker-update.sh` updates it to the newest release. The token stays in `.env` on the Pi (never in the image), and the database, backups and `raidbot.log` live in the `data` folder. The Windows `.bat` files work exactly as before.
- New optional setting `LOG_DIR` puts `raidbot.log` in another folder (Docker uses it to keep the log next to the database).
- The invite link from `SETUP.bat` now also asks for **Manage Events**, **Manage Messages** and **Attach Files**, so `/alerts events`, the pinned `/config board` and `/config backupchannel` work on a new install without changing the bot's role by hand. A bot added earlier keeps its old permissions; give it these yourself if you use those extras.

## 1.13.0
"Safety net" and "convenience" in one release. Everything new is optional and off by default; nothing new is required.
- **More WoW versions.** Besides Retail and Classic Era (also Hardcore) there are now presets for Season of Discovery, Burning Crusade, Wrath, Cataclysm, Mists of Pandaria and WoW: Forever. Pick one in `SETUP.bat` or with `WOW_VERSION` in `.env`.
- **Confirm and undo (`/config confirm`, `/raid restore`).** With the switch on, `/raid cancel` and `/raid remove` ask "are you sure?" first. A cancelled raid can be brought back with all its signups for 14 days with `/raid restore`, whether or not the switch is on.
- **`/forgetme`.** A player deletes their own characters, signups, `/always` sign-ups and `/remindme` setting in every server the bot is in. It asks first and takes their name off the raid posts; raids they created stay.
- **Daily backup post (`/config backupchannel`).** Every day after 04:00 (server time) the bot posts a copy of this server's data in a private admin channel, so a backup lives off the bot's PC. It refuses a public channel and keeps the newest 7 posts. `/config backup` now also holds only this server's data.
- **Realms.** `/character add realm:` and `/character realm` save a realm with a character (Raider.IO import fills it in). `/config realms` shows it on raid posts, e.g. `Thrall (Area 52)`.
- **Signup notes (`/config notes`).** Players add a short note with `/note`, e.g. `late 15 min`. Officers see it in `/raid roster`, and with `onpost: True` it shows on the post too. Mentions, links and formatting are removed.
- **🔄 Spec button (`/config specswitch`).** Switch to another spec of the same character from the raid post without signing up again. Role limits and the bench are checked again when the role changes.
- **`/raid promote`.** Move a chosen benched player in, ahead of the line. It refuses when their role or class is full unless you add `over_limit: True`.
- **In-game invites (`/raid roster invites: True`).** One `/invite Name-Realm` line per player who is in or late, grouped so each block fits in a macro.
- **Upcoming raids board (`/config board`).** One pinned message listing the next 15 raids with their time, signups per role and a link, kept up to date by itself. Raids in channels the board's readers can't see are left off.
- **Low-signup alert (`/alerts lowsignup`).** A set number of hours before a raid, officers get one line in a channel you pick if the raid is short, optionally with a ping (`ping_officers: True`).
- **Discord Scheduled Events (`/alerts events`).** Each raid gets an entry in the server's Events list that follows the raid (edited, cancelled, restored, started and ended with it). Only raids in channels everyone can see get one, and it needs the **Manage Events** permission.
- `/alerts` is a new command group for alert settings, because `/config` is at Discord's limit of 25 commands.
- Releases are now built, tested and scanned by GitHub Actions, and Dependabot keeps the dependencies up to date.
- Tests: new `tests/test_v113.py` and `tests/test_release.py`.

## 1.12.0
"Polish". Everything here is optional and off by default; nothing new is required.
- **`/config setup`: a guided panel.** One private panel with pages for roles, channels, timing and the optional extras. Every control saves as soon as you choose it, shows the current value, and uses the same settings as the `/config` commands. Only the admin who opened it can use it.
- **New-version notice (`/config updatenotice`).** About once a day the bot asks GitHub for the newest release and posts one line in the log channel, once per version, with a link and the reminder to run `Update.bat`. A failed check is retried in an hour; `/status` shows the latest version. Only a normal request to GitHub is made: nothing about your server.
- **`/character import` (`/config raiderio`).** Retail only. A player types a character name (and realm) and the bot looks it up on Raider.IO to save the class and spec, and shows the item level. It sends only the name and realm the player typed, and only when an admin has switched it on (with an optional default region and realm). If Raider.IO is down or has no such character, the player is told and can use `/character add`.
- **Setup extras for Windows.** At the end of `SETUP.bat` you are asked, with the default being no, whether to put a desktop shortcut on your desktop and whether to start the bot when Windows starts. `Shortcuts.bat` switches either on or off any time.
- Tests: new `tests/test_v112.py`.

## 1.11.0
"Accountability".
- **Automatic no-shows from a voice channel (optional).** `/config voicechannel` (or `/config channel voice:` per raid team) names the channel where your raiders gather. Fifteen minutes after a raid starts (change it with `minutes`), anyone signed up as **in** who isn't in that channel is marked as a no-show, and the result goes to the log channel. Safeguards: if nobody is in the channel the check is skipped; people marked **late**, benched or tentative are never checked; only raids that started in the last 6 hours are looked at, so turning it on never changes old attendance; and `/raid noshow` (undo: True) reverses any mark.
- **Class limits.** `/raid create classes: Mage 4, Warlock 2` limits how many of a class can be in; the rest go on the bench and move in when a spot opens, with their place in line ("#1 in line for a Mage slot"). The post's footer shows how many of each limited class are in. `/raid edit classes:` changes or removes them (`off`), and templates, `/raid copy` and repeating raids keep them.
- **Missing-utility warnings (optional, `/config buffs`).** Once 8 or more people are in and none of them can provide Bloodlust or a battle res, the post says so. The class lists are in `wow_data.py` (Classic Era has no Bloodlust).
- **`/stats export`:** attendance as a spreadsheet (CSV) for officers: Discord id, name, signups, attended, late, no-shows, declined and the show-up percentage. Names that start with `=`, `+`, `-` or `@` are neutralised so a spreadsheet can't run them as formulas.
- **Weekly digest (optional, `/config digest`):** every Monday after 09:00 (server time), a summary of the last 7 days in the log channel.
- Tests: new `tests/test_v111.py`.

## 1.10.0
"Faster for players".
- **One-click sign-up.** Press **Sign Up** and the top button signs you up as the character you used last time (your main the first time). The prompt also tells you your current status. Choosing another character or class still works the same way.
- **📅 Calendar button** on every raid post: sends a private `.ics` file for Google, Apple or Outlook calendar. The entry has the raid's title, description and a link back to the post, and opening the file again after the raid moves updates the same entry.
- **`/remindme`:** get raid reminders as a private message instead of a ping in the channel. If your private messages from the server are closed, you are pinged in the channel as before. Off by default.
- **`/always join | leave | list`:** sign up once to a repeating raid and be signed up to every new occurrence as it is posted (with the character you chose; role limits and the bench still apply, and you can still press Absent on any date). It also works for repeating raids that were already running.
- **Your place in line on the bench.** When a role is full and you are benched, the bot tells you your place ("#2 in line for a tank slot"), and the bench list on the post shows it, e.g. `(#2 tank)`.
- Fixed wording: when DPS slots were full the messages said "DP slots"; they now say "DPS slots".
- Tests: new `tests/test_v110.py`.

## 1.9.0
"Faster for officers".
- **Type dates the way you'd say them.** `/raid create`, `/raid copy` and `/raid edit` now understand `thu 8pm`, `tomorrow 19:30`, `nov 14 8pm`, `+7d 20:00` and `next sun noon`, as well as the old `2026-11-14` with `20:00`. The date box also offers the next two weeks to pick from, and the time box is optional when the date includes a time. In `/raid edit` you can change just the day or just the time. Unclear input is refused with an example instead of guessed (a bare `8` could be morning or evening).
- **`/raid list`:** your upcoming raids at a glance, with sign-up counts per role, times and links.
- **`/raid add`:** sign someone up, or change their status, character or spec, for them. It uses their saved main (or the character they already signed up with), or you can pick one of their characters or give a class and spec. It works after sign-ups are closed, still respects role limits (extra people go on the bench), and is written to the activity log.
- **`/config raiderrole`:** only members with this role can sign up (officers are always allowed). Off by default.
- **`/config autoclose`:** close sign-ups a set number of hours before the start. Off by default. A raid created inside that window is left open, and an officer can reopen any raid with `/raid close`. Moving a raid re-arms it.
- Tests: new `tests/test_v19.py`, and a check that every program file is in the updater's file list (a new file that the updater skipped would break updating).

## 1.8.7
A hardening release: no new commands except `/config backup`. Everything below came out of a code audit.
- **The scheduler can no longer die quietly.** Before, one unexpected error stopped locking, reminders, recurring raids and cleanup until the next restart. Each job now runs on its own, a failure in one raid doesn't affect the others, and the scheduler restarts itself if it ever stops.
- **Failed jobs are retried instead of lost.** A recurring raid whose next post failed (a permissions problem, a Discord outage) used to never repeat again; the post-closed marker and the reminder were also recorded before they actually worked. They are now recorded only on success, retried once a minute, and given up on after 5 tries with a note in `/status`.
- **Long titles and descriptions no longer break the post.** Titles are limited to 256 characters and descriptions to 2000, with a clear message instead of "I couldn't post in this channel". A full roster is shortened to stay inside Discord's 6000-character limit, and a @mention is never cut in half.
- **Daylight saving:** a time that doesn't exist (e.g. 02:30 on the night clocks go forward) or happens twice is now refused with an explanation, instead of silently landing an hour off.
- **Moving a raid re-arms the "who hasn't answered" ping** (it used to be skipped for the new time).
- **Database safety:** the database is checked at startup. If it is damaged, the newest healthy daily backup is restored automatically and the damaged file is kept. Daily backups are verified and written atomically. Upgrades add columns only when missing and no longer hide real errors. New `/config backup` sends admins a private copy of the database.
- **Log file:** errors and warnings go to `raidbot.log` (rotating), not just the window. `/status` shows the last problem. `start.bat` restarts the bot after a crash (but not after a settings problem).
- **A rush of signups costs fewer edits:** clicks that arrive while the post is being redrawn are folded into one more redraw, so a busy minute no longer hits Discord's edit rate limit.
- **Security and permissions:** `Update.bat` no longer installs a download it can't verify (it asks you to type INSTALL). Reminders and nudges can't ping `@everyone` or roles even if a raid title contains them. Channel raid leaders can no longer overwrite or delete the server-wide templates. `/config` and `/status` now check for Manage Server themselves, in case a server changed Discord's default.
- A character can't be added past 25 (the sign-up menu can only show 25).
- New test suite `tests/test_v187.py` (one group per finding above) and updater tests for the missing-checksum case.

## 1.8.6
- **Setup works on Linux and macOS as well as it does on Windows:**
  - Startup messages no longer talk about Notepad and `SETUP.bat` on non-Windows systems; they point to `python setup.py` and "a text editor".
  - The setup wizard always prints the Discord web address it wants you to open, so it works on a server with no screen (open it on any computer).
  - README: the `python3-venv` step for Debian/Ubuntu/Raspberry Pi OS, a note on running setup over SSH, and an example systemd service.
- **`SETUP.bat` finds winget by its full path** when it isn't on the PATH (it is installed on most Windows 10/11 PCs but isn't always on the PATH), so installing Python for you works in more situations.
- README: a picture of what a raid post looks like, a Security notes section, a note on which Python to use. Added `SECURITY.md` and issue templates.
- New tests: a fresh-install check (brand-new virtualenv, install, first run, cancelled setup) that runs on Ubuntu and Windows in CI, and a docs check (every README link and anchor works, every slash command is documented, the changelog has an entry for the current version).

## 1.8.5
- **Fixed: v1.8.4 crashed at startup on Python 3.10 and 3.11** with `SyntaxError: f-string: unmatched '('`. Two `/config` messages used an f-string pattern that only Python 3.12 and newer accept. If you updated to v1.8.4 and the bot won't start, double-click `Update.bat` to get this fix (the updater doesn't need the bot to run). Python 3.12+ and anyone whose Python was installed by `SETUP.bat` were not affected.
- New test (`tests/test_py310_syntax.py`) that fails on any construct needing Python 3.12+, so this can't recur even when developing on a newer Python. GitHub's automated tests also run on Python 3.10, which is what caught it.

## 1.8.4
- **Fixed: a player with a main character could not confirm it in the Sign Up menu.** The menu preselected the main, and choosing an already-selected option sends nothing to the bot, so a player with a single saved character was stuck (the class picker still worked). Nothing is preselected now and the main is marked with ⭐. Found while testing on a live Discord server.
- Fixed wording like "1 days" / "1 hours" / "1 minutes" in `/config` replies and `/config show`.
- **README rewritten:** a plain-English "what is this and how does it work" section, a prominent note that the bot must run 24/7 (and what happens when it doesn't), detailed step-by-step setup, a first-raid walkthrough, FAQ, a much longer troubleshooting table, and a tip about dismissing the private "Only you can see this" replies.
- Verified live on a Discord server with two accounts: all signup buttons, `/raid remove` and `unsigned`, templates (save, list, delete, create from), `/raid copy`, `/raid repeat`, automatic threads (and their removal on cancel), the activity log, old-post cleanup, the unanswered nudge, and the reminder (including the fix for empty rosters).

## 1.8.3
- **Fixed: `Update.bat` failed with "The term 'Get-FileHash' is not recognized"** when Windows PowerShell was started from PowerShell 7 (found by the new GitHub Actions run on Windows). The updater now uses .NET directly for the checksum and for unzipping, so it no longer depends on PowerShell's module path.

## 1.8.2
- **One-click updates:** double-click `Update.bat`. It checks GitHub for the latest release, verifies the download's checksum, backs up your current program files into `backups\code-<version>-<time>`, and replaces only the program files. Your settings (`.env`), data (`raidbot.db`), backups and icons are never touched, and a failed or corrupted download changes nothing.
- Fixed: `/raid roster` no longer lists someone you've marked as a no-show as "attending".
- Tests: new updater suite (runs against a fake GitHub on localhost) and a roster check.
- Verified on a live Discord server: `/raid repeat`, the activity log channel, and a member with no officer role being refused `/raid`. The real `Update.bat` flow was also run against the live v1.8.0 → v1.8.1 releases.

## 1.8.1
- **Fixed: reminders are no longer used up when nobody has signed up yet.** If the reminder time arrived with an empty roster, the reminder was marked as sent and later signups never got one. It now waits until the first person has signed up (benched and absent players don't count).
- **Fixed: setup suggested the wrong timezone for British Columbia.** BC now stays on UTC-7 all year, which Windows reports as a new zone, "British Columbia Standard Time". The setup wizard only knew a fixed list of Windows zone names, so it fell back to a menu where Los Angeles (UTC-8 in winter) was easy to pick, putting raid times an hour off. The guess now checks the PC clock's real UTC offset, and the menu starts with Vancouver and Edmonton. `tzdata` is now required at version 2026.2 or newer, the first release that knows BC's permanent time.
- Fixed wording: "Tank slots are full" instead of "Tanks slots are full".
- `/stats team` shows display names instead of @mentions, so leaders no longer see "@unknown-user" for people their client hasn't loaded.
- Tests: new reminder suite; timezone-guess checks; every suite still runs offline with `python tests/run_all.py`.
- Verified on a live Discord server with two accounts: bench promotion with DM, scoped raid leaders, recurring raids, templates, copy, roster, no-shows and attendance stats, the unanswered nudge, locking, and the closed/reopen/cancel flow.

## 1.8
- **Attendance:** `/raid noshow` marks people who signed up but didn't turn up; `/stats me` and `/stats team` show attended, late, no-show and declined counts plus a "showed up" percentage.
- `/myraids` lists the raids you've answered, with links.
- `/status` health check: version, uptime, per-channel permission problems, last backup.
- Optional "who hasn't answered" ping (`/config nudge`) and an activity log channel (`/config logchannel`).
- Fixed: `/status` crashed while the gateway was reconnecting (latency is `NaN` then).
- Tests moved into `tests/` with a one-command runner (`python tests/run_all.py`).
- Public release: MIT license, rewritten README, contributing guide. Class icons are no longer bundled (Blizzard artwork); the bot falls back to coloured squares, or uses icons you drop into `icons/`.

## 1.7
- Recurring raids (`repeat`, weekly or every 2 weeks, keeps the same local time across daylight saving), templates (`/raid template`), `/raid copy`.
- Optional discussion thread per raid (`/config threads`) and optional cleanup of old posts (`/config archive`).

## 1.6
- Multiple raid teams per server: `/config channel` sets a ping role and a raid-leader role per channel or category; leaders manage only their own channel's raids.
- The raid picker shows `#channel · Title · date` and only lists raids you can manage.

## 1.5
- `/raid edit`, `/raid roster`, `/help`, role ping on create, `/config` (timezone, reminder, officer role, ping role).
- Automatic bench promotion with a DM, composition warnings on the post, daily database backups, friendly error replies.

## 1.4
- Startup self-check with plain-English fixes for the common setup mistakes.
- One-click `SETUP.bat` wizard that creates the settings file and deletes itself.

## 1.0 to 1.3
- Core bot: saved characters, signup buttons, Raid-Helper-style embed with role groups and stable signup order, bench, reminders, event locking, class emoji, Windows launcher.
