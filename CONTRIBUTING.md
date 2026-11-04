# Contributing

Thanks for helping! This is a small, friendly project and every improvement is welcome: bug reports, fixes,
new features, docs, translations of the setup text.

## Quick start

```bash
git clone https://github.com/zedhand/SuperDuperRaidBot.git
cd SuperDuperRaidBot
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
python tests/run_all.py                       # should end with: ALL SUITES PASSED
```

The tests run **offline** against a faked Discord, so you don't need a bot token to contribute. To try the bot for real,
make a throwaway Discord server and a test bot, then run `python setup.py` and `python bot.py`.

## Ground rules

- **Never commit secrets.** `.env`, `*.db` and `backups/` are git-ignored; keep it that way. If you ever paste a token
  anywhere public, reset it immediately in the Discord Developer Portal.
- **Don't add Blizzard artwork** (class icons, game screenshots) to the repo. Users supply their own icons.
- **Keep setup effortless.** A new feature should work with no extra configuration; optional settings need a sensible default.
- **Older databases must keep working.** Schema changes go in `MIGRATIONS` in `db.py` as `ALTER TABLE ... ADD COLUMN` with a default.
- **Add tests** for new behavior. Look at `tests/test_v18.py` for how Discord objects are faked.

## Working with the setup scripts

`SETUP.bat` normally deletes itself when it finishes. Inside a git clone it detects the `.git` folder and keeps itself
(and `setup.py`), so your working tree stays clean.

## Pull requests

Small, focused PRs are easiest to review. Describe what changed and why, and mention how you tested it.
By contributing you agree that your contribution is released under the project's [MIT license](LICENSE).
