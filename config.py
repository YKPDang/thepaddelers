import os
import json
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from dotenv import load_dotenv


@dataclass
class Config:
    """Configuration container for the Padellen monitoring system."""

    apprise_urls: list[str]
    target_date: str
    time_range_start: str
    time_range_end: str
    # Discord bot DM notifications (optional)
    discord_bot_token: str = ""
    discord_user_ids: list[str] | None = None
    location_id: str = "bookings-locations-15-name"  # Padellen location element ID
    duration_minutes: int = 60  # Booking duration (60, 90, or 120)
    priority_times: list[str] | None = None  # Preferred booking times in order
    wait_timeout: int = 10  # Element wait timeout in seconds
    state_file: str = "config/availability_state.json"  # Path to persisted state
    # Polling and retries
    min_poll_interval: int = 600  # 10 minutes
    max_poll_interval: int = 1200  # 20 minutes
    max_retries: int = 3
    retry_backoff: int = 2  # seconds
    # Chrome
    chrome_path: str | None = None  # Path to Chrome executable
    chrome_version: int | None = None  # Pin driver major version (None = auto-detect)
    headless: bool = True  # Run in headless mode
    # Auto-booking
    auto_book: bool = False
    booking_email: str = ""
    booking_password: str = ""

    def __post_init__(self):
        if self.priority_times is None:
            self.priority_times = []
        if self.discord_user_ids is None:
            self.discord_user_ids = []

    def validate(self) -> None:
        """Validate configuration values."""
        if not self.apprise_urls:
            raise ValueError("APPRISE_URLS is required")
        if not self.target_date:
            raise ValueError("TARGET_DATE is required")
        if not self.time_range_start:
            raise ValueError("TIME_RANGE_START is required")
        if not self.time_range_end:
            raise ValueError("TIME_RANGE_END is required")

        # Validate auto-book credentials
        if self.auto_book:
            if not self.booking_email or not self.booking_password:
                raise ValueError(
                    "BOOKING_EMAIL and BOOKING_PASSWORD are required when --auto-book is enabled"
                )

        # Validate duration
        if self.duration_minutes not in [60, 90, 120]:
            raise ValueError(
                f"DURATION_MINUTES must be 60, 90, or 120, got {self.duration_minutes}"
            )

        # Validate time format (HH:MM)
        for time_str in [self.time_range_start, self.time_range_end]:
            if not time_str:
                continue
            parts = time_str.split(":")
            if len(parts) != 2:
                raise ValueError(f"Invalid time format: {time_str}. Use HH:MM")
            try:
                hour = int(parts[0])
                minute = int(parts[1])
                if not (0 <= hour < 24 and 0 <= minute < 60):
                    raise ValueError(f"Invalid time range: {time_str}")
            except ValueError:
                raise ValueError(f"Invalid time format: {time_str}")


def _resolve(
    cli_val: Any,
    env_key: str,
    config_data: dict[str, Any],
    config_key: str,
    default: Any = None,
    type_fn: type | None = None,
) -> Any:
    """Resolve config value from CLI arg, env var, or config file (in priority order)."""
    val = cli_val
    if val is None:
        env_val = os.getenv(env_key)
        if env_val is not None:
            val = type_fn(env_val) if type_fn else env_val
    if val is None:
        val = config_data.get(config_key, default)
    return val


def load_config() -> Config:
    """Load configuration from config file, env vars, and CLI arguments."""

    # Load .env file if it exists
    load_dotenv()

    # Parse CLI arguments
    parser = argparse.ArgumentParser(
        description="Padellen court availability monitoring system"
    )
    parser.add_argument(
        "--config",
        help="Path to config file (JSON format)",
        default=os.getenv("CONFIG_FILE", "config/config.json"),
    )
    parser.add_argument(
        "--state-file",
        help="Path to the state file (default: alongside the config file)",
        default=None,
    )
    parser.add_argument(
        "--apprise-urls", help="Comma-separated Apprise notification URLs", default=None
    )
    parser.add_argument(
        "--target-date", help="Target date in ISO format (YYYY-MM-DD)", default=None
    )
    parser.add_argument(
        "--time-range-start", help="Start time in HH:MM format", default=None
    )
    parser.add_argument(
        "--time-range-end", help="End time in HH:MM format", default=None
    )
    parser.add_argument(
        "--location-id",
        help="Padellen location element ID (default: bookings-locations-15-name)",
        default=None,
    )
    parser.add_argument(
        "--duration",
        type=int,
        help="Booking duration in minutes (60, 90, or 120)",
        default=None,
    )
    parser.add_argument(
        "--priority-times",
        help="Comma-separated list of preferred times (e.g., '18:00,19:00,20:00')",
        default=None,
    )
    parser.add_argument(
        "--discord-bot-token",
        help="Discord bot token for sending DM notifications",
        default=None,
    )
    parser.add_argument(
        "--discord-user-ids",
        help="Comma-separated Discord user IDs to DM (e.g., '123,456')",
        default=None,
    )
    parser.add_argument(
        "--min-poll-interval",
        type=int,
        help="Minimum poll interval in seconds (default: 600)",
        default=None,
    )
    parser.add_argument(
        "--max-poll-interval",
        type=int,
        help="Maximum poll interval in seconds (default: 1200)",
        default=None,
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        help="Maximum retry attempts (default: 3)",
        default=None,
    )
    parser.add_argument(
        "--retry-backoff",
        type=int,
        help="Retry backoff in seconds (default: 2)",
        default=None,
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        help="Element wait timeout in seconds (default: 10)",
        default=None,
    )
    parser.add_argument(
        "--chrome-path",
        help="Path to Chrome executable (usually not needed)",
        default=None,
    )
    parser.add_argument(
        "--chrome-version",
        type=int,
        help="Pin Chrome driver major version (default: auto-detect)",
        default=None,
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome in headless mode",
        default=None,
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run Chrome with UI visible",
        default=None,
    )
    parser.add_argument(
        "--auto-book",
        action="store_true",
        help="Automatically book the best available slot and keep the reservation alive",
        default=None,
    )
    parser.add_argument(
        "--booking-email",
        help="Email address for Padellen account login",
        default=None,
    )
    parser.add_argument(
        "--booking-password",
        help="Password for Padellen account login",
        default=None,
    )

    args = parser.parse_args()

    # Load config from file if it exists
    config_data = {}
    config_file = Path(args.config)
    if config_file.exists():
        try:
            with open(config_file, "r") as f:
                config_data = json.load(f)
            print(f"Loaded config from {config_file}")
        except Exception as e:
            print(f"Warning: Failed to load config file {config_file}: {e}")

    # Resolve values: CLI args > env vars > config file > defaults
    apprise_urls_str = _resolve(
        args.apprise_urls, "APPRISE_URLS", config_data, "apprise_urls", ""
    )
    if isinstance(apprise_urls_str, list):
        apprise_urls_str = ",".join(apprise_urls_str)
    apprise_urls = [url.strip() for url in apprise_urls_str.split(",") if url.strip()]

    target_date = _resolve(
        args.target_date, "TARGET_DATE", config_data, "target_date", ""
    )
    time_range_start = _resolve(
        args.time_range_start, "TIME_RANGE_START", config_data, "time_range_start", ""
    )
    time_range_end = _resolve(
        args.time_range_end, "TIME_RANGE_END", config_data, "time_range_end", ""
    )
    location_id = _resolve(
        args.location_id,
        "LOCATION_ID",
        config_data,
        "location_id",
        "bookings-locations-15-name",
    )
    duration_minutes = _resolve(
        args.duration, "DURATION_MINUTES", config_data, "duration_minutes", 60, int
    )
    min_poll_interval = _resolve(
        args.min_poll_interval,
        "MIN_POLL_INTERVAL",
        config_data,
        "min_poll_interval",
        600,
        int,
    )
    max_poll_interval = _resolve(
        args.max_poll_interval,
        "MAX_POLL_INTERVAL",
        config_data,
        "max_poll_interval",
        1200,
        int,
    )
    max_retries = _resolve(
        args.max_retries, "MAX_RETRIES", config_data, "max_retries", 3, int
    )
    retry_backoff = _resolve(
        args.retry_backoff, "RETRY_BACKOFF", config_data, "retry_backoff", 2, int
    )
    wait_timeout = _resolve(
        args.wait_timeout, "WAIT_TIMEOUT", config_data, "wait_timeout", 10, int
    )
    chrome_path = _resolve(args.chrome_path, "CHROME_PATH", config_data, "chrome_path")
    chrome_version = _resolve(
        args.chrome_version, "CHROME_VERSION", config_data, "chrome_version", None, int
    )

    # State file: default to living next to the config file so a single mounted
    # folder holds both config and persisted state.
    state_file = _resolve(args.state_file, "STATE_FILE", config_data, "state_file")
    if not state_file:
        state_file = str(config_file.parent / "availability_state.json")

    # Priority times: can be comma-separated string or list
    priority_times_raw = _resolve(
        args.priority_times, "PRIORITY_TIMES", config_data, "priority_times", []
    )
    if isinstance(priority_times_raw, str):
        priority_times = [t.strip() for t in priority_times_raw.split(",") if t.strip()]
    else:
        priority_times = priority_times_raw or []

    # Discord bot DM notifications
    discord_bot_token = _resolve(
        args.discord_bot_token, "DISCORD_BOT_TOKEN", config_data, "discord_bot_token", ""
    )
    # User IDs: can be comma-separated string or list
    discord_user_ids_raw = _resolve(
        args.discord_user_ids, "DISCORD_USER_IDS", config_data, "discord_user_ids", []
    )
    if isinstance(discord_user_ids_raw, str):
        discord_user_ids = [
            uid.strip() for uid in discord_user_ids_raw.split(",") if uid.strip()
        ]
    else:
        discord_user_ids = [str(uid) for uid in (discord_user_ids_raw or [])]

    # Determine headless mode
    headless = config_data.get("headless", True)
    if args.no_headless:
        headless = False
    elif args.headless:
        headless = True
    elif os.getenv("HEADLESS"):
        headless = os.getenv("HEADLESS", "true").lower() != "false"

    # Determine auto_book mode
    auto_book = config_data.get("auto_book", False)
    if args.auto_book:
        auto_book = True
    elif os.getenv("AUTO_BOOK"):
        auto_book = os.getenv("AUTO_BOOK", "false").lower() in ("true", "1", "yes")

    booking_email = _resolve(
        args.booking_email, "BOOKING_EMAIL", config_data, "booking_email"
    )
    booking_password = _resolve(
        args.booking_password, "BOOKING_PASSWORD", config_data, "booking_password"
    )

    config = Config(
        apprise_urls=apprise_urls,
        target_date=target_date,
        time_range_start=time_range_start,
        time_range_end=time_range_end,
        discord_bot_token=discord_bot_token,
        discord_user_ids=discord_user_ids,
        location_id=location_id,
        duration_minutes=duration_minutes,
        priority_times=priority_times,
        wait_timeout=wait_timeout,
        state_file=state_file,
        min_poll_interval=min_poll_interval,
        max_poll_interval=max_poll_interval,
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        chrome_path=chrome_path,
        chrome_version=chrome_version,
        headless=headless,
        auto_book=auto_book,
        booking_email=booking_email,
        booking_password=booking_password,
    )

    config.validate()
    return config
