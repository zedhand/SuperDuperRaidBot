# Roadmap

Guiding rule: **no new setup for the user.** No new required settings, no extra installs. Optional features get a sensible default
(usually "off") and a one-line `/config` switch.

Shipped versions are listed in [CHANGELOG.md](CHANGELOG.md). Ideas below are open for anyone to pick up.

## Planned, in build order

Everything below is optional and off by default unless noted. One release (v1.14.0) will roll up both batches.

### v1.13 "safety net"
| # | Feature | Why | Effort | Status |
|---|---|---|---|---|
| 1 | More WoW presets: Season of Discovery, TBC, Wrath, Cataclysm, Mists, WoW: Forever | Only data in `wow_data.py` | S | Done (on the v1.13 branch) |
| 2 | Release automation (tag builds, scans and attaches the zip) and Dependabot | One-click releases, free security upkeep | M | Done (on the v1.13 branch) |
| 3 | Optional confirm before `/raid cancel` and `/raid remove` (`/config confirm`), plus undo for cancel (`/raid restore`, 14 days) | One mistyped ID currently deletes a raid and its signups for good | S-M | Done (on the v1.13 branch) |
| 4 | `/forgetme`: a player deletes their own characters and signup history | Privacy, nearly free | S | Done (on the v1.13 branch) |
| 5 | Daily backup sent to a private admin channel (`/config backupchannel`) | Backups sit on the same PC as the bot | M | Done (on the v1.13 branch) |

### v1.14 "convenience"
| # | Feature | Why | Effort | Status |
|---|---|---|---|---|
| 6 | Realm stored with characters (Raider.IO import fills it), optional on raid posts (`/config realms`) | Needed for invite names | M | Done (on the v1.13 branch) |
| 7 | Short note on a signup (`/note`), shown in the roster and optionally on the post (`/config notes`) | Very common request | S | Done (on the v1.13 branch) |
| 8 | Switch spec from the post without redoing signup | Respeccing is routine | M | Done (on the v1.13 branch, `/config specswitch`) |
| 9 | `/raid promote`: pull a specific benched player in | Bench promotion is strictly first-come today | S | Done (on the v1.13 branch) |
| 10 | `/raid roster` outputs ready-to-paste in-game invite names (`Name-Realm`) | What officers use right before a pull | M | Done (on the v1.13 branch, `/raid roster invites: True`) |
| 11 | Pinned "Upcoming raids" board that edits itself | One place to see the week | S-M | Done (on the v1.13 branch, `/config board`) |
| 12 | Low-signup alert to officers (e.g. 15/25 a day before) | Catches a failing raid early | S | Done (on the v1.13 branch, `/alerts lowsignup`) |
| 13 | Native Discord Scheduled Event with each raid | Shows in the Events tab and phone calendar | M | Done (on the v1.13 branch, `/alerts events`) |
| 14 | Docker image and a Raspberry Pi guide | Runs 24/7 without a Windows PC | M | Planned |

## Other ideas
| Idea | Why | Effort |
|---|---|---|
| Warn when the melee/ranged mix is lopsided | Optional composition hints | S |
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
5. Push the tag (`git tag v1.x.y && git push origin v1.x.y`). The Release workflow reruns the tests, builds the zip, scans it for
   secrets (`.env` values, tokens, IDs, personal paths, unlisted files) and creates a **draft** release. Check the notes, then publish.
