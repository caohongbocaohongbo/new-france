"""28 低位竞价雷达内存滑动窗口与六里程碑状态。"""
from collections import defaultdict, deque


class AuctionStore:
    def __init__(self, max_frames: int = 240):
        self.max_frames = max(1, int(max_frames))
        self.auction_frames = defaultdict(lambda: deque(maxlen=self.max_frames))
        self.milestones = {}
        self.state_date = None
        self.frozen_pool = None
        self.last_quote = {}

    def ensure_date(self, now):
        d = getattr(now, "date", None)
        d = d() if callable(d) else d
        if self.state_date is not None and self.state_date != d:
            self.reset()
        self.state_date = d

    def add_auction_frame(self, code, quote, now):
        self.auction_frames[str(code).zfill(6)].append({"ts": now.isoformat(), "quote": quote})
        self.last_quote[str(code).zfill(6)] = quote

    def frames(self, code):
        return list(self.auction_frames[str(code).zfill(6)])

    def set_milestone(self, key, state, evidence=None, error=None, now=None):
        m = self.milestones.setdefault(key, {"key": key, "state": "pending"})
        m["state"] = state
        if now is not None:
            ts = now.isoformat()
            if state in ("started", "running"):
                m["started_at"] = ts
            else:
                m["finished_at"] = ts
        if evidence:
            m.setdefault("evidence", {}).update(evidence)
        if error:
            m["error"] = error
        return m

    def milestone_map(self):
        return dict(self.milestones)

    def reset(self):
        self.auction_frames.clear()
        self.milestones.clear()
        self.state_date = None
