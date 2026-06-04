"""Decide when a campaign has plateaued (the trigger for the analyst loop).

We use AFL's own native signals rather than differentiating plot_data:
  - time_wo_finds: seconds since the last new corpus find
  - pending_favs / pending_total: queue entries still worth fuzzing
A plateau = the fuzzer has stopped finding anything new AND has nothing
favored left to try. That is exactly the state the plain maze sat in
(time_wo_finds climbing, pending_favs==0, edges flat) in M1.
"""
from __future__ import annotations

from dataclasses import dataclass

from .fuzzer import Snapshot


@dataclass
class PlateauDetector:
    min_stall_seconds: int = 30   # no new find for at least this long
    require_no_pending_favs: bool = True

    def is_plateau(self, snap: Snapshot) -> bool:
        if snap.solved:
            return False  # solving is progress, not a plateau
        if snap.time_wo_finds < self.min_stall_seconds:
            return False
        if self.require_no_pending_favs and snap.pending_favs > 0:
            return False
        return True

    def explain(self, snap: Snapshot) -> str:
        return (f"run_time={snap.run_time}s time_wo_finds={snap.time_wo_finds}s "
                f"edges={snap.edges_found} corpus={snap.corpus_count} "
                f"pending_favs={snap.pending_favs} pending_total={snap.pending_total} "
                f"-> plateau={self.is_plateau(snap)}")
