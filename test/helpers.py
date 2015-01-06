import contextlib

from nose.tools import assert_equal, assert_false, assert_raises, assert_true
from mock import call, patch, MagicMock

@contextlib.contextmanager
def fake_timeout_fail(*args, **kwargs):
    from unicornherder.timeout import TimeoutError
    raise TimeoutError()
