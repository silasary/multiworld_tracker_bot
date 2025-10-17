from ap_alert.models.enums import CompletionStatus, ProgressionStatus, TrackerStatus


import datetime


class CheeseGame(dict):
    @property
    def id(self) -> int:
        return self.get("id", -1)

    @property
    def game(self) -> str:
        return self.get("game", None)

    @property
    def progression_status(self) -> str:
        return ProgressionStatus(self.get("progression_status", "unknown"))

    @property
    def tracker_status(self) -> str:
        return TrackerStatus(self.get("tracker_status", "unknown"))

    @property
    def completion_status(self) -> str:
        return CompletionStatus(self.get("completion_status", "unknown"))

    @property
    def last_activity(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(self.get("last_activity", None) or "1970-01-01T00:00:00Z")

    @property
    def last_checked(self) -> datetime.datetime:
        last_checked_string = self.get("last_checked", None)
        if isinstance(last_checked_string, datetime.datetime):
            return last_checked_string
        return datetime.datetime.fromisoformat(last_checked_string or "1970-01-01T00:00:00Z")

    @property
    def name(self) -> str:
        return self.get("name", self.get("position", "Unknown"))
