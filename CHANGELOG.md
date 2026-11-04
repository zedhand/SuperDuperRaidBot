# Changelog

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
