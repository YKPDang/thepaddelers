import logging
import time
import random
import sys
from datetime import datetime

from config import Config, load_config
from scraper import PadellenScraper, DateNotBookableError
from state import StateTracker
from notifier import NotificationService

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

KEEPALIVE_INTERVAL = 240  # 4 minutes


def _format_priority_info(ranking: dict) -> str | None:
    """Format priority ranking info for notification display."""
    distance = ranking.get("best_match_distance_minutes")
    closest_to = ranking.get("best_match_closest_to")
    if distance is None or distance < 0:
        return None
    if distance == 0:
        return "(Perfect match!)"
    return f"({distance}min from {closest_to})"


def _select_best_slot(available_slots: list[str], priority_times: list[str]) -> str:
    """Pick the best slot from available slots using priority ranking."""
    if not available_slots:
        raise ValueError("No available slots to select from")
    ranking = StateTracker.rank_slots_by_priority(available_slots, priority_times)
    return ranking["best_match"]


def _select_alternate_slot(available_slots: list[str], current_slot: str) -> str | None:
    """Pick any slot other than the current one for keepalive cycling."""
    others = [s for s in available_slots if s != current_slot]
    return others[0] if others else None


def _navigate_and_get_slots(scraper: PadellenScraper, config: Config) -> list[str]:
    """Full navigation sequence: booking page -> location -> duration -> date -> get slots."""
    time_range = (config.time_range_start, config.time_range_end)
    scraper.navigate_to_booking()
    scraper.select_location()
    scraper.select_duration(config.duration_minutes)
    scraper.select_date(config.target_date)
    return scraper.get_available_slots(time_range)


def _run_auto_booking(
    scraper: PadellenScraper,
    config: Config,
    best_slot: str,
    available_slots: list[str],
    notifier: NotificationService,
) -> None:
    """
    Book the best slot and enter keepalive loop.

    The keepalive cycle renews the reservation every ~4m50s by:
    1. Cancelling the current reservation
    2. Optionally cycling through an alternate slot
    3. Re-booking the preferred slot
    """
    logger.info(f"=== AUTO-BOOKING: Attempting to book slot {best_slot} ===")

    # Initial booking
    scraper.click_slot(best_slot)
    scraper.login(config.booking_email, config.booking_password)
    scraper.confirm_booking()

    logger.info(f"Slot {best_slot} booked successfully!")
    notifier.send_reservation_notification(best_slot, config.target_date)

    # Keepalive loop
    alternate_slot = _select_alternate_slot(available_slots, best_slot)
    cycle_count = 0

    while True:
        try:
            logger.info(
                f"Keepalive: sleeping {KEEPALIVE_INTERVAL}s before renewal "
                f"(cycle #{cycle_count + 1})"
            )
            time.sleep(KEEPALIVE_INTERVAL)

            logger.info("Keepalive: renewing reservation...")

            # Cancel current reservation
            scraper.cancel_reservation()
            time.sleep(1)

            # Cycle through alternate slot if available
            if alternate_slot:
                logger.info(
                    f"Keepalive: cycling through alternate slot {alternate_slot}"
                )
                scraper.click_slot(alternate_slot)
                scraper.confirm_booking()
                time.sleep(1)
                scraper.cancel_reservation()
                time.sleep(1)

            # Re-book preferred slot
            scraper.click_slot(best_slot)
            scraper.confirm_booking()

            cycle_count += 1
            logger.info(f"Keepalive: reservation renewed (cycle #{cycle_count})")

        except KeyboardInterrupt:
            logger.info("Keepalive interrupted by user")
            raise
        except Exception as e:
            logger.error(f"Keepalive cycle failed: {e}")
            logger.info("Attempting full re-navigation recovery...")
            try:
                available_slots = _navigate_and_get_slots(scraper, config)
                if not available_slots:
                    logger.error(
                        "No slots available after recovery, retrying in 30s..."
                    )
                    time.sleep(30)
                    continue

                best_slot = _select_best_slot(
                    available_slots, config.priority_times or []
                )
                alternate_slot = _select_alternate_slot(available_slots, best_slot)

                scraper.click_slot(best_slot)
                scraper.login(config.booking_email, config.booking_password)
                scraper.confirm_booking()

                logger.info(f"Recovery successful, re-booked slot {best_slot}")
            except Exception as recovery_error:
                logger.error(f"Recovery failed: {recovery_error}")
                logger.info("Retrying recovery in 30s...")
                time.sleep(30)


def main():
    """Run the main monitoring loop."""
    config = None
    scraper = None
    state_tracker = None
    notifier = None

    try:
        # Load configuration
        logger.info("Loading configuration...")
        config = load_config()
        logger.info(
            f"Configuration loaded: date={config.target_date}, "
            f"time_range={config.time_range_start}-{config.time_range_end}, "
            f"duration={config.duration_minutes}min"
        )
        if config.priority_times:
            logger.info(f"Priority times: {', '.join(config.priority_times)}")
        if config.auto_book:
            logger.info("Auto-booking mode ENABLED")

        # Initialize components
        scraper = PadellenScraper(
            headless=config.headless,
            location_id=config.location_id,
            wait_timeout=config.wait_timeout,
            chrome_path=config.chrome_path,
            chrome_version=config.chrome_version,
        )
        state_tracker = StateTracker(config.state_file)
        notifier = NotificationService(
            config.apprise_urls,
            discord_bot_token=config.discord_bot_token,
            discord_user_ids=config.discord_user_ids,
        )

        time_range = (config.time_range_start, config.time_range_end)

        # Main monitoring loop
        while True:
            try:
                check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                logger.info(f"=== Checking availability at {check_time} ===")

                # Navigate and scrape with retry logic
                available_slots = None
                for attempt in range(config.max_retries):
                    try:
                        scraper.navigate_to_booking()
                        scraper.select_location()
                        scraper.select_duration(config.duration_minutes)
                        scraper.select_date(config.target_date)
                        available_slots = scraper.get_available_slots(time_range)
                        break  # Success, exit retry loop
                    except DateNotBookableError as e:
                        # Date isn't open for booking yet — not an error. Treat
                        # as "no slots" and just wait for the next poll instead
                        # of retrying or backing off.
                        logger.info(f"{e}. Waiting until next check.")
                        available_slots = []
                        break
                    except Exception as e:
                        logger.warning(
                            f"Attempt {attempt + 1}/{config.max_retries} failed: {e}"
                        )
                        if attempt < config.max_retries - 1:
                            wait_time = config.retry_backoff * (2**attempt)
                            logger.info(f"Retrying in {wait_time} seconds...")
                            time.sleep(wait_time)
                        else:
                            raise

                assert available_slots is not None  # guaranteed by raise on final retry

                # Update state and detect new slots
                new_slots = state_tracker.update_and_get_new(
                    available_slots, config.target_date
                )

                # Send notification if new slots found
                if new_slots:
                    logger.info(f"Found {len(new_slots)} new slots: {new_slots}")

                    best_match = None
                    priority_info = None
                    if config.priority_times:
                        ranking = StateTracker.rank_slots_by_priority(
                            new_slots, config.priority_times
                        )
                        best_match = ranking.get("best_match")
                        priority_info = _format_priority_info(ranking)
                        logger.info(f"Best match: {best_match} {priority_info or ''}")

                    notifier.send_notification(
                        new_slots,
                        config.target_date,
                        best_match=best_match,
                        priority_info=priority_info,
                    )

                # Auto-book if enabled and slots are available
                if config.auto_book and available_slots:
                    best_slot = _select_best_slot(
                        available_slots, config.priority_times or []
                    )
                    logger.info(
                        f"Auto-book: selected best slot {best_slot}, "
                        f"entering booking + keepalive mode"
                    )
                    _run_auto_booking(
                        scraper, config, best_slot, available_slots, notifier
                    )
                    # _run_auto_booking only returns on KeyboardInterrupt (re-raised)
                    break

                if not new_slots:
                    logger.info("No new slots since last check")

                # Log next check time
                poll_interval = random.randint(
                    config.min_poll_interval, config.max_poll_interval
                )
                next_check = datetime.fromtimestamp(
                    time.time() + poll_interval
                ).strftime("%Y-%m-%d %H:%M:%S")
                logger.info(f"Next check in {poll_interval} seconds at {next_check}")

                # Sleep
                time.sleep(poll_interval)

            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received, shutting down...")
                break
            except Exception as e:
                logger.error(f"Unexpected error in monitoring loop: {e}")
                logger.info("Sleeping 30 seconds before retry...")
                time.sleep(30)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received during startup")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        # Cleanup
        if scraper:
            scraper.cleanup()
        logger.info("Application shut down")


if __name__ == "__main__":
    main()
