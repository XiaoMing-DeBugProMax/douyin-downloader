class TaskCancellation(Exception):
    def __init__(self, *, retain_parts: bool) -> None:
        super().__init__("task cancelled")
        self.retain_parts = retain_parts


class TaskInterruption(Exception):
    """Stop active work while preserving resumable progress for a later run."""
