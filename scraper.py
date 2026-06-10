import logging
import os
import re
import shutil
import subprocess
import sys
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
import undetected_chromedriver as uc

logger = logging.getLogger(__name__)


class DateNotBookableError(Exception):
    """The target date is reachable but not yet open for booking.

    Distinct from real errors: the monitor should treat this as "no slots yet"
    and simply wait for the next poll, not retry or back off aggressively.
    """


def find_chrome_executable() -> str | None:
    """Locate a Chrome/Chromium binary across platforms and release channels.

    undetected-chromedriver's built-in detection misses non-standard installs
    (Chrome Beta/Dev/Canary, per-user installs), so we check the Windows
    registry and common locations ourselves before falling back to uc.
    """
    candidates: list[str] = []

    if sys.platform == "win32":
        # Registry App Paths covers whatever channel is actually installed.
        try:
            import winreg

            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(
                        root,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                    ) as key:
                        path, _ = winreg.QueryValueEx(key, "")
                        if path:
                            candidates.append(path)
                except OSError:
                    continue
        except ImportError:
            pass

        bases = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        channels = ["Chrome", "Chrome Beta", "Chrome Dev", "Chrome SxS"]
        for base in bases:
            if not base:
                continue
            for channel in channels:
                candidates.append(
                    os.path.join(base, "Google", channel, "Application", "chrome.exe")
                )
    elif sys.platform == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    else:  # linux / other
        for name in (
            "google-chrome",
            "google-chrome-stable",
            "google-chrome-beta",
            "chromium",
            "chromium-browser",
            "chrome",
        ):
            found = shutil.which(name)
            if found:
                candidates.append(found)

    for path in candidates:
        if path and os.path.exists(path):
            return path

    # Last resort: let undetected-chromedriver try its own detector.
    try:
        return uc.find_chrome_executable()
    except Exception:
        return None


def detect_chrome_major_version(binary: str | None) -> int | None:
    """Detect the installed Chrome major version so the matching driver is used."""
    if not binary:
        return None

    if sys.platform == "win32":
        # `chrome.exe --version` prints nothing on Windows, but the Application
        # directory contains a version-named subfolder (e.g. .../150.0.7871.13/).
        app_dir = os.path.dirname(binary)
        try:
            versions = [
                int(m.group(1))
                for entry in os.listdir(app_dir)
                if (m := re.fullmatch(r"(\d+)\.\d+\.\d+\.\d+", entry))
                and os.path.isdir(os.path.join(app_dir, entry))
            ]
            if versions:
                return max(versions)
        except OSError:
            pass
        return None

    # macOS/Linux: the binary reports its version on stdout.
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=10
        ).stdout
        if m := re.search(r"(\d+)\.\d+\.\d+", out):
            return int(m.group(1))
    except Exception:
        pass
    return None


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

    # Calendar navigation element IDs
    CALENDAR_MONTHS_ID = "bookings-date-step-calendar-navigation-months"
    CALENDAR_FORWARD_ID = "bookings-date-step-calendar-arrow-forward"

    # Month names as shown in the calendar header (the site renders Dutch)
    DUTCH_MONTHS = {
        "januari": 1,
        "februari": 2,
        "maart": 3,
        "april": 4,
        "mei": 5,
        "juni": 6,
        "juli": 7,
        "augustus": 8,
        "september": 9,
        "oktober": 10,
        "november": 11,
        "december": 12,
    }

    def __init__(
        self,
        headless: bool = True,
        location_id: str = "bookings-locations-15-name",
        wait_timeout: int = 10,
        chrome_path: str | None = None,
        chrome_version: int | None = None,
    ):
        """
        Initialize the scraper with undetected Chrome driver.

        Args:
            headless: Run in headless mode
            location_id: Padellen location element ID
            wait_timeout: Element wait timeout in seconds
            chrome_path: Path to Chrome executable (optional, rarely needed)
            chrome_version: Pin the Chrome major version for the driver. Leave
                None (default) to auto-detect the installed Chrome version.
        """
        self.location_id = location_id
        self.wait_timeout = wait_timeout

        # Resolve the Chrome binary (any channel) and its major version so the
        # matching driver is fetched. Both are overridable via config.
        binary = chrome_path or find_chrome_executable()
        if binary:
            logger.info(f"Using Chrome binary at {binary}")
        else:
            logger.warning(
                "Could not locate a Chrome binary; relying on uc auto-detection"
            )

        version_main = chrome_version or detect_chrome_major_version(binary)
        if version_main:
            logger.info(f"Targeting Chrome major version {version_main}")
        else:
            logger.warning(
                "Could not detect Chrome version; uc will use the latest driver"
            )

        options = Options()
        if headless:
            options.add_argument("--headless=new")
        else:
            # In visible mode on Windows, GPU compositing often paints the whole
            # window gray; disabling it and maximizing forces a correct render.
            options.add_argument("--start-maximized")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")

        self.driver = uc.Chrome(
            options=options,
            browser_executable_path=binary,
            version_main=version_main,
        )
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

    def _get_displayed_month(self) -> tuple[int, int] | None:
        """Return (year, month) currently shown in the calendar header, or None.

        The header element's text looks like "juni, 2026".
        """
        try:
            header = self.driver.find_element(By.ID, self.CALENDAR_MONTHS_ID)
            text = (header.get_attribute("textContent") or header.text or "").strip()
        except Exception:
            return None

        match = re.search(r"([A-Za-z]+)\D+(\d{4})", text)
        if not match:
            return None
        month = self.DUTCH_MONTHS.get(match.group(1).lower())
        if not month:
            return None
        return int(match.group(2)), month

    def _is_forward_enabled(self) -> bool:
        """Whether the calendar's forward arrow can advance to the next month.

        The site marks the arrow with `cursor-pointer` when navigable and
        `text-white` (muted/disabled) once it hits the booking-window limit.
        """
        try:
            el = self.driver.find_element(By.ID, self.CALENDAR_FORWARD_ID)
            return "cursor-pointer" in (el.get_attribute("class") or "")
        except Exception:
            return False

    def _click_forward_month(self) -> None:
        """Advance the calendar to the next month.

        The forward control is an SVG, so a normal click is unreliable; we
        dispatch a bubbling MouseEvent on it instead.
        """
        self.wait.until(
            EC.presence_of_element_located((By.ID, self.CALENDAR_FORWARD_ID))
        )
        self.driver.execute_script(
            "document.getElementById(arguments[0])"
            ".dispatchEvent(new MouseEvent('click', { bubbles: true }));",
            self.CALENDAR_FORWARD_ID,
        )

    def select_date(self, target_date: str) -> None:
        """
        Select the target date from the calendar, navigating forward through
        months if the target is not currently displayed.

        Args:
            target_date: Date in ISO format (YYYY-MM-DD)
        """
        target_year = int(target_date[:4])
        target_month = int(target_date[5:7])
        date_element_id = f"bookings-date-step-calendar-day-{target_date}"
        logger.info(f"Selecting date {target_date}...")

        # Wait for the calendar header to be present and readable.
        self.wait.until(
            EC.presence_of_element_located((By.ID, self.CALENDAR_MONTHS_ID))
        )
        try:
            self.wait.until(lambda d: self._get_displayed_month() is not None)
        except TimeoutException:
            logger.warning(
                "Could not read calendar month header; "
                "attempting to click the day directly"
            )

        # Advance month-by-month until the calendar shows the target month.
        max_advances = 24  # don't navigate more than ~2 years ahead
        for _ in range(max_advances + 1):
            displayed = self._get_displayed_month()
            if displayed is None:
                break
            if displayed == (target_year, target_month):
                logger.info(f"Calendar showing target month {target_year}-{target_month:02d}")
                break
            if displayed > (target_year, target_month):
                raise ValueError(
                    f"Calendar is showing {displayed[0]}-{displayed[1]:02d}, "
                    f"which is past target {target_date}; cannot navigate backward"
                )

            # The forward arrow is disabled once we reach the booking-window
            # limit, so a future date may simply not be bookable yet.
            if not self._is_forward_enabled():
                raise DateNotBookableError(
                    f"Date {target_date} is not yet bookable: the calendar cannot "
                    f"advance past {displayed[0]}-{displayed[1]:02d} "
                    f"(booking-window limit reached)"
                )

            logger.info(
                f"Calendar at {displayed[0]}-{displayed[1]:02d}, "
                f"advancing toward {target_year}-{target_month:02d}"
            )
            self._click_forward_month()

            # Wait for the header to actually change before reading it again.
            try:
                WebDriverWait(self.driver, self.wait_timeout).until(
                    lambda d, prev=displayed: self._get_displayed_month() != prev
                )
            except TimeoutException:
                logger.warning("Calendar did not advance after clicking forward")
                break
        else:
            raise ValueError(
                f"Target month for {target_date} not reachable "
                f"within {max_advances} months"
            )

        date_element = self.wait.until(
            EC.element_to_be_clickable((By.ID, date_element_id))
        )

        # Verify the date is available
        classes = date_element.get_attribute("class") or ""
        if "cursor-not-allowed" in classes:
            raise DateNotBookableError(
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
