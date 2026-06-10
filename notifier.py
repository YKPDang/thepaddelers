import logging
import traceback

import requests
from apprise import Apprise

logger = logging.getLogger(__name__)


class DiscordBotNotifier:
    """Send direct messages to specific Discord users via a bot token.

    Apprise's Discord support only posts to channels via webhooks, so to DM
    individual users we use the Discord REST API directly: open (or reuse) a DM
    channel per recipient, then post the message to it.

    Requirements:
        - The bot must share at least one server with each recipient.
        - The recipient must allow DMs from server members.
    """

    API_BASE = "https://discord.com/api/v10"

    def __init__(self, bot_token: str, user_ids: list[str]):
        self.bot_token = bot_token
        self.user_ids = user_ids
        self.headers = {
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
        }
        # Cache user_id -> DM channel_id so we don't re-open channels each send.
        self._dm_channels: dict[str, str] = {}

    def _get_dm_channel(self, user_id: str) -> str | None:
        """Open (or reuse) a DM channel with the given user, returning its ID."""
        if user_id in self._dm_channels:
            return self._dm_channels[user_id]
        try:
            resp = requests.post(
                f"{self.API_BASE}/users/@me/channels",
                headers=self.headers,
                json={"recipient_id": user_id},
                timeout=15,
            )
            resp.raise_for_status()
            channel_id = resp.json()["id"]
            self._dm_channels[user_id] = channel_id
            return channel_id
        except Exception as e:
            logger.error(f"Discord: failed to open DM channel for user {user_id}: {e}")
            return None

    def send(self, title: str, body: str) -> bool:
        """DM every configured recipient. Returns True if at least one succeeded."""
        if not self.user_ids:
            logger.warning("Discord: no recipient user IDs configured")
            return False

        content = f"**{title}**\n\n{body}" if title else body
        content = content[:2000]  # Discord hard-limits messages to 2000 chars

        sent_any = False
        for user_id in self.user_ids:
            channel_id = self._get_dm_channel(user_id)
            if not channel_id:
                continue
            try:
                resp = requests.post(
                    f"{self.API_BASE}/channels/{channel_id}/messages",
                    headers=self.headers,
                    json={"content": content},
                    timeout=15,
                )
                resp.raise_for_status()
                sent_any = True
                logger.info(f"Discord DM sent to user {user_id}")
            except Exception as e:
                logger.error(f"Discord: failed to DM user {user_id}: {e}")
        return sent_any


class NotificationService:
    """Handle notifications via Apprise and (optionally) Discord bot DMs."""

    BOOKING_URL = "https://thepadellers.bookaball.com/nl/bookings/create"

    def __init__(
        self,
        apprise_urls: list[str],
        discord_bot_token: str = "",
        discord_user_ids: list[str] | None = None,
    ):
        """
        Initialize notification service.

        Args:
            apprise_urls: List of Apprise service URLs
            discord_bot_token: Discord bot token for sending DMs (optional)
            discord_user_ids: Discord user IDs to DM (optional)
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

        # Optional Discord bot DM notifier
        self.discord: DiscordBotNotifier | None = None
        discord_user_ids = discord_user_ids or []
        if discord_bot_token and discord_user_ids:
            self.discord = DiscordBotNotifier(discord_bot_token, discord_user_ids)
            logger.info(
                f"Discord bot DM notifier enabled for {len(discord_user_ids)} recipient(s)"
            )
        elif discord_bot_token or discord_user_ids:
            logger.warning(
                "Discord notifier needs both a bot token and user IDs; skipping"
            )

    def _dispatch(self, title: str, body: str) -> bool:
        """Send a message through all configured channels.

        Returns True if at least one channel delivered successfully.
        """
        if not len(self.apprise) and not self.discord:
            logger.error("Cannot send notification: no channels configured")
            return False

        sent_any = False

        if len(self.apprise):
            try:
                if self.apprise.notify(body=body, title=title):
                    sent_any = True
                    logger.info("Apprise notification sent")
                else:
                    logger.error("Failed to send notification - Apprise returned False")
            except Exception as e:
                logger.error(f"Exception while sending Apprise notification: {e}")
                logger.debug(f"Traceback: {traceback.format_exc()}")

        if self.discord:
            if self.discord.send(title, body):
                sent_any = True
            else:
                logger.error("Failed to send Discord DM(s)")

        return sent_any

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

        logger.info(f"Sending notification for {len(new_slots)} new slots")
        logger.debug(f"Notification title: {title}")
        logger.debug(f"Notification body: {body}")

        result = self._dispatch(title, body)
        if result:
            logger.info(f"Notification sent successfully for slots: {slots_str}")
        return result

    def send_reservation_notification(self, slot_time: str, date: str) -> bool:
        """
        Send notification that a slot has been reserved and keepalive is active.

        Args:
            slot_time: The reserved slot time in HH:MM format
            date: Target date

        Returns:
            True if notification sent successfully, False otherwise
        """
        title = "🎾 Slot Reserved!"
        body = (
            f"Auto-booked slot {slot_time} on {date}\n\n"
            f"Keepalive is active — reservation will be renewed every ~4m50s.\n"
            f"Press Ctrl+C to stop and release the reservation."
        )

        logger.info(f"Sending reservation notification for {slot_time}")
        result = self._dispatch(title, body)
        if result:
            logger.info("Reservation notification sent successfully")
        return result
