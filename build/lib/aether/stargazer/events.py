"""Reading aether:events for Stargazer (spec §15.2, §16.1-7).

Initial load pulls a BOUNDED recent window via XREVRANGE (never the whole
history); live updates tail via XREAD from a cursor. Reconnect resumes strictly
after the last-seen id, so nothing already displayed is re-sent.
"""
from __future__ import annotations

import json
from typing import List, Optional, Tuple

from ..core.aether_client import EVENTS_STREAM

Record = Tuple[str, dict]  # (stream_entry_id, parsed_record)


class EventReader:
    def __init__(self, ro_redis, stream: str = EVENTS_STREAM) -> None:
        self.ro = ro_redis
        self.stream = stream

    @staticmethod
    def _parse(rows) -> List[Record]:
        return [(eid, json.loads(fields["data"])) for eid, fields in rows]

    def recent(self, window: int = 200) -> List[Record]:
        """The most recent ``window`` events, oldest-first. Bounded — this is the
        initial-load window, NOT the full stream (spec §15.2 / §16.1-7)."""
        rows = self.ro.xrevrange(self.stream, count=window)  # newest first
        return list(reversed(self._parse(rows)))             # → chronological

    def after(self, last_id: str, count: int = 1000) -> List[Record]:
        """All events strictly after ``last_id`` (used for clean reconnect)."""
        rows = self.ro.xrange(self.stream, min=f"({last_id}", max="+", count=count)
        return self._parse(rows)

    def tail(self, last_id: str, count: int = 200, block_ms: int = 1000) -> List[Record]:
        """Block up to ``block_ms`` for events after ``last_id`` (live follow)."""
        resp = self.ro.xread({self.stream: last_id}, count=count, block=block_ms)
        out: List[Record] = []
        for _stream, entries in resp or []:
            out.extend(self._parse(entries))
        return out

    def latest_id(self) -> str:
        """Id of the newest event, or '0' if the stream is empty — a cursor that
        a fresh SSE connection can tail forward from."""
        rows = self.ro.xrevrange(self.stream, count=1)
        return rows[0][0] if rows else "0"
