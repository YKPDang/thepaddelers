import logging
import time
import random
import sys
from datetime import datetime

from config import load_config
from scraper import PadellenScraper
from state import StateTracker
from notifier import NotificationService

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def _format_priority_info(ranking: dict) -> str | None:
    """Format priority ranking info for notification display."""
    distance = ranking.get("best_match_distance_minutes")
    closest_to = ranking.get("best_match_closest_to")
    if distance is None or distance < 0:
        return None
    if distance == 0:
        return "(Perfect match!)"
    return f"({distance}min from {closest_to})"


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

        # Initialize components
        scraper = PadellenScraper(
            headless=config.headless,
            location_id=config.location_id,
            wait_timeout=config.wait_timeout,
            chrome_path=config.chrome_path,
        )
        state_tracker = StateTracker()
        notifier = NotificationService(config.apprise_urls)

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
                else:
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
