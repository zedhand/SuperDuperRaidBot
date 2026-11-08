# Changelog

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
