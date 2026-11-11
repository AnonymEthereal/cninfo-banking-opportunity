from contextvars import ContextVar
from time import monotonic

deadline = ContextVar('announcement_deadline', default=None)

def remaining():
    end = deadline.get()
    if end is None:
        return None
    seconds = end - monotonic()
    if seconds <= 0:
        raise TimeoutError('Analysis deadline exceeded')
    return seconds
