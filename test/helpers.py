import contextlib

from nose.tools import *
from mock import *

@contextlib.contextmanager
def fake_timeout_fail(*args, **kwargs):
    from unicornherder.timeout import TimeoutError
    raise TimeoutError()
