This folder is the bot's data when it runs in Docker (see DOCKER.md).

raidbot.db   characters, raids and signups
backups/     the daily copies of raidbot.db (the newest 7 are kept)
raidbot.log  errors and events

Everything here except this file is private and is never committed to git.
To move the bot from Windows, stop it there and copy its raidbot.db into this folder.
