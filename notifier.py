import logging
import traceback
from apprise import Apprise

logger = logging.getLogger(__name__)


class NotificationService:
    """Handle notifications via Apprise."""

    BOOKING_URL = "https://thepadellers.bookaball.com/nl/bookings/create"

    def __init__(self, apprise_urls: list[str]):
        """
        Initialize notification service.

        Args:
            apprise_urls: List of Apprise service URLs
        """
        self.apprise = Apprise()

        for url in apprise_urls:
            try:
                result = self.apprise.add(url)
                if result:
                    logger.info(f"Added Apprise service: {url}")
                else:
                    logger.warning(
                        f"Failed to add Apprise service: {url} - Invalid URL format"
                    )
            except Exception as e:
                logger.error(f"Error adding Apprise service {url}: {e}")

        if not len(self.apprise):
            logger.warning("No valid Apprise services configured")

    def send_notification(
        self,
        new_slots: list[str],
        date: str,
        best_match: str | None = None,
        priority_info: str | None = None,
    ) -> bool:
        """
        Send notification about new available slots.

        Args:
            new_slots: List of newly available slot times
            date: Target date
            best_match: Best matching slot based on priorities (optional)
            priority_info: Information about priority matching (optional)

        Returns:
            True if notification sent successfully, False otherwise
        """
        if not new_slots:
            logger.debug("No new slots to notify about")
            return True

        if not len(self.apprise):
            logger.error(
                "Cannot send notification: no Apprise services configured successfully"
            )
            return False

        # Format slots for display
        slots_str = ", ".join(new_slots)

        # Create notification message
        title = "🎾 New Padellen Slots Available"

        if best_match:
            body = f"⭐ BEST MATCH: {best_match}"
            if priority_info:
                body += f" {priority_info}"
            body += (
                f"\n\nAll available slots for {date}:\n"
                f"{slots_str}\n\n"
                f"Book here: {self.BOOKING_URL}"
            )
        else:
            body = (
                f"New slots available for {date}:\n"
                f"{slots_str}\n\n"
                f"Book here: {self.BOOKING_URL}"
            )

        try:
            logger.info(
                f"Sending notification for {len(new_slots)} new slots to {len(self.apprise)} service(s)"
            )
            logger.debug(f"Notification title: {title}")
            logger.debug(f"Notification body: {body}")

            result = self.apprise.notify(
                body=body,
                title=title,
            )

            if result:
                logger.info(f"Notification sent successfully for slots: {slots_str}")
                return True
            else:
                logger.error("Failed to send notification - Apprise returned False")
                return False

        except Exception as e:
            logger.error(f"Exception while sending notification: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    def send_reservation_notification(self, slot_time: str, date: str) -> bool:
        """
        Send notification that a slot has been reserved and keepalive is active.

        Args:
            slot_time: The reserved slot time in HH:MM format
            date: Target date

        Returns:
            True if notification sent successfully, False otherwise
        """
        if not len(self.apprise):
            logger.error(
                "Cannot send notification: no Apprise services configured successfully"
            )
            return False

        title = "🎾 Slot Reserved!"
        body = (
            f"Auto-booked slot {slot_time} on {date}\n\n"
            f"Keepalive is active — reservation will be renewed every ~4m50s.\n"
            f"Press Ctrl+C to stop and release the reservation."
        )

        try:
            logger.info(f"Sending reservation notification for {slot_time}")
            result = self.apprise.notify(body=body, title=title)
            if result:
                logger.info("Reservation notification sent successfully")
                return True
            else:
                logger.error("Failed to send reservation notification")
                return False
        except Exception as e:
            logger.error(f"Exception while sending reservation notification: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
