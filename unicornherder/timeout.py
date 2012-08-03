import contextlib
import signal


class TimeoutError(Exception):
    pass


@contextlib.contextmanager
def timeout(time=30):
    def _fail(signal, frame):
        raise TimeoutError("%s second timeout expired" % time)

    signal.signal(signal.SIGALRM, _fail)
    signal.alarm(time)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
