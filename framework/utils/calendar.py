from datetime import datetime, timedelta
from typing import List, Optional
import pandas as pd

class TradingCalendar:
    """Handle rebalancing schedule"""

    def __init__(
        self,
        frequency: str = 'weekly',
        day: str = 'monday',
        time: str = '00:00'
    ):
        self.frequency = frequency.lower()
        self.day = day.lower()
        self.time = time

        # Parse time
        hour, minute = map(int, time.split(':'))
        self.rebalance_hour = hour
        self.rebalance_minute = minute

        # Day mapping
        self.day_map = {
            'monday': 0,
            'tuesday': 1,
            'wednesday': 2,
            'thursday': 3,
            'friday': 4,
            'saturday': 5,
            'sunday': 6
        }

    def is_rebalance_time(self, timestamp: datetime) -> bool:
        """Check if current timestamp is a rebalance time"""

        # Check time first
        if (timestamp.hour != self.rebalance_hour or
            timestamp.minute != self.rebalance_minute):
            return False

        # Check frequency
        if self.frequency == 'daily':
            return True

        elif self.frequency == 'weekly':
            return timestamp.weekday() == self.day_map.get(self.day, 0)

        elif self.frequency == 'monthly':
            # First trading day of month
            return timestamp.day <= 7 and timestamp.weekday() == self.day_map.get(self.day, 0)

        return False

    def get_rebalance_dates(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> List[datetime]:
        """Get all rebalance dates in a period"""

        rebalance_dates = []
        current = start_date

        while current <= end_date:
            if self.is_rebalance_time(current):
                rebalance_dates.append(current)

            # Move to next potential rebalance
            if self.frequency == 'daily':
                current += timedelta(days=1)
            elif self.frequency == 'weekly':
                current += timedelta(days=1)
            elif self.frequency == 'monthly':
                current += timedelta(days=1)
            else:
                current += timedelta(hours=1)

        return rebalance_dates
