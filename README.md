# Padellen Booking Monitor

Monitors [The Padellers](https://thepadellers.bookaball.com/nl/bookings/create) for court availability and sends you a notification when a slot opens up. Can also auto-book the best slot and hold the reservation for you.

Uses undetected-chromedriver to get past the booking site's bot detection.

## What it does

- Checks for available courts every 10-20 minutes
- Sends notifications through pretty much any service (Telegram, Discord, ntfy, Slack, etc.) via [Apprise](https://github.com/caronc/apprise)
- Remembers which slots it already told you about so you don't get spammed
- Optionally auto-books the best available slot and keeps the reservation alive by renewing it every ~5 minutes
- Runs in Docker or locally

## Quick start

You need Python 3.12+, Chrome, and [uv](https://github.com/astral-sh/uv).

```bash
git clone <repository-url>
cd padellen-signup
uv sync
```

Copy the example config and fill in your details:

```bash
cp config.example.json config.json
```

Then run it:

```bash
python main.py
```

Or with Docker Compose:

```bash
docker-compose up -d
```

## Configuration

Settings are loaded in this order (first one wins):

1. CLI arguments
2. Environment variables (or `.env` file)
3. `config.json`
4. Built-in defaults

### config.json

```json
{
  "apprise_urls": ["ntfys://token@ntfy.example.com/padellen"],
  "target_date": "2026-02-10",
  "time_range_start": "18:00",
  "time_range_end": "22:00",
  "duration_minutes": 60,
  "priority_times": ["18:00", "19:00", "20:00"],
  "headless": true
}
```

See `config.example.json` for all available options.

### Environment variables

Same options, just uppercased with underscores. See `.env.example` for the full list.

```env
APPRISE_URLS=ntfys://token@ntfy.example.com/padellen
TARGET_DATE=2026-02-10
TIME_RANGE_START=18:00
TIME_RANGE_END=22:00
```

### CLI arguments

```bash
python main.py \
  --target-date "2026-02-10" \
  --time-range-start "18:00" \
  --time-range-end "22:00" \
  --duration 60 \
  --no-headless
```

Run `python main.py --help` to see everything.

## Auto-booking

When `--auto-book` is enabled, the monitor will automatically book the best available slot (based on your `priority_times`) and then keep the reservation alive indefinitely by renewing it every ~4 minutes 50 seconds -- just under the site's 5-minute timeout.

You need to provide your Padellen account credentials:

```bash
python main.py --auto-book --booking-email you@example.com --booking-password yourpass --no-headless
```

Or in `config.json`:

```json
{
  "auto_book": true,
  "booking_email": "you@example.com",
  "booking_password": "yourpass"
}
```

The keepalive works by cancelling and immediately re-booking the slot in a loop. If something goes wrong, it does a full page re-navigation and tries again.

Press Ctrl+C to stop and release the reservation.

## Docker

### With Docker Compose (recommended)

```bash
docker-compose up -d
docker-compose logs -f
```

### With Docker directly

```bash
docker build -t padellen-monitor .
docker run -d \
  -v $(pwd)/config.json:/app/config.json:ro \
  --name padellen-monitor \
  padellen-monitor
```

A prebuilt image is also published to GitHub Container Registry on every push to `main`. You can pull it with:

```bash
docker pull ghcr.io/<owner>/padellen-signup:latest
```

## Notifications

Apprise supports 80+ notification services. Some common ones:

| Service  | URL format                                    |
|----------|-----------------------------------------------|
| ntfy.sh  | `ntfy://ntfy.sh/your-topic`                   |
| ntfy (self-hosted) | `ntfys://token@ntfy.example.com/topic` |
| Telegram | `tgram://bottoken/ChatID`                     |
| Discord  | `discord://webhook_id/webhook_token`          |
| Slack    | `slack://botname@token-a/token-b/token-c`     |

You can use multiple services by comma-separating the URLs or adding multiple entries to the `apprise_urls` list in your config.

See the [Apprise docs](https://github.com/caronc/apprise) for the full list.

## Troubleshooting

**Chrome not found** -- Make sure Chrome or Chromium is installed. On Docker this is handled automatically.

**No notifications** -- Test your Apprise URL directly:
```bash
python -c "from apprise import Apprise; a = Apprise(); a.add('YOUR_URL'); a.notify(body='Test')"
```

**Slots not detected** -- Check that your target date actually has slots on the booking site. Try running with `--no-headless` to watch what the browser is doing.

**Stale state** -- Delete `availability_state.json` to reset. All current slots will be treated as new on the next run.

## Project structure

```
config.py    -- Configuration loading (CLI, env, config file)
scraper.py   -- Browser automation with undetected-chromedriver
state.py     -- Tracks seen slots to avoid duplicate notifications
notifier.py  -- Sends notifications via Apprise
main.py      -- Main monitoring loop and auto-booking logic
```

## License

Provided as-is for personal use.
