"""Cooperative request budget; reserve time to persist delivery results."""
import time


class BudgetExpired(TimeoutError):
    pass


class Budget:
    def __init__(self, seconds=10, clock=time.monotonic):
        self.clock = clock
        self.deadline = clock() + seconds

    def remaining(self):
        return max(0, self.deadline - self.clock())

    def can_work(self):
        return self.remaining() > 3

    def io_timeout(self):
        remaining = self.remaining() - 2
        if remaining <= 0:
            raise BudgetExpired()
        return min(2, remaining)
