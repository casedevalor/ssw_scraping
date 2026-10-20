from datetime import datetime, date, timedelta
from typing import Tuple, List, Dict, Optional


class Dates:
    """
    A class to handle date-related operations for various routines.
    """

    # -----------------------------------------------------------------
    # Private helpers shared by the month/year "look-back" methods below
    # -----------------------------------------------------------------
    def _month_start(self, d: date) -> date:
        """Returns the 1st day of d's month."""
        return d.replace(day=1)

    def _months_back_ranges(self, today: date, n: int, clip_current_to_today: bool) -> List[Tuple[str, str]]:
        """
        Shared walker behind previous_twelve_months_ranges() and
        last_three_months_ranges() — both are "n consecutive calendar
        months ending at the current one", differing only in whether the
        current month is included (clipped to today) or excluded entirely.

        clip_current_to_today=True:  the last entry is the current month,
            from its 1st up to `today` (a "growing" range); the remaining
            n-1 entries are full completed months before it.
        clip_current_to_today=False: all n entries are full completed
            months; the current (in-progress) month is not included.

        Returns them oldest first, in 'ddmmyy' format.
        """
        cursor = self._month_start(today)
        ranges: List[Tuple[date, date]] = []

        if clip_current_to_today:
            ranges.append((cursor, today))
            n -= 1

        for _ in range(n):
            end = cursor - timedelta(days=1)
            start = self._month_start(end)
            ranges.append((start, end))
            cursor = start

        ranges.reverse()
        return [(s.strftime("%d%m%y"), e.strftime("%d%m%y")) for s, e in ranges]

    def _year_ranges(self, current_year: int, n_years: int, clip_current_to: Optional[date]) -> List[Tuple[str, str]]:
        """
        Shared builder behind previous_and_current_year_ranges(),
        last_three_years_to_date_ranges() and
        last_three_full_calendar_years_ranges() — all are "the last
        n_years calendar years", differing only in how the current year's
        end date is capped: `clip_current_to` (typically today) stops it
        there, or None lets it run to Dec 31st even if that's in the future.

        Returns them oldest first, in 'ddmmyy' format.
        """
        years = [current_year - (n_years - 1) + i for i in range(n_years)]
        ranges = []
        for year in years:
            start = date(year, 1, 1)
            end = clip_current_to if (year == current_year and clip_current_to is not None) else date(year, 12, 31)
            ranges.append((start.strftime("%d%m%y"), end.strftime("%d%m%y")))
        return ranges

    def current_day(self) -> str:
        """
        Returns today's date in 'ddmmyy' format.

        Example: if today is 15/11/2024, returns '151124'.
        """
        return datetime.now().strftime("%d%m%y")

    def last_day(self) -> str:
        """
        Returns yesterday's date in 'ddmmyy' format.

        Example: if today is 15/11/2024, returns '141124'.
        """
        return (datetime.now() - timedelta(days=1)).strftime("%d%m%y")

    def yesterday_and_today(self) -> List[str]:
        """
        Returns yesterday and today as two separate dates, in 'ddmmyy' format,
        oldest first — for per-day routines that reprocess yesterday and keep
        today up to date on every run.

        Example: if today is 15/11/2024, returns ['141124', '151124'].
        """
        return [self.last_day(), self.current_day()]

    def fifteen_days_ago(self) -> str:
        """
        Returns the date 15 days before today, in 'ddmmyy' format.

        Example: if today is 15/11/2024, returns '311024'.

        Renamed from `fortnight()` — that name was too easily confused with
        `get_fortnight_dates()`, which returns a completely different shape
        (a list of two date ranges, not a single date).
        """
        return (datetime.now() - timedelta(days=15)).strftime("%d%m%y")

    def month_to_date_range(self) -> Tuple[str, str]:
        """
        Returns the "month-to-date" range: the 1st of the current month and
        today, in 'ddmmyy' format. The end date keeps moving forward each
        day the routine runs — hence the previous name "growing_days".

        Example: if today is 15/01/2022, returns ('010122', '150122').
        """
        current_date = date.today()
        first_day_current_month = date(current_date.year, current_date.month, 1)
        return first_day_current_month.strftime("%d%m%y"), current_date.strftime("%d%m%y")

    def previous_month_range(self) -> Tuple[str, str]:
        """
        Returns the previous month as a full (start, end) range, in 'ddmmyy'
        format — always the complete calendar month, regardless of today's day.

        Example: if today is any day in January 2022, returns ('011221', '311221').
        """
        today = datetime.now()
        first_day_current_month = today.replace(day=1)
        last_day_previous_month = first_day_current_month - timedelta(days=1)
        first_day_previous_month = last_day_previous_month.replace(day=1)
        return first_day_previous_month.strftime('%d%m%y'), last_day_previous_month.strftime('%d%m%y')

    def year_to_date_range(self) -> Tuple[str, str]:
        """
        Returns the year-to-date range: Jan 1st of the current year and
        today, in 'ddmmyy' format.

        Example: if today is 15/01/2022, returns ('010122', '150122').
        """
        initial_date = date(date.today().year, 1, 1)
        current_date = date.today()
        return initial_date.strftime("%d%m%y"), current_date.strftime("%d%m%y")

    def previous_and_current_year_ranges(self) -> List[Tuple[str, str]]:
        """
        Returns two ranges: the full previous calendar year, and the current
        year from Jan 1st up to today. Dates are in 'ddmmyy' format.

        Example: if today is 15/03/2025, returns
        [('010124', '311224'), ('010125', '150325')].

        Built on the shared `_year_ranges()` helper, like
        `last_three_years_to_date_ranges()` with a 2-year window.
        """
        today = date.today()
        return self._year_ranges(today.year, n_years=2, clip_current_to=today)

    def last_three_years_to_date_ranges(self) -> List[Tuple[str, str]]:
        """
        Returns a list of tuples with start and end dates for the last three years.
        The two previous years are full calendar years; the current year runs
        from Jan 1st up to today (partial, not Dec 31st).
        Dates are in 'ddmmyy' format.

        Example: if today is 15/03/2025, returns
        [('010123', '311223'), ('010124', '311224'), ('010125', '150325')].

        Built on the shared `_year_ranges()` helper (see
        `last_three_full_calendar_years_ranges()`, which uses the same
        helper with the current year uncapped instead).
        """
        today = date.today()
        return self._year_ranges(today.year, n_years=3, clip_current_to=today)

    def previous_twelve_months_ranges(self) -> List[Tuple[str, str]]:
        """
        Returns the (start, end) ranges for the last 12 full calendar months,
        excluding the current (in-progress) month. Handles year transitions
        automatically.

        Order: chronological (oldest first, most recent last).

        Example: if today is any day in March 2025, the list runs from
        March 2024 to February 2025 (12 full months, March 2025 excluded).

        Built on the shared `_months_back_ranges()` helper (see
        `last_three_months_ranges()`, which uses the same helper but
        includes the current, still-growing month).
        """
        return self._months_back_ranges(date.today(), n=12, clip_current_to_today=False)

    def previous_and_current_month_ranges(self) -> List[Tuple[str, str]]:
        """
        Returns two ranges: the full previous month, and the current month
        from the 1st up to today. Dates are in 'ddmmyy' format.

        Example: if today is 15/03/2025, returns
        [('010225', '280225'), ('010325', '150325')].
        """
        today = datetime.now()
        last_month_end = today.replace(day=1) - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        current_month_start = today.replace(day=1)
        current_month_end = today

        return [
            (last_month_start.strftime('%d%m%y'), last_month_end.strftime('%d%m%y')),
            (current_month_start.strftime('%d%m%y'), current_month_end.strftime('%d%m%y'))
        ]

    def previous_and_current_month_to_yesterday_ranges(self) -> List[Tuple[str, str]]:
        """
        Returns the full previous month and the current month from the 1st
        up to yesterday (today is never included). Dates are in 'ddmmyy'
        format.

        On the 1st of the month the current month has no closed day yet, so
        only the previous month is returned — a range ending before it starts
        would be invalid.

        Example: if today is 15/03/2025, returns
        [('010225', '280225'), ('010325', '140325')];
        if today is 01/03/2025, returns [('010225', '280225')].
        """
        today = date.today()
        current_month_start = today.replace(day=1)
        last_month_end = current_month_start - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        yesterday = today - timedelta(days=1)

        ranges: List[Tuple[str, str]] = [
            (last_month_start.strftime('%d%m%y'), last_month_end.strftime('%d%m%y'))
        ]
        if yesterday >= current_month_start:
            ranges.append((current_month_start.strftime('%d%m%y'), yesterday.strftime('%d%m%y')))
        return ranges

    def last_three_months_ranges(self) -> List[Tuple[str, str]]:
        
        """
        Returns a list with:
        - The start and end dates of the two previous full months.
        - The start of the current month up to today.
        Dates are in 'ddmmyy' format.

        Example: if today is 28/03/2025, returns [
            ('010125', '310125'),  # January
            ('010225', '280225'),  # February
            ('010325', '280325'),  # March, up to today
        ]

        Built on the shared `_months_back_ranges()` helper (see
        `previous_twelve_months_ranges()`, which uses the same helper but
        excludes the current month entirely).
        """
        return self._months_back_ranges(date.today(), n=3, clip_current_to_today=True)
    
    def previous_and_current_fortnight_ranges(self) -> List[Tuple[str, str]]:
        """
        Returns the dates for the previous fortnight and the current fortnight.
        The previous fortnight is complete (first or second half of the previous month),
        and the current fortnight is from the beginning of the month up to the current day.

        Example: if today is 20/03/2025 (day > 15), returns
        [('010325', '150325'), ('160325', '200325')].

        Renamed from `get_fortnight_dates()` to match the `..._ranges` naming
        used by the other multi-range date functions in this class.

        :return: A list of tuples with start and end dates in 'ddmmyy' format.
        """
        today = datetime.today()
        day = today.day

        # Determine the current fortnight (growing)
        if 1 <= day <= 15:
            current_start_date = today.replace(day=1)
            current_end_date = today
        else:
            current_start_date = today.replace(day=16)
            current_end_date = today

        # Determine the previous fortnight (complete)
        if 1 <= day <= 15:
            last_month = today.replace(day=1) - timedelta(days=1)
            previous_start_date = last_month.replace(day=16)
            previous_end_date = last_month
        else:
            previous_start_date = today.replace(day=1)
            previous_end_date = today.replace(day=15)

        # Return a list of tuples with dates in 'ddmmyy' format
        return [
            (previous_start_date.strftime('%d%m%y'), previous_end_date.strftime('%d%m%y')),
            (current_start_date.strftime('%d%m%y'), current_end_date.strftime('%d%m%y'))
        ]

    def last_two_days_range(self) -> Tuple[str, str]:
        """
        Returns the dates of the last two days, excluding today, in the format 'ddmmyy'.
        Example: if today is 15/11/2024, returns ('131124', '141124')

        :return: A tuple with the dates of the two days prior to today.
        """
        # Get the current date and time
        today = datetime.now()
        # Calculate the dates for the last two days, excluding today
        day1 = (today - timedelta(days=1)).strftime('%d%m%y')
        day2 = (today - timedelta(days=2)).strftime('%d%m%y')
        # Return the days in the required order (two days ago, yesterday)
        return day2, day1

    def days_of_month_to_date(self) -> List[str]:
        """
        Returns every day of the current month from the 1st up to (but not
        including) today, as a list of individual dates.
        The dates are formatted as 'ddmmyy'.

        Example: if today is 03/03/2025, returns ['010325', '020325'].

        :return: List of strings representing each day of the month up to yesterday.
        """
        today = datetime.now()
        start_of_month = today.replace(day=1)
        days = []

        for i in range((today - start_of_month).days):
            day = start_of_month + timedelta(days=i)
            days.append(day.strftime('%d%m%y'))
        
        return days

    def last_three_full_calendar_years_ranges(self) -> List[Tuple[str, str]]:
        """
        Returns the start and end dates for the last 3 years in the format 'ddmmyy'.
        The start date is the 1st of January, and the end date is the 31st of December
        for EACH of the three years, including the current one — unlike
        `last_three_years_to_date_ranges()`, the current year here is not clipped
        to today, so its end date can land in the future.

        :return: A list of tuples, each containing the 'start' and 'end' dates for the last 3 years.

        Built on the shared `_year_ranges()` helper — see
        `last_three_years_to_date_ranges()` for the today-clipped variant.
        NOTE: unlike that method, this one is not currently called by any
        bot (dead code, kept in case a future routine needs the uncapped
        current-year end date).
        """
        return self._year_ranges(date.today().year, n_years=3, clip_current_to=None)

    def all_time_range(self) -> Tuple[str, str]:
        """
        Returns a tuple with the fixed start date '010101' and the current date in 'ddmmyy' format.

        Example:
        - If today is 15th March 2025: ('010101', '150325')
        - If today is 1st January 2025: ('010101', '010125')
        """
        # Fixed start date: 01/01/01 (assumed as base year 2001)
        start_date = "010101"

        # Current date
        current_date = date.today().strftime("%d%m%y")

        return start_date, current_date
    