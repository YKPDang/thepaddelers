import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class StateTracker:
    """Track previously seen availability to detect new slots."""

    def __init__(self, state_file: str = "availability_state.json"):
        """
        Initialize state tracker.

        Args:
            state_file: Path to the state file
        """
        self.state_file = Path(state_file)
        self.state = self._load_state()

    def _load_state(self) -> dict:
        """Load state from file or create new state."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    state = json.load(f)
                logger.info(f"Loaded state from {self.state_file}")
                return state
            except Exception as e:
                logger.warning(f"Failed to load state file: {e}. Starting fresh.")
                return {"date": None, "slots": []}
        else:
            logger.info("No state file found. Starting fresh.")
            return {"date": None, "slots": []}

    def _save_state(self) -> None:
        """Save state to file."""
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
            logger.debug(f"State saved to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def update_and_get_new(self, current_slots: list[str], date: str) -> list[str]:
        """
        Compare current slots with previous state and identify new slots.

        Args:
            current_slots: List of currently available slot times
            date: Target date (ISO format)

        Returns:
            List of newly available slots not seen before
        """
        previous_slots = set(self.state.get("slots", []))
        current_slots_set = set(current_slots)
        new_slots = sorted(current_slots_set - previous_slots)

        # Update state
        self.state["date"] = date
        self.state["slots"] = sorted(current_slots)
        self.state["last_updated"] = datetime.now().isoformat()

        self._save_state()

        if new_slots:
            logger.info(f"New slots detected: {new_slots}")
        else:
            logger.debug("No new slots detected")

        return new_slots

    @staticmethod
    def rank_slots_by_priority(
        available_slots: list[str], priority_times: list[str]
    ) -> dict:
        """
        Rank available slots by proximity to priority times.

        Args:
            available_slots: List of available slot times
            priority_times: List of preferred times in order of preference

        Returns:
            Dict with 'best_match' (closest slot), 'ranked_slots' (all slots sorted by preference)
        """
        if not priority_times or not available_slots:
            return {
                "best_match": available_slots[0] if available_slots else None,
                "ranked_slots": available_slots,
            }

        def time_to_minutes(time_str: str) -> int:
            """Convert HH:MM to minutes since midnight."""
            try:
                h, m = map(int, time_str.split(":"))
                return h * 60 + m
            except (ValueError, AttributeError):
                return 0

        # Calculate distance from each slot to nearest priority time
        slot_scores = []
        for slot in available_slots:
            slot_minutes = time_to_minutes(slot)
            # Find closest priority time
            min_distance = float("inf")
            closest_priority = None
            for priority in priority_times:
                priority_minutes = time_to_minutes(priority)
                distance = abs(slot_minutes - priority_minutes)
                if distance < min_distance:
                    min_distance = distance
                    closest_priority = priority

            slot_scores.append((slot, min_distance, closest_priority))

        # Sort by distance (closer is better)
        ranked = sorted(slot_scores, key=lambda x: x[1])
        best_match = ranked[0][0] if ranked else None

        return {
            "best_match": best_match,
            "best_match_distance_minutes": ranked[0][1] if ranked else None,
            "best_match_closest_to": ranked[0][2] if ranked else None,
            "ranked_slots": [slot[0] for slot in ranked],
        }
