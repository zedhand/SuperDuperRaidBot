# Running the bot 24/7 on a Raspberry Pi (Docker)

A Raspberry Pi is a small, quiet computer that uses about as much power as a phone charger, so it can stay on all the
time and your PC doesn't have to. This guide sets one up from scratch and moves your existing bot (settings, raids and
signups) over from Windows. You don't need to know Linux: every command is here to copy and paste. It takes about
30 minutes, most of it waiting for downloads.

The bot runs inside **Docker**, which packages it with exactly the Python it needs, starts it again after a crash, a
reboot or a power cut, and makes updating one command. Each release comes with a ready-made image for the Pi (and for
PCs), so the Pi only downloads it and never has to build the bot itself. The same steps work on any Linux machine with Docker, such as an
old laptop or a rented server (VPS): skip to [step 3](#step-3-install-docker).

> **Only run the bot in one place at a time.** Two copies with the same token answer every button twice and send every
> reminder twice. When the Pi is running, close the bot on Windows and switch off its auto-start (see
> [step 5](#step-5-move-your-raids-and-signups-over-from-windows)).

**Contents**

- [What you need](#what-you-need)
- [Step 1: Put Raspberry Pi OS on the card](#step-1-put-raspberry-pi-os-on-the-card)
- [Step 2: Connect to the Pi from your PC](#step-2-connect-to-the-pi-from-your-pc)
- [Step 3: Install Docker](#step-3-install-docker)
- [Step 4: Download the bot and add your settings](#step-4-download-the-bot-and-add-your-settings)
- [Step 5: Move your raids and signups over from Windows](#step-5-move-your-raids-and-signups-over-from-windows)
- [Step 6: Start it](#step-6-start-it)
- [Everyday commands](#everyday-commands)
- [Updating](#updating)
- [Building the image yourself](#building-the-image-yourself)
- [Backups](#backups)
- [Troubleshooting](#troubleshooting)

## What you need

- A **Raspberry Pi 3B+, 4, 5 or Zero 2 W** (any that can run the 64-bit system). A Pi 4 or 5 with 2 GB is plenty.
- Its **power supply**, and a **microSD card** of 16 GB or more (or a USB SSD, which lasts longer).
- An internet connection for the Pi. A network cable is the most reliable; Wi-Fi works too.
- Your Windows PC, to set the card up and to type commands into the Pi.

## Step 1: Put Raspberry Pi OS on the card

1. On your PC, install and open **Raspberry Pi Imager** from <https://www.raspberrypi.com/software/>.
2. **Choose device**: your Pi model. **Choose OS**: *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite (64-bit)**.
   (Lite has no desktop, which the bot doesn't need. It must be the **64-bit** one.) **Choose storage**: your card.
3. Click **Next**, then **Edit settings** when it offers OS customisation, and fill in:
   - **General**: hostname `raidbot`, a username and password you'll remember, your Wi-Fi (if you're not using a
     cable), and your time zone.
   - **Services**: tick **Enable SSH** and choose *Use password authentication*.
4. Save, click **Yes** to write the card, and wait for it to finish.
5. Put the card in the Pi, connect the network cable (if any) and then the power. Give it two or three minutes the
   first time.

## Step 2: Connect to the Pi from your PC

On Windows, open **Terminal** (or PowerShell) and type, using the username you picked in step 1:

```
ssh youruser@raidbot.local
```

Type `yes` the first time it asks about the fingerprint, then your password (nothing shows while you type it; that's
normal). When the prompt changes to something like `youruser@raidbot:~ $` you are typing on the Pi. Everything in the
grey boxes from here on is typed there, unless it says *on Windows*. To leave, type `exit`.

If `raidbot.local` isn't found, look up the Pi's address in your router's list of devices (for example `192.168.1.50`)
and use `ssh youruser@192.168.1.50` instead, here and later on.

## Step 3: Install Docker

Copy and paste these, one line at a time. The first one can take a few minutes.

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

The last line lets you use Docker without typing `sudo`. It only takes effect after you log in again, so type `exit`,
connect again with `ssh` as in step 2, and check it works:

```bash
docker run --rm hello-world
```

You should see *"Hello from Docker!"*.

## Step 4: Download the bot and add your settings

```bash
git clone https://github.com/zedhand/SuperDuperRaidBot.git ~/SuperDuperRaidBot
cd ~/SuperDuperRaidBot
```

The bot needs the same `.env` settings file it uses on Windows. Pick one:

**A. You already run the bot on Windows (the usual case):** copy your `.env` over. *On Windows*, open the bot's folder in
File Explorer, click the address bar, type `powershell` and press Enter. In the window that opens, type (with your
username):

```
scp .env youruser@raidbot.local:SuperDuperRaidBot/
```

**B. A brand-new bot:** run the setup wizard on the Pi. It asks the same questions as `SETUP.bat` and prints the Discord
web pages to open, so open those on your PC:

```bash
python3 setup.py
```

Or do it by hand: `cp .env.example .env`, then `nano .env`, fill in the values (see the README's *Settings* section),
and press `Ctrl+O`, Enter, `Ctrl+X` to save and close.

Your token stays in that `.env` file on the Pi. It is never put into the Docker image, and git ignores it.

## Step 5: Move your raids and signups over from Windows

Skip this for a brand-new bot.

1. *On Windows*, **close the bot's black window** so it stops and finishes writing its database. Then double-click
   **`Shortcuts.bat`** and switch off *start when Windows starts*, so it doesn't come back after a reboot.
2. In the PowerShell window from step 4 (still in the bot's folder), copy the database, and the daily backups if you
   want them, into the Pi's `data` folder:

   ```
   scp raidbot.db youruser@raidbot.local:SuperDuperRaidBot/data/
   scp -r backups youruser@raidbot.local:SuperDuperRaidBot/data/
   ```

That's all the bot keeps: characters, raids, signups and every `/config` setting are in `raidbot.db`. The class icons
were already uploaded to Discord as the bot's own emoji, so they keep working. (If you want the Pi to be able to upload
them again, copy the pictures from the `icons` folder into `SuperDuperRaidBot/icons/` on the Pi the same way.)

## Step 6: Start it

```bash
cd ~/SuperDuperRaidBot
./docker-update.sh
```

The first start downloads the bot's ready-made image (well under 100 MB), which takes a minute or so. Then watch it start:

```bash
docker compose logs -f
```

You're looking for `Token: OK`, your server's name and `Logged in as ...`. Press `Ctrl+C` to stop watching; the bot keeps
running. Check in Discord that it's online and that your raid posts' buttons work.

From now on it runs by itself, and starts again on its own after a crash, a reboot or a power cut. You can close the
SSH window and switch your PC off.

## Everyday commands

Type these in the bot's folder on the Pi (`cd ~/SuperDuperRaidBot` first).

| To... | Type |
|---|---|
| See if it's running | `docker compose ps` |
| Watch what it's doing (Ctrl+C to stop watching) | `docker compose logs -f` |
| Stop it | `docker compose down` |
| Start it again | `docker compose up -d` |
| Restart it | `docker compose restart` |
| Use new settings after editing `.env` | `docker compose up -d` (a plain *restart* doesn't re-read `.env`) |
| Update to the newest release | `./docker-update.sh` |

Errors are also written to `data/raidbot.log`, the same file as on Windows: `tail -n 50 data/raidbot.log` shows the end
of it. Most settings are still changed live in Discord with `/config` and `/alerts`, exactly as before.

## Updating

```bash
cd ~/SuperDuperRaidBot
./docker-update.sh
```

It asks GitHub for the newest published release, switches to it, downloads that version's ready-made image and restarts
the bot. Nothing is built on the Pi, so it only takes as long as the download. Your `.env` and the
`data` folder are never touched, and the database upgrades itself on the next start, just like `Update.bat`. With
`/config updatenotice` on, the bot tells you in the log channel when there's a new version.

## Building the image yourself

You don't normally need this: every release has a ready-made image at `ghcr.io/zedhand/superduperraidbot`. But if the
download doesn't work (no access to `ghcr.io`, or you changed the code yourself), the Pi can build the image from the
files in the bot's folder instead, as earlier versions did. It takes a few minutes on a Pi:

```bash
cd ~/SuperDuperRaidBot
./docker-update.sh --build
```

`./docker-update.sh` also does this by itself when the download fails. To build without updating to a new release:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

The image it builds gets the same name as the ready-made one, so all the everyday commands keep working. The next plain
`./docker-update.sh` goes back to the ready-made image.

## Backups

The bot still makes its daily copy of the database, now in `data/backups` on the Pi, and keeps the newest 7. To keep a
copy somewhere else as well:

- **In Discord:** `/config backupchannel` posts this server's data to a private channel every day.
- **On your PC:** *on Windows*, in PowerShell in any folder you like, run
  `scp -r youruser@raidbot.local:SuperDuperRaidBot/data/backups .`

**To restore a backup** on the Pi:

```bash
cd ~/SuperDuperRaidBot
docker compose down
ls data/backups                                  # pick a date
cp data/backups/raidbot-2026-01-31.db data/raidbot.db
docker compose up -d
```

**To move back to Windows** (or to another machine): `docker compose down` on the Pi, copy `data/raidbot.db` into the
bot's folder on Windows (`scp youruser@raidbot.local:SuperDuperRaidBot/data/raidbot.db .` in that folder), and start
it there with `start.bat`.

## Troubleshooting

- **`permission denied` ... `docker.sock`**: you haven't logged in again since `usermod` in step 3. Type `exit`, connect
  with `ssh` again, and retry.
- **`docker compose ps` says *Restarting***: the bot is stopping on a settings problem and Docker keeps trying again.
  `docker compose logs --tail 20` shows the reason in plain English (the same checks as on Windows: the token, the
  *Server Members Intent*, `GUILD_ID`...). Fix `.env` with `nano .env`, then run `docker compose up -d`.
- **`env file ... .env not found`**: there's no `.env` next to `docker-compose.yml`. Go back to
  [step 4](#step-4-download-the-bot-and-add-your-settings).
- **`unable to open database file`** or *permission denied* on `/data`: the bot runs as user number 1000 and can't
  write to the `data` folder. Fix it with `sudo chown -R 1000:1000 data`, then `docker compose up -d`.
- **`no matching manifest for linux/arm/v7`**, **`exec format error`** or the build fails on a Pi: it's running the
  32-bit system. The ready-made image is for 64-bit only. `uname -m` must say `aarch64`.
  Write the card again with **Raspberry Pi OS Lite (64-bit)**.
- **`docker compose pull` says *denied*, *unauthorized* or *manifest unknown***: the ready-made image for that version
  isn't available. `./docker-update.sh` builds it on the Pi instead, or run `./docker-update.sh --build` (see
  [Building the image yourself](#building-the-image-yourself)).
- **Buttons answer twice, or reminders arrive twice**: the bot is still running on Windows too. Close it there.
- **Raid times are off by some hours**: the bot uses `TIMEZONE` from `.env` (or `/config timezone`), not the Pi's
  clock. Check those.
- **Running it on Windows or a Mac with Docker Desktop** works with the same commands, but on Windows `start.bat` is the
  simpler choice.
