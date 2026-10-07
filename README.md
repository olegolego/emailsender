# EmailManager

A local cold-email program for supplier outreach. It runs on your Mac and:
- sends emails from your own mailbox in batches, with timed follow-ups;
- reads replies and has Claude summarise and sort them;
- stops a company's follow-ups when it answers, and notifies you.

All data stays in one SQLite file on your computer.

- **Batches**: a list of companies, one email style and one sending mailbox. You choose the days, hours and daily limit for each batch.
- **Email styles**: a first email, then "no reply after N days → follow-up". A reply always stops the sequence and notifies you.
- **One strategy per company**: an email address, or anyone else at the same company, can only ever be in one sequence.
- **Replies**: every incoming email gets a category, an interest score, a summary and a suggested answer. You answer from the same thread. People who write to you first become new conversations. Emails written in Russian, newsletters and automated mail are ignored. You can delete any conversation and optionally ignore that sender.
- **Companies**: shows where every supplier stands (ready, emailing, replied, form to fill or submitted).
- **Natural pacing**: a daily limit per mailbox, a random gap between emails (default 4 min ± 30 s), sending only inside each batch's days and hours, and a "Pause all" switch.

## Setup

```bash
./start.sh            # creates .venv, installs requirements, opens http://127.0.0.1:5055
./autostart.sh on     # optional: run in the background from login (LaunchAgent)
./autostart.sh off
./stop.sh
```

Requirements: macOS, Python 3.10+ and a Google Workspace or Gmail mailbox.

1. **Mailbox**:
   - Turn on 2-Step Verification for the Google account.
   - Create an app password at https://myaccount.google.com/apppasswords.
   - In the app, go to **Settings → Add mailbox** and paste it, then click **Test**.
2. **Claude**: paste an Anthropic API key in **Settings**. Without one, replies are sorted by keyword rules.
3. **Test mode** (Settings) sends every email to your own address until you switch it off.

Passwords and API keys are stored in the **macOS Keychain** (service `EmailManager`), not in the database or this repository.

## Importing supplier spreadsheets

```bash
.venv/bin/python -m app.importer /path/to/folder --dry-run   # preview
.venv/bin/python -m app.importer /path/to/folder --reset     # import: ready batches + forms workbook
.venv/bin/python -m app.styles [--refresh]                   # add / update the built-in email styles
```

The importer:
- merges the spreadsheets by website domain and drops duplicates (same email or same email domain);
- puts companies with a wholesale form on their site into a forms `.xlsx` to fill in by hand;
- splits everyone else into ready batches of about 100.

## Where data lives (never committed)

| What | Where |
|---|---|
| Contacts, companies, emails, replies, batches, settings | `data/emailmanager.db` |
| Backups, server log | `data/` |
| Mailbox app passwords, Anthropic key | macOS Keychain |

## Code

```
run.py             starts the web server (waitress) and the background worker
app/server.py      JSON API (only reachable from 127.0.0.1)
app/engine.py      queue, sending, pacing, inbound matching, reply routing
app/sequences.py   email styles (steps) and batch progress in plain words
app/styles.py      the built-in email styles
app/importer.py    spreadsheet import and the forms workbook
app/mailer.py      SMTP send, IMAP read (never marks mail as read), parsing and filters
app/analyzer.py    Claude reply analysis and drafting, with a keyword fallback
app/schedule.py    send windows, time zones, random delays
app/stats.py       metrics
app/worker.py      background loop (every 15 s; checks the inbox every few minutes)
static/            web interface (plain HTML/CSS/JS, no build step)
```
