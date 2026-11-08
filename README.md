# SuperDuperRaidBot

**A free, open-source Discord raid-signup bot for World of Warcraft guilds.**
Self-hosted, no subscription, no premium tier, no account to create. Install it in a few minutes, and your
guild's roster, characters and raid history stay on your own computer.

[![Tests](https://github.com/zedhand/SuperDuperRaidBot/actions/workflows/tests.yml/badge.svg)](https://github.com/zedhand/SuperDuperRaidBot/actions/workflows/tests.yml)
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

**Jump to:** [What is this?](#what-is-this-in-plain-english) · [Keep it running 24/7](#important-the-bot-must-stay-running) ·
[Setup](#setup-windows-about-10-minutes) · [Using it](#using-it) · [FAQ](#faq) · [Troubleshooting](#troubleshooting) ·
[Updating](#updating) · [For developers](#for-developers)

---

## What is this? (in plain English)

If your guild uses a Discord server to organise raids, you've probably seen the usual mess: "who's coming Thursday?",
people changing their minds in chat, nobody sure whether you have a tank. This bot replaces that with **one tidy post per raid**:

- An officer types one command and a **raid post** appears in your signup channel.
- Each player clicks **Sign Up**, picks their character, and they show up in the right column (Tank, Healer, Melee, Ranged).
- Players who can't make it click **Absent**; unsure players click **Tentative**; late ones click **Late**.
- If a role is full (say you only want 2 tanks), extra people go on the **bench** automatically, and the first benched
  person is moved in and messaged if a spot opens up.
- Everyone gets a **reminder** shortly before the raid, and the post **locks** when the raid starts.
- Raids can **repeat every week by themselves**, and the bot keeps simple **attendance** records.

**How it works, with no jargon.** The bot is a small program that you run on a computer. It logs into Discord under its
own name, like an extra (robot) member of your server. When someone types a command or clicks a button, Discord hands that
request to the program. The program writes it into its notebook (one small file called `raidbot.db` on your computer)
and updates the raid post you see. There are four pieces:

| Piece | What it is |
|---|---|
| **Discord** | Where everyone chats and clicks buttons. Nothing to install for players. |
| **The bot** | The small program that reads clicks and commands and edits the posts. **This is the part you run.** |
| **The notebook** | `raidbot.db`, a file next to the bot with everyone's characters and signups. Backed up automatically each day. |
| **You** | The person who sets it up once and keeps it running. |

It does **not** read your server's chat messages. It only reacts to its own commands and buttons.

---

## Important: the bot must stay running

> **This is a Discord bot, and a bot only works while its program is running.** It can't do anything on its own from "the
> cloud". It has to be switched on and connected to the internet so it can talk to Discord. To get the full
> experience, **it needs to run 24 hours a day, 7 days a week.**

**What happens when it's not running** (computer off, asleep, the black window closed, or no internet):

- Buttons on raid posts stop working and show **"This interaction failed"**.
- Slash commands show **"The application did not respond"**, and the bot appears offline in your member list.
- Reminders, "who hasn't answered" pings and automatic cleanup don't happen while it's off.
- **When it comes back**, it catches up on the important things: raids that have started get locked, and recurring raids
  post their next date. Reminders for raids that have already started are skipped.
- **Nothing is lost.** Everyone's signups are saved in `raidbot.db` and are all still there when it starts again.

**How to keep it running** (pick one):

1. **A PC that's always on.** Run `start.bat` and leave the window open. In Windows, set the PC to never sleep:
   *Settings → System → Power → Screen and sleep → put my device to sleep after: Never*. Closing the window stops the bot.
2. **A spare laptop or mini-PC** left on in a corner is perfect for this. It barely uses any power or internet.
3. **A Raspberry Pi or a small rented server (VPS).** It's plain Python, so it runs on Linux too. See
   [any OS, from source](#any-os-from-source).

**Start it automatically when Windows starts** (so a reboot or power cut doesn't take it down for long). The easy way: at
the end of `SETUP.bat` answer **y** to *"Start the bot automatically when Windows starts?"*, or double-click **`Shortcuts.bat`**
whenever you like. It can also put a **Raid Bot** shortcut on your desktop, and switch either one off again. By hand: press
`Win + R`, type `shell:startup`, press Enter, then drag a **shortcut** to `start.bat` into the folder that opens.

If you only run raids at set times (say two evenings a week), you *can* switch it on just for those evenings, but people
won't be able to sign up in between, so a 24/7 setup is strongly recommended.

---

## Setup (Windows, about 10 minutes)

You need: a Windows PC, an internet connection, and a Discord server where you have **Manage Server** permission. You do
**not** need to know any programming. Python is installed for you if it's missing.

### Step 1: Download and unzip
1. Go to the [Releases page](../../releases) and download the latest **`SuperDuperRaidBot-v….zip`**.
2. Right-click the zip → **Extract All…** and choose somewhere permanent, like `C:\RaidBot` or your Documents folder.
   (Don't run it from inside the zip, and don't put it somewhere that gets cleaned up.)

### Step 2: Run the setup
Double-click **`SETUP.bat`**. Windows may say *"Windows protected your PC"* because the file is new. Click **More info →
Run anyway**. A black window opens and does the technical part (installing Python if needed, and the bot's components, which takes a minute or two). Then it guides
you through five short steps and opens the right Discord pages for you:

| Step | What you do |
|---|---|
| **1. Create the bot and copy its token** | A Discord Developer Portal page opens. Click **New Application**, name it anything (e.g. "Raid Bot"), open **Bot** in the left sidebar, click **Reset Token**, then **Copy**. Paste the token into the black window (right-click pastes) and press Enter. It checks the token immediately and tells you if it's wrong. |
| **2. Allow the bot to see member lists** | On the same **Bot** page, scroll to **Privileged Gateway Intents**, switch **Server Members Intent** on, and click **Save Changes**. The setup notices when you've done it. |
| **3. Add the bot to your server** | A page opens asking which server to add the bot to. Pick yours and click **Authorize**. The setup notices when the bot has joined, and picks your server automatically. |
| **4. Timezone** | Setup suggests your PC's timezone. Press Enter to accept, or choose another. Raid times you type are read in this timezone. |
| **5. WoW version** | Choose Retail, Classic Era, Season of Discovery, Burning Crusade, Wrath, Cataclysm, Mists of Pandaria or WoW: Forever. This decides which classes and specs people can pick. |

> **Treat the token like a password.** Anyone who has it can control your bot. Never post it in Discord or share your `.env`
> file. If it ever leaks, go back to the Developer Portal → Bot → **Reset Token**.

At the very end it asks two optional yes/no questions (the answer is **no** unless you type `y`): a desktop shortcut, and starting the bot when Windows starts. You can change both later with `Shortcuts.bat`.

When it's done, `SETUP.bat` fills in your settings file (`.env`), **deletes itself**, and starts the bot. You'll see
lines like `Logged in as …`. **Leave that window open** (see [keep it running](#important-the-bot-must-stay-running)).
From then on, start the bot by double-clicking **`start.bat`**. To stop it, close the window.

### Step 3: Check the bot has access to your channels
The bot needs these permissions in each channel where raids are posted: **View Channel, Send Messages, Embed Links,
Read Message History** (and **Create Public Threads** if you want discussion threads). If your signup channels are
private, add the bot's role to them. Run **`/status`** in Discord any time: it lists channels where something is missing.

### Step 4: Give officers a way to post raids
Anyone with the **Manage Events** permission can already post raids. To let a role do it without that permission, run
`/config officer` (see [Who can do what](#who-can-do-what)).

You're done. Continue to [Using it](#using-it).

### Any OS, from source
For Linux, macOS, a Raspberry Pi or a server. You need Python 3.10 or newer (3.12 is recommended).

```bash
git clone https://github.com/zedhand/SuperDuperRaidBot.git
cd SuperDuperRaidBot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip
.venv/bin/python setup.py                      # guided setup: token, server, timezone -> writes .env
.venv/bin/python bot.py                        # leave it running (see the 24/7 note above)
```

- **Debian / Ubuntu / Raspberry Pi OS:** if `python3 -m venv .venv` says *"ensurepip is not available"*, run
  `sudo apt install python3-venv python3-pip` first, then try again.
- **On a server with no screen** (a VPS, a Pi over SSH): the setup wizard prints the Discord web addresses it wants you to
  open, so open them in a browser on **any computer** and carry on. It notices when you've finished each step and doesn't
  need to run on the same machine as your browser.
- **Doing it by hand instead:** copy `.env.example` to `.env`, fill it in (see [Settings](#settings-env)), and run `bot.py`.
- **Keeping it running** after you log out or reboot: use a service manager. As an *example* on a systemd Linux machine, save
  this as `/etc/systemd/system/raidbot.service` (adjust the user and folder), then run
  `sudo systemctl enable --now raidbot`:

  ```ini
  [Unit]
  Description=SuperDuperRaidBot
  After=network-online.target
  Wants=network-online.target

  [Service]
  User=youruser
  WorkingDirectory=/home/youruser/SuperDuperRaidBot
  ExecStart=/home/youruser/SuperDuperRaidBot/.venv/bin/python bot.py
  Restart=always
  RestartSec=10

  [Install]
  WantedBy=multi-user.target
  ```

  `screen` or `tmux` also work if you just want it to survive closing your SSH window.

---

## Using it

### What a raid post looks like
Roughly this (names made up). Everyone sees the time in their own timezone, and the post updates itself as people click.

```
Naxxramas
🏳️ @Officer              👥 7                  ⏳ in 2 days
📅 November 14, 2026      🕒 8:00 PM

🛡️ Tank (2/2)            ⚔️ Melee (1)          🏹 Ranged (2)
1 Thrall                  4 Valeera             5 Jaina
2 Uther                                         6 Khadgar

💚 Healer (1/6)
3 Anduin

⚠️ Need 5 more healers (1/6)

🪑 Bench (1): 7 Garrosh
⏰ Late (1): 8 Sylvanas
❌ Absence (1): 9 Illidan

Event #12 • DPS 4/17 • 🔁 weekly
[ Sign Up ]  [ Late ]  [ Tentative ]  [ Bench ]  [ Absent ]
```

Numbers show the order people signed up in. If a class icon is available (see [Class icons](#class-icons)) it's shown next to each name.

### Your first raid (officers)
1. Go to the channel where you want the raid posted (your signup channel).
2. Type `/raid create` and press Enter. Discord shows the options. Fill in **date** and, if you like, a **title**, and the
   maximum number of **tanks**, **healers** and **dps** (`0` means no limit). Pick the day from the list Discord offers, or
   just type it, with the time on the end: `thu 8pm`, `tomorrow 19:30`, `nov 14 8pm`, `+7d 20:00` or `2026-11-14 20:00`.
   (You can also use the separate **time** box: `20:00` or `8pm`.)
3. A **raid post** appears with buttons. Players click **Sign Up**.

```
/raid create   date: thu 8pm   title: Naxxramas   tanks: 2   healers: 6   dps: 17   ping: @Raiders
```

How dates are read: a bare weekday (`thu`) is the next one, and today if its time hasn't passed yet; `next thu` is never today;
`nov 14` is the next November 14; a bare number like `8` is refused (morning or evening?), so write `8pm` or `20:00`. Times are in
your server's timezone. If a time doesn't exist (clocks jump forward) or happens twice (clocks go back), the bot says so.
The same date entry works in `/raid copy` and `/raid edit` (in `/raid edit` you can change just the day or just the time).

Times are in the timezone chosen during setup (change it any time with `/config timezone`). On the post itself, every player
sees the time in **their own** timezone with a live countdown.

### Signing up (players)
1. **Once:** save your characters with `/character add` (name, class, spec, and optionally the realm). Pick one as your **main** with `/character main`.
2. On a raid post click **Sign Up**. If you've signed up before, the top button is **⚡ Sign up as (your last character)**:
   one click and you're in. (Your main is used the very first time.) Or choose a different saved character (your main is
   marked ⭐), **or** pick a class and spec on the spot if you haven't saved one.
3. To change your mind, or switch character, click **Sign Up** again, or use the other buttons.

| Button | Meaning |
|---|---|
| **Sign Up** | You're coming. Pick your character. |
| **Late** | You're coming but will be late. You still count toward the roster. |
| **Tentative** | Maybe. Not counted yet. |
| **Bench** | Happy to fill in if there's room. |
| **Absent** | You can't make it. |
| **🔄 Spec** | Only if an admin turned it on (`/config specswitch`). Switch to another spec of the character you signed up with, e.g. from Fire to Frost, or from Protection to Fury. |
| **📅 Calendar** | Gives you a file (`.ics`) to add the raid to Google, Apple or Outlook calendar. Opening it again after the raid moves updates the entry. |

**Switching spec (optional, `/config specswitch`).** With the switch on, raid posts get a **🔄 Spec** button. Press it and pick
another spec of the class you signed up with: your character, status and note stay as they are. If the new spec has a different
role (say you go from healer to dps), the role limits are checked again, so you may be put on the bench when that role is full,
or move in when you were waiting for a slot. Your saved characters aren't changed. To switch to a different character, press
**Sign Up** again.

Late / Tentative / Bench / Absent reuse the character you signed up with (or your main). If you've never signed up and have
no saved character, the bot asks you to press **Sign Up** first.

**When a role is full** you're put on the bench automatically, and the bot tells you your place in line (for example
"#2 in line for a tank slot"). The bench list on the post shows it too, for example `(#2 tank)`. When a slot opens, the
first person in line for that role is moved in and messaged.

**Private reminders.** Type `/remindme on: True` and, before a raid you're signed up for, the bot messages you privately
instead of pinging you in the channel. If your private messages from the server are closed, it pings you in the channel as
usual. `/remindme on: False` goes back.

**Repeating raids: sign up once.** For a raid that repeats every week, type `/always join`, pick the raid, and you are
signed up to this one and to every new one as it is posted, with the character you chose. You can still press **Absent**
on any single date. `/always list` shows what you're in, and `/always leave` stops it.

### What the bot does by itself
- **Full roles → bench → promotion.** If a role's limit is reached, the next person is benched automatically. When a spot
  opens (someone leaves, or the limit is raised), the first auto-benched person in that role moves in and gets a **message**. People who chose Bench themselves stay on the bench.
- **Warnings** on the post while signups are open, like `⚠️ Need 1 more tank (1/2)` or `⚠️ No healer signed up yet`.
- **Reminder** before the raid (default 30 minutes) pinging everyone who's in, late or tentative. It's sent once. If nobody has signed up yet when it's due, it waits for the first signup.
- **Locking.** When the start time passes the post greys out and signups close.
- **Recurring raids.** With `repeat` set, the next one is posted when the current one starts.
- **Automatic no-shows (optional).** With a voice channel set (`/config voicechannel`), 15 minutes after a raid starts the bot looks at who is in that channel. Anyone signed up as **in** who isn't there is marked as a no-show, so officers don't have to do it by hand, and the result is written to the log channel. If **nobody** is in the channel the check is skipped (the raid is probably being run somewhere else), people marked **late** are never checked, and a mistake is one `/raid noshow` (undo) away. It only looks at raids that started in the last 6 hours, so turning it on never rewrites old attendance.
- **Class limits (optional).** `/raid create classes: Mage 4, Warlock 2` limits how many of a class can be in. Extra people go on the bench and move in when a spot opens, like role limits. The limits show in the post's footer and are kept by templates, copies and repeating raids. `/raid edit classes: off` removes them.
- **Signup notes (optional, `/config notes`).** A player types `/note` and picks a raid they signed up for to add a short note, e.g. `late 15 min` or `can swap to heals` (up to 60 characters; leave the text out to clear it). The note stays when they change status or character. Officers see notes in `/raid roster`, and with `onpost: True` they also show in italics after the name on the raid post. Formatting, mentions and links are taken out, so a note can't ping anyone or break the post.
- **Missing utility warning (optional, `/config buffs`).** Once 8 or more people are in and none of them can provide it, the post says `No Bloodlust yet (Shaman, Mage, Evoker, Hunter)` or `No a battle res yet (...)`. The lists are in `wow_data.py` and are short on purpose; Classic Era, Season of Discovery and WoW: Forever have no Bloodlust.
- **New-version notice (optional, `/config updatenotice`).** About once a day the bot asks GitHub for the newest release and says so **once per version** in the log channel, with a link. GitHub only sees a request from your computer; nothing about your server is sent. `/status` then also shows the latest version.
- **Weekly digest (optional, `/config digest`).** Every Monday after 09:00 (server time) a summary of the last 7 days is posted in the log channel: raids, attended, no-shows, who had the most no-shows.
- **Daily backup** of your data (the last 7 are kept). Each copy is checked right after it is made, and if the database is ever found damaged when the bot starts, it restores the newest healthy backup by itself and keeps the damaged file.

### Commands

| Who | Command | What it does |
|---|---|---|
| Everyone | `/character add / list / main / remove` | Manage your saved characters |
| Everyone | `/character realm` | Set or clear the realm of a saved character (optional; `/character add realm:` works too) |
| Everyone | `/myraids` | The upcoming raids you've answered, with links |
| Everyone | `/note` | Add a short note to your signup, e.g. `late 15 min` (leave the text out to clear it). Only if an admin turned it on |
| Everyone | `/remindme` | Get raid reminders as a private message instead of a channel ping |
| Everyone | `/forgetme` | Delete your characters and signup history from the bot (asks first, can't be undone) |
| Everyone | `/character import` | Look a **retail** character up on Raider.IO and save it (only if an admin turned it on) |
| Everyone | `/always join / leave / list` | Stay signed up to a repeating raid automatically |
| Everyone | `/stats me` | Your own attendance (last 90 days) |
| Everyone | `/help` | Command list inside Discord |
| Officers | `/raid create` | Post a raid (options: title, template, description, tanks, healers, dps, classes, ping, repeat, thread) |
| Officers | `/raid list` | Your upcoming raids with sign-up counts, times and links |
| Officers | `/raid add` | Sign someone up, or change their status or character (works even if sign-ups are closed) |
| Officers | `/raid promote` | Move a specific benched player into the raid, ahead of the line. Refuses if their role or class is full unless you add `over_limit: True` |
| Officers | `/raid edit` | Change title, date, time, description or limits. If the time changes, signed-up people are told |
| Officers | `/raid copy` | The same raid on a new date (same limits and description, no signups) |
| Officers | `/raid repeat` | Repeat weekly or every 2 weeks, or stop repeating |
| Officers | `/raid template save / list / delete` | Saved setups; use one with `/raid create template:` |
| Officers | `/raid roster` | A private, copy-friendly list of who's coming. Add `invites: True` for `/invite Name-Realm` lines to paste into WoW |
| Officers | `/raid noshow` | Mark someone who signed up but didn't turn up (add `undo: True` to take it back) |
| Officers | `/raid close` | Close signups (run it again to reopen) |
| Officers | `/raid cancel` | Delete a raid and its post (and thread). `/raid restore` undoes it |
| Officers | `/raid restore` | Bring back a raid cancelled in the last 14 days, with all its signups, as a new post |
| Officers | `/raid remove` | Remove someone's signup |
| Officers | `/raid unsigned` | List (or ping) members of a role who haven't answered |
| Officers | `/stats team` | Everyone's attendance |
| Officers | `/stats export` | The same as a spreadsheet file (CSV). It has Discord ids, so keep it private |
| Admins | `/config show` | Show every setting |
| Admins | `/config timezone / reminder / officer / pingrole` | Server-wide settings |
| Admins | `/config channel` | Per-channel (or per-category) ping role and raid-leader role |
| Admins | `/config threads / archive / nudge / logchannel` | Optional extras. All are **off** until you turn them on |
| Admins | `/config raiderrole` | Only members with this role can sign up (default: anyone) |
| Admins | `/config realms` | Show each character's realm after the name on raid posts, e.g. `Thrall (Area 52)` (default: off) |
| Admins | `/config specswitch` | Put a **🔄 Spec** button on raid posts so a signed-up player can switch spec without signing up again (default: off) |
| Admins | `/config notes` | Let players add a note to their signup with `/note`. Officers see notes in `/raid roster`; `onpost: True` also shows them on the raid post (default: off) |
| Admins | `/config confirm` | Ask "are you sure?" before `/raid cancel` and `/raid remove` (default: off) |
| Admins | `/config autoclose` | Close sign-ups a set number of hours before a raid (default: off) |
| Admins | `/config voicechannel` | Mark no-shows automatically from a voice channel (default: off). `/config channel voice:` sets one per raid team |
| Admins | `/config buffs` | Warn on the post when nobody can provide Bloodlust or a battle res (default: off) |
| Admins | `/config digest` | Post a weekly attendance summary in the log channel (default: off) |
| Admins | `/config setup` | A guided private panel for the main settings: roles, channels, timing and the optional extras |
| Admins | `/config raiderio` | Allow `/character import` (retail only, default: off) and set a default region and realm |
| Admins | `/config updatenotice` | Say in the log channel when a newer version is released (default: off) |
| Admins | `/config backup` | Sends you a private copy of the database as a file |
| Admins | `/config board` | A pinned **Upcoming raids** message in a channel you pick, with each raid's time, signups per role and a link to its post. It updates by itself (default: off) |
| Admins | `/config backupchannel` | Post a copy of this server's data in a private admin channel every day, so a backup lives off the bot's PC (default: off) |
| Admins | `/status` | Health check: version, uptime, channel permissions, last backup, last problem |

**Extras explained**
- `/config threads` starts a discussion thread under every new raid.
- `/config archive` deletes raid posts a set number of days after they start. Records are kept.
- `/config nudge` pings people in a raid's ping role who haven't answered, a set number of hours before the raid.
- `/config raiderrole` limits sign-ups to one role. Officers are always allowed, and can sign people up with `/raid add`.
- `/config autoclose` closes sign-ups that many hours before the start. Reopen a raid any time with `/raid close`.
- `/config voicechannel`, `/config buffs` and `/config digest` switch on the optional extras described under *What the bot does by itself*.
- `/config board` posts one **📅 Upcoming raids** message in the channel you choose and pins it. It lists the next 15 raids
  with their time (in each reader's own time zone), how many are in per role, and a link to the post, and it edits itself
  when a raid is posted, edited, cancelled or restored, when people sign up, and when a raid starts (it drops off). A quiet
  channel such as `#raid-schedule` works best. A raid is only listed if everyone who can read the board can also see the
  raid's channel, so officer-only or team-only raids stay hidden on a public board. Run it again to move the board or to bring
  it back if someone deleted it, and leave the channel empty to turn it off. Pinning needs **Manage Messages**; without it the
  board still works, unpinned.
- `/config logchannel` posts a one-line note whenever a raid is created, edited, cancelled, closed or someone is marked a no-show.

### Who can do what
- **Officers** are people with the **Manage Events** (or Administrator) permission, or the role you set with `/config officer`.
- **Admins** are people with **Manage Server**. The `/config` and `/status` commands are hidden from everyone else.
- A **channel leader** is someone with a role set by `/config channel`. They can create and manage raids **only in that channel
  or category**, which is how several raid teams can share one server.

### Running several raid teams
Say you have categories **Raid 1**, **Raid 2** and **20 Mans**, each with a signup channel. Give the bot access to those
channels, then in each signup channel run:

```
/config channel   ping: @Raid1   officer: @Raid1Leaders
```

Raid 1's leaders can now post and manage Raid 1's raids, which ping `@Raid1`, and can't touch Raid 2's. Settings on a channel beat
settings on its category, which beat the server-wide ones. Players keep one set of saved characters for all teams.
To ping a role, the role needs **"Allow anyone to @mention this role"** switched on (Server Settings → Roles); the bot tells you if it isn't.

### Tip: dismiss the private replies
Many bot replies are **private**: they say **"Only you can see this"** and have a **Dismiss message** link underneath.
They don't bother anyone else, but they pile up in *your* view the more you use commands. Click **Dismiss message** on
each as you go, or press **Ctrl + R** (**Cmd + R** on a Mac) to reload Discord, which clears them all at once.

---

## FAQ

**Is it really free?** Yes. It's MIT licensed: free to use, change, share and even rebrand. There's no paid tier and no account.

**Do my players need to install anything?** No. They only use Discord: slash commands and buttons.

**Does the bot have to be running all the time?** Yes, for it to work. It only reacts while its program is running. See
[the 24/7 section](#important-the-bot-must-stay-running).

**Where is my data, and who can see it?** On the computer running the bot, in `raidbot.db`. Nothing is sent anywhere
except the posts and messages the bot sends in your own Discord server.

**Can I run it from my phone, or from Discord itself?** No. It needs a computer, a Raspberry Pi or a server to run on.

**Which Python version do I need?** Python 3.10 or newer. 3.10 no longer gets upstream security updates, so 3.12 is
recommended; on Windows, `SETUP.bat` installs 3.12 for you if you don't have a suitable Python.

**Can I use it in more than one Discord server?** It's built for one server per bot. Run a second copy (in another folder, with
its own bot token) for a second server.

**Can I change the class and spec list?** Yes. Eight lists come built in (every WoW version above). Edit `wow_data.py` to add or rename specs
(for example when a patch adds one).

**How do I get class icons?** They're not included, because the artwork belongs to Blizzard. Without them the bot uses coloured
squares. To use icons, put images named after each class in the `icons` folder (see `icons/README.txt`).

**Can I get the raid in my phone's calendar?** Yes: press the **📅 Calendar** button on the raid post and open the file it sends you. Raids have no end time, so the calendar shows 3 hours.

**I cancelled the wrong raid. Can I get it back?** Yes, for 14 days. `/raid restore` lists this server's recently cancelled raids; pick one and it is posted again in its channel with everyone's signups. After 14 days the copy is deleted for good.

**How do I invite everyone in game without typing each name?** Run `/raid roster invites: True`. It gives you one `/invite` line per player who is in or late, with the realm (`Thrall-Area52`) for anyone whose saved character has one, so players from other realms work too. Paste a line into WoW chat, or each block into its own macro: the lines are grouped so a block fits in one macro (255 characters). Players on the bench, tentative or marked as a no-show aren't listed.

**Someone is on the bench but we want them in tonight. Can we skip the line?** Yes. `/raid promote` moves that player in straight away and tells them. If their role or class is already full it asks you to free a slot first, or to add `over_limit: True` to take them in anyway.

**A player made a mistake. Can an officer fix their signup?** Yes. Use `/raid remove` to clear it, or `/raid add` to set their status, character or spec for them.

**What if I move to a new computer?** Copy the whole bot folder, or at least `raidbot.db` and `.env`, to the new computer and
run `start.bat`. Everyone's characters and raids carry over.

**What if my bot token leaked?** Go to the Discord Developer Portal → your application → **Bot** → **Reset Token**, paste the new
token into `.env` (or run `SETUP.bat`/`python setup.py` again), and restart the bot.

**How do I stop the bot, or remove it completely?** Close its window to stop it. To remove it, delete the folder and kick the
bot from your server (Server Settings → Members).

**Why do raid times look different to me than what I typed?** You type times in the *server's* timezone; the post shows each person
the time in *their own* timezone. Check the server's timezone with `/config show` and change it with `/config timezone`.

**Why are there "Only you can see this" messages everywhere?** They're private replies. See
[Tip: dismiss the private replies](#tip-dismiss-the-private-replies).

---

## Troubleshooting

When the bot can't start it explains the problem and the fix in its window. Read the last lines. For everything else:

| What you see | What it means / what to do |
|---|---|
| Buttons say **"This interaction failed"**, or commands say **"The application did not respond"** | The bot isn't running (or lost its connection). Start `start.bat` and check the window for an error. |
| The bot shows **offline** in the member list | Same: it isn't running. It appears online a few seconds after `start.bat` starts. |
| Slash commands don't show up | Wait a minute and restart Discord (Ctrl + R). Make sure the bot was added with the invite link from setup (it needs the *applications.commands* scope), and that it's running. |
| `There is no file named exactly '.env'` | The settings file must be named `.env` and sit next to `bot.py`. In File Explorer turn on *View → Show → File name extensions* to spot names like `.env.txt`. |
| `DISCORD_TOKEN is empty` | Run `SETUP.bat` (or `python setup.py`), or paste the token after `DISCORD_TOKEN=` in `.env`. |
| `DISCORD_TOKEN … looks wrong` | You pasted the wrong thing (Public Key, Client Secret or an ID). Use **Bot → Reset Token → Copy**. |
| `Discord rejected this token` | The token was reset or copied wrong. **Bot → Reset Token → Copy**, then paste it into `.env`. |
| `Server Members Intent is switched off` | Developer Portal → Bot → Privileged Gateway Intents → turn on **Server Members Intent** → Save Changes. |
| `The bot isn't in any server yet` | Open the link it prints, pick your server and click Authorize. |
| `GUILD_ID … isn't a server this bot is in` | Fix or blank out `GUILD_ID` in `.env`, or add the bot with the printed link. |
| `Only raid officers can use /raid commands` | You need **Manage Events**, the `/config officer` role, or a channel officer role. |
| `You can only manage raids in channels you're an officer for` | That raid belongs to another team's channel. Use the right channel or ask an admin. |
| A raid post doesn't appear, or doesn't update | The bot lacks permission in that channel. Give its role **View Channel, Send Messages, Embed Links, Read Message History** there (or on its category). `/status` shows which channels have a problem. |
| "I couldn't start a thread" | Give the bot **Create Public Threads** in that channel (or its category). |
| `@unknown-user` or a long number instead of a name | Discord hasn't loaded that member in your view yet. Scroll the member list or reload Discord (Ctrl + R). |
| The next recurring raid didn't appear | It's posted when the previous one **starts**. The bot must be running then and able to post in that channel. |
| Raid times are an hour off | Check `/config show` for the timezone. Set the right one with `/config timezone` (for example `America/Vancouver`). |
| No reminder arrived | Reminders go only to people who are signed in, late or tentative, once, before the raid starts, and only while the bot is running. Check `/config reminder`. |
| `Python is not installed` | `SETUP.bat` installs it. If that fails, install Python from python.org, tick **Add python.exe to PATH**, and run `SETUP.bat` again. |
| The bot window closed or the bot stopped by itself | Open `raidbot.log` (next to `bot.py`) and read the last lines. `start.bat` restarts the bot automatically after a crash. |
| "That time doesn't exist" / "happens twice" when creating a raid | Clocks change that night in your timezone (daylight saving). Pick a time before or after the change. |
| Windows says **"Windows protected your PC"** | Click **More info → Run anyway**. The file is new and unsigned. |
| The update failed | Close the bot, try `Update.bat` again, or [update by hand](#updating). Your settings and data are never touched. |

Still stuck? [Open an issue](../../issues/new/choose) with the bot version and the last lines from the bot's window or `raidbot.log`
(**remove your token first**).

---

## Security notes

- **The bot token is a password.** Keep `.env` private; never post it in Discord, in an issue, or in a screenshot. `.env` is
  git-ignored. If it leaks, reset it right away (Developer Portal → Bot → **Reset Token**).
- **What the bot can see:** member lists (that's what *Server Members Intent* is for, e.g. for `/raid unsigned`) and the
  commands and button clicks aimed at it. It does **not** read your chat messages.
- **What it can do:** only what its role allows in each channel. Give it just the channels it needs.
- **What it sends to other websites:** nothing by default; it only talks to Discord. Two **optional** features contact another site, and only when an admin turns them on: `/config updatenotice` asks GitHub for the newest release number (no information about you or your server), and `/config raiderio` lets a player run `/character import`, which sends the character name and realm *that player typed* to raider.io to look it up (no accounts, nothing else).
- **`Update.bat` downloads code from this repository's GitHub releases** and checks the download against the checksum GitHub
  publishes for it, which catches corrupted downloads. It can't protect against the repository itself being compromised, so
  only run it if you trust this project (or your fork).
- Found a security problem? See [SECURITY.md](SECURITY.md).

## Settings (`.env`)

`SETUP.bat` / `setup.py` fill this in for you, and most things are changed live with `/config`. To edit it by hand, use Notepad,
save the file, and restart the bot.

| Setting | Meaning |
|---|---|
| `DISCORD_TOKEN` | The bot's password. **Never share it or commit it.** `.env` is git-ignored |
| `GUILD_ID` | Your server's ID. Optional: blank + bot in one server = it picks that one |
| `TIMEZONE` | Default timezone (`/config timezone` overrides it per server) |
| `WOW_VERSION` | the class/spec list: `retail`, `classic_era` (also Hardcore), `season_of_discovery`, `tbc_classic`, `wrath_classic`, `cata_classic`, `mists_classic` or `wow_forever` |
| `REMINDER_MINUTES` | Default reminder lead time (`/config reminder` overrides it) |

## Updating

Close the bot, then double-click **`Update.bat`**. It downloads the latest release, checks its checksum, saves your current
program files into `backups\code-<version>-<time>`, and replaces only the program files. Your settings (`.env`), data
(`raidbot.db`), backups and icons are never touched, and the database upgrades itself on the next start. Then start the bot
again with `start.bat`. If GitHub doesn't supply a checksum for the download, `Update.bat` can't verify it, so it asks you to type `INSTALL` before going on (press Enter to cancel).

`Update.bat` first appears in v1.8.2. If you're on an older version, update once by hand: unzip the new version into a
**new folder**, copy your old `raidbot.db` and `.env` into it, and run `start.bat`. After that, `Update.bat` does it for you.

## Data, backups and privacy

Everything lives in `raidbot.db` (SQLite) next to the bot: characters, raids and signups. Once a day the bot copies it to
`backups/` and keeps the newest 7. To restore, stop the bot, copy a backup over `raidbot.db`, start it again. No data leaves
your machine except what Discord itself receives (the posts and messages the bot sends). The bot does not read your server's
chat messages.

Any player can type `/forgetme` to delete what the bot stores about them, in every server it's in: their characters, their
signups (and so their attendance history), their `/always` sign-ups, their `/remindme` setting and their place in raids
kept for `/raid restore`. The bot asks first, then takes their name off the raid posts. Raids they created stay. The daily
backups still hold the old data until they're replaced (within 7 days), as does any copy an admin made with `/config backup`.

Admins can get a copy of the database any time with `/config backup` (it contains your members' Discord IDs, so keep it private). If `raidbot.db` is ever damaged, the bot restores the newest healthy daily backup on the next start, keeps the damaged file as `raidbot.db.damaged-<time>`, and `/status` tells you it happened. If there is no healthy backup it stops and explains, leaving the file untouched. Errors are also written to `raidbot.log` (the newest 5 MB are kept).

To keep a backup somewhere other than the bot's PC, `/config backupchannel` picks a channel where the bot posts a copy every
day after 04:00 (server time), starting right away. It refuses a channel that everyone in the server can see, and skips the
post if the channel is made public later. The copy holds only that server's data (other servers the bot is in are left out),
and the bot deletes its own older posts so the newest 7 stay. Leave the channel empty to turn it off. To restore from one, stop
the bot and put the file in place of `raidbot.db`; if the bot runs in more than one server, use the `backups` folder instead,
since the posted copy only has one server's data.

## Class icons

Class and spec data is in `wow_data.py` (add a line when a patch adds a spec). Class icons are **not included**, because the
artwork belongs to Blizzard. Without them the bot uses coloured squares. To use icons, put images named after each class in the
`icons/` folder (`warrior.png`, `deathknight.png`, …); the bot uploads them as its own emoji on startup.

---

## For developers

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
python tests/run_all.py          # every suite, offline, no Discord token needed
```

| File | Role |
|---|---|
| `bot.py` | Commands, buttons, background loops |
| `db.py` | SQLite layer and migrations (older databases upgrade on startup) |
| `embeds.py` | Builds the raid post |
| `wow_data.py` | Class/spec/role data (a preset for each WoW version) |
| `checks.py` | Startup and setup checks against Discord (standard library only) |
| `setup.py` / `SETUP.bat` / `start.bat` | The one-click setup and launcher |
| `Update.bat` / `update.ps1` | One-click updater (downloads the latest release, keeps your settings and data) |
| `tests/` | Offline test suites with a faked Discord (run on every push on Ubuntu and Windows) |

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Ideas and what's planned are in [ROADMAP.md](ROADMAP.md),
and the version history is in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE). Use it, fork it, sell it, rebrand it. No strings attached.
