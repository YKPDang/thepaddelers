import logging
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
import undetected_chromedriver as uc

logger = logging.getLogger(__name__)


class PadellenScraper:
    """Web scraper for Padellen court booking availability."""

    BOOKING_URL = "https://thepadellers.bookaball.com/nl/bookings/create"
    SLOTS_CONTAINER_ID = "bookings-date-step-times"

    # Login form element IDs
    LOGIN_EMAIL_ID = "login-step-auth-login-form-text-input-email-input"
    LOGIN_PASSWORD_ID = "login-step-auth-login-form-text-input-password-input"
    LOGIN_REMEMBER_ID = "login-step-auth-login-form-checkbox-input-remember-input"
    LOGIN_SUBMIT_ID = "login-step-auth-login-form-login-button-submit-button"

    # Booking summary popup element IDs
    SUMMARY_CONTINUE_ID = "bookings-summary-popup-match-type-continue"
    SUMMARY_BACKDROP_ID = "bookings-summary-popup-backdrop"

    def __init__(
        self,
        headless: bool = True,
        location_id: str = "bookings-locations-15-name",
        wait_timeout: int = 10,
        chrome_path: str | None = None,
    ):
        """
        Initialize the scraper with undetected Chrome driver.

        Args:
            headless: Run in headless mode
            location_id: Padellen location element ID
            wait_timeout: Element wait timeout in seconds
            chrome_path: Path to Chrome executable (optional, rarely needed)
        """
        self.location_id = location_id
        self.wait_timeout = wait_timeout

        options = Options()
        if chrome_path:
            options.binary_location = chrome_path
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        self.driver = uc.Chrome(options=options, version_main=144)
        self.wait = WebDriverWait(self.driver, self.wait_timeout)
        logger.info("Chrome driver initialized")

    def navigate_to_booking(self) -> None:
        """Navigate to the booking page."""
        logger.info(f"Navigating to {self.BOOKING_URL}")
        self.driver.get(self.BOOKING_URL)

    def select_location(self) -> None:
        """Select the booking location."""
        logger.info(f"Selecting location ({self.location_id})...")
        location_element = self.wait.until(
            EC.element_to_be_clickable((By.ID, self.location_id))
        )
        location_element.click()
        logger.info("Location selected")

    def select_duration(self, duration_minutes: int) -> None:
        """
        Select the booking duration.

        Args:
            duration_minutes: Duration in minutes (60, 90, or 120)
        """
        if duration_minutes not in [60, 90, 120]:
            raise ValueError(f"Duration must be 60, 90, or 120, got {duration_minutes}")

        duration_element_id = f"bookings-date-step-duration-{duration_minutes}"
        logger.info(f"Selecting duration {duration_minutes} minutes...")
        duration_element = self.wait.until(
            EC.element_to_be_clickable((By.ID, duration_element_id))
        )
        duration_element.click()
        logger.info(f"Duration {duration_minutes} minutes selected")

    def select_date(self, target_date: str) -> None:
        """
        Select the target date from the calendar.

        Args:
            target_date: Date in ISO format (YYYY-MM-DD)
        """
        date_element_id = f"bookings-date-step-calendar-day-{target_date}"
        logger.info(f"Selecting date {target_date}...")

        date_element = self.wait.until(
            EC.element_to_be_clickable((By.ID, date_element_id))
        )

        # Verify the date is available
        classes = date_element.get_attribute("class") or ""
        if "cursor-not-allowed" in classes:
            raise ValueError(
                f"Date {target_date} is not available (cursor-not-allowed)"
            )

        date_element.click()
        logger.info(f"Date {target_date} selected")

    def get_available_slots(self, time_range: tuple[str, str]) -> list[str]:
        """
        Get available time slots within the given range.

        Args:
            time_range: Tuple of (start_time, end_time) in HH:MM format

        Returns:
            List of available slot times in HH:MM format
        """
        start_time, end_time = time_range
        available_slots = []

        try:
            # Wait for slots container to be present
            self.wait.until(
                EC.presence_of_element_located((By.ID, self.SLOTS_CONTAINER_ID))
            )
            logger.info("Slots container found")

            # Wait for slot elements to appear
            self.wait.until(
                EC.presence_of_all_elements_located(
                    (
                        By.XPATH,
                        f"//*[@id='{self.SLOTS_CONTAINER_ID}']//*[contains(@id, 'bookings-date-step-time-')]",
                    )
                )
            )
            logger.info("Slots loaded and ready")

            # Wait for DOM to stabilize before reading elements.
            # The JS framework may still be rendering slots after the
            # container appears, causing stale element references.
            time.sleep(0.5)

            max_stale_retries = 3
            for stale_attempt in range(max_stale_retries):
                available_slots = []
                stale_hit = False

                slots_container = self.driver.find_element(By.ID, self.SLOTS_CONTAINER_ID)
                slot_divs = slots_container.find_elements(
                    By.XPATH, ".//*[contains(@id, 'bookings-date-step-time-')]"
                )
                logger.info(f"Found {len(slot_divs)} time slot elements")

                for slot_div in slot_divs:
                    try:
                        slot_id = slot_div.get_attribute("id")
                        if not slot_id or not slot_id.startswith(
                            "bookings-date-step-time-"
                        ):
                            continue

                        # Extract time from ID (format: bookings-date-step-time-HH:MM)
                        time_part = slot_id.replace("bookings-date-step-time-", "")

                        # Skip if it contains "-spare" or other suffixes
                        if "-" in time_part and time_part.count(":") == 0:
                            continue

                        # Get just the HH:MM part
                        slot_time = (
                            time_part.split("-")[0] if "-" in time_part else time_part
                        )

                        # Validate time format
                        if ":" not in slot_time:
                            continue

                        # Check if time is in range
                        if not self._is_time_in_range(slot_time, start_time, end_time):
                            continue

                        # Check availability by examining classes
                        classes = slot_div.get_attribute("class") or ""

                        # Skip disabled slots (cursor-not-allowed, line-through, text-gray-400)
                        if "cursor-not-allowed" in classes or "line-through" in classes:
                            logger.debug(f"Slot {slot_time} is disabled (fully booked)")
                            continue

                        # Available slots have border-gray-300 and cursor-pointer
                        # Waitlist slots have border-dashed
                        if (
                            "border-gray-300" in classes
                            and "border-dashed" not in classes
                            and "cursor-pointer" in classes
                        ):
                            available_slots.append(slot_time)
                            logger.debug(f"Found available slot: {slot_time}")
                        elif "border-dashed" in classes:
                            logger.debug(f"Slot {slot_time} is on waitlist")

                    except StaleElementReferenceException:
                        logger.warning(
                            f"Stale element reference, will re-fetch "
                            f"(attempt {stale_attempt + 1}/{max_stale_retries})"
                        )
                        stale_hit = True
                        break
                    except Exception as e:
                        logger.warning(f"Error processing slot element: {e}")
                        continue

                if not stale_hit:
                    break
                # DOM changed under us — wait a bit longer and re-fetch all elements
                time.sleep(0.5)
            else:
                logger.warning("Max stale-element retries reached, returning partial results")

            logger.info(
                f"Available slots in range [{start_time}-{end_time}]: {available_slots}"
            )
            return available_slots

        except TimeoutException:
            logger.error(
                f"Timeout waiting for slots container {self.SLOTS_CONTAINER_ID}"
            )
            raise
        except Exception as e:
            logger.error(f"Failed to get available slots: {e}")
            raise

    @staticmethod
    def _is_time_in_range(slot_time: str, start_time: str, end_time: str) -> bool:
        """Check if a time slot is within the given range."""
        try:
            slot_h, slot_m = map(int, slot_time.split(":"))
            start_h, start_m = map(int, start_time.split(":"))
            end_h, end_m = map(int, end_time.split(":"))

            slot_minutes = slot_h * 60 + slot_m
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m

            return start_minutes <= slot_minutes <= end_minutes
        except (ValueError, AttributeError):
            return False

    def click_slot(self, slot_time: str) -> None:
        """
        Click an available time slot.

        Args:
            slot_time: Time in HH:MM format
        """
        slot_id = f"bookings-date-step-time-{slot_time}"
        logger.info(f"Clicking slot {slot_time}...")
        slot_element = self.wait.until(EC.element_to_be_clickable((By.ID, slot_id)))
        slot_element.click()
        logger.info(f"Slot {slot_time} clicked")

    def login(self, email: str, password: str) -> None:
        """
        Fill in login credentials and submit. If no login form appears
        (already logged in), returns silently.

        Args:
            email: Account email address
            password: Account password
        """
        try:
            login_wait = WebDriverWait(self.driver, 10)
            email_field = login_wait.until(
                EC.presence_of_element_located((By.ID, self.LOGIN_EMAIL_ID))
            )
        except TimeoutException:
            logger.info("No login form detected, already logged in")
            return

        logger.info("Login form detected, filling credentials...")
        email_field.clear()
        email_field.send_keys(email)

        password_field = self.driver.find_element(By.ID, self.LOGIN_PASSWORD_ID)
        password_field.clear()
        password_field.send_keys(password)

        # Check "remember me" checkbox
        try:
            remember_checkbox = self.driver.find_element(By.ID, self.LOGIN_REMEMBER_ID)
            if not remember_checkbox.is_selected():
                remember_checkbox.click()
        except Exception:
            logger.debug("Remember me checkbox not found or not clickable")

        submit_button = self.driver.find_element(By.ID, self.LOGIN_SUBMIT_ID)
        submit_button.click()
        logger.info("Login submitted")

    def confirm_booking(self) -> None:
        """Wait for and click the booking confirmation button."""
        logger.info("Waiting for booking summary popup...")
        confirm_wait = WebDriverWait(self.driver, 15)
        continue_button = confirm_wait.until(
            EC.element_to_be_clickable((By.ID, self.SUMMARY_CONTINUE_ID))
        )
        continue_button.click()
        logger.info("Booking confirmed")

    def cancel_reservation(self) -> None:
        """Cancel the current reservation by clicking the backdrop overlay."""
        logger.info("Cancelling reservation (clicking backdrop)...")
        backdrop = self.wait.until(
            EC.element_to_be_clickable((By.ID, self.SUMMARY_BACKDROP_ID))
        )
        backdrop.click()
        logger.info("Reservation cancelled")

    def cleanup(self) -> None:
        """Close the Chrome driver."""
        try:
            if self.driver:
                self.driver.quit()
                logger.info("Chrome driver closed")
        except Exception as e:
            logger.error(f"Error closing driver: {e}")
