"""Bound team retries without deleting history or inventing match identities."""
from datetime import datetime, timezone, timedelta
import re

KST = timezone(timedelta(hours=9))


def kickoff(value):
    text = str(value or '').strip()
    match = re.fullmatch(
        r'(\d{2}|\d{4})\.(\d{2})\.(\d{2})\s*(?:\([월화수목금토일]\)\s*)?(\d{2}):(\d{2})', text
    )
    try:
        if match:
            year, month, day, hour, minute = map(int, match.groups())
            if year < 100:
                year += 2000
            return datetime(year, month, day, hour, minute, tzinfo=KST)
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?', text):
            return None
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)
    except ValueError:
        return None


def due_rows(conn, now, limit):
    """Keep unknown times and current games; only omit kickoffs over a day ago.

    Filter before the batch limit so old records cannot crowd out new games.
    SELECT only: originals, statuses, attempts and results remain untouched.
    """
    cursor = conn.execute(
        """SELECT retry_key,home_name,away_name,match_time,league_name,attempts
           FROM team_identity_retry_queue
           WHERE status!='RESOLVED' AND (next_retry_at IS NULL OR next_retry_at<=?)
           ORDER BY COALESCE(next_retry_at,''),updated_at,retry_key""",
        (now.astimezone(timezone.utc).isoformat(timespec='seconds'),),
    )
    wanted = max(1, int(limit or 1))
    selected = []
    retained = unknown = eligible = 0
    while True:
        batch = cursor.fetchmany(100)
        if not batch:
            break
        for row in batch:
            when = kickoff(row[3])
            if when is not None and when < now - timedelta(days=1):
                retained += 1
                continue
            eligible += 1
            if when is None:
                unknown += 1
            if len(selected) < wanted:
                selected.append(row)
    return selected, {'eligible_due': eligible, 'past_due_retained': retained,
                      'unknown_due': unknown}


def retry_report(processed, resolved, totals, due):
    return {**totals, **due, 'total_resolved': int(totals.get('resolved', 0)),
            'processed': processed, 'resolved': resolved,
            'due': due['eligible_due']}
