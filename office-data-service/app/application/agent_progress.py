"""将有界展示快照合并保存；不用token更新审批版本，不持长事务。"""

import time

from app.infrastructure.models import AgentRun
from app.modules.agent.outputs import RunProgress
from app.modules.agent.state import ACTIVE


class ProgressWriter:
    def __init__(self, sessions, identifier):
        self.sessions, self.identifier = sessions, identifier
        self.last = 0.0
        self.stage = ""
        self.seq = 0

    async def __call__(self, value):
        checked = RunProgress.model_validate(value)
        now = time.monotonic()
        if checked.seq <= self.seq:
            return
        # 首条文字、阶段切换立即保存；同阶段至多每250ms一次。
        if checked.stage == self.stage and now - self.last < 0.25:
            return
        async with self.sessions.begin() as db:
            row = await db.get(AgentRun, self.identifier, with_for_update=True)
            if row is None or row.status not in ACTIVE:
                return
            row.progress = checked.model_dump(mode="json")
            # version只用于业务决定，不因展示增量变化，避免审批竞争。
        self.stage, self.last, self.seq = checked.stage, now, checked.seq
