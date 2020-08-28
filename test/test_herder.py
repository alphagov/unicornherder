import signal
import sys
from .helpers import *
from unicornherder.herder import Herder, HerderError

if sys.version_info > (3, 0):
    builtin_mod = 'builtins'
else:
    builtin_mod = '__builtin__'


class TestHerder(object):

    def test_init_defaults(self):
        h = Herder()
        assert_equal(h.unicorn, 'gunicorn')
        assert_equal(h.pidfile, 'gunicorn.pid')
        assert_equal(h.args, '')

    def test_init_unicorn(self):
        h = Herder(unicorn='unicorn')
        assert_equal(h.unicorn, 'unicorn')

    def test_init_gunicorn(self):
        h = Herder(unicorn='gunicorn')
        assert_equal(h.unicorn, 'gunicorn')

    def test_init_unicornbad(self):
        assert_raises(HerderError, Herder, unicorn='unicornbad')

    @patch('unicornherder.herder.subprocess.Popen')
    def test_spawn_returns_true(self, popen_mock):
        h = Herder()
        h._boot_loop = lambda: True
        assert_true(h.spawn())

    @patch('unicornherder.herder.subprocess.Popen')
    def test_spawn_gunicorn(self, popen_mock):
        h = Herder(unicorn='gunicorn')
        h._boot_loop = lambda: True
        h.spawn()
        assert_equal(popen_mock.call_count, 1)
        popen_mock.assert_called_once_with(['gunicorn', '-D', '-p', 'gunicorn.pid'])

    @patch('unicornherder.herder.subprocess.Popen')
    def test_spawn_unicorn(self, popen_mock):
        h = Herder(unicorn='unicorn')
        h._boot_loop = lambda: True
        h.spawn()
        assert_equal(popen_mock.call_count, 1)
        popen_mock.assert_called_once_with(['unicorn', '-D', '-P', 'unicorn.pid'])

    @patch('unicornherder.herder.subprocess.Popen')
    @patch('unicornherder.herder.timeout')
    def test_spawn_unicorn_timeout(self, timeout_mock, popen_mock):
        popen_mock.return_value.pid = -1
        timeout_mock.side_effect = fake_timeout_fail
        h = Herder()
        popen_mock.return_value.poll.return_value = None
        ret = h.spawn()
        assert_false(ret)
        popen_mock.return_value.terminate.assert_called_once_with()

    @patch('unicornherder.herder.subprocess.Popen')
    @patch('unicornherder.herder.timeout')
    def test_configurable_boot_timeout(self, timeout_mock, popen_mock):
        popen_mock.return_value.pid = -1
        timeout_mock.side_effect = fake_timeout_fail
        h = Herder(boot_timeout=45)
        popen_mock.return_value.poll.return_value = None
        ret = h.spawn()
        timeout_mock.assert_called_once_with(45)
        assert_false(ret)
        popen_mock.return_value.terminate.assert_called_once_with()

    @patch('unicornherder.herder.time.sleep')
    @patch('unicornherder.herder.psutil.Process')
    @patch('%s.open' % builtin_mod)
    def test_configurable_overlap(self, open_mock, process_mock, sleep_mock):
        h = Herder(overlap=17)

        # Set up an initial dummy master process for the herder to kill later
        open_mock.return_value.read.return_value = '123\n'
        process = MagicMock(pid=123)
        process.children.return_value = ["forked process", "worker 1"]
        process_mock.return_value = process
        h._loop_inner()

        # Simulate a reloaded Unicorn
        open_mock.return_value.read.return_value = '456\n'
        process = MagicMock(pid=456)
        process.children.return_value = ["worker 1"]
        process_mock.return_value = process

        # Simulate SIGHUP, so the Herder thinks it's reloading
        h._handle_HUP(signal.SIGHUP, None)

        h._loop_inner()
        sleep_mock.assert_any_call(17)


    @patch('unicornherder.herder.time.sleep')
    @patch('unicornherder.herder.psutil.Process')
    @patch('%s.open' % builtin_mod)
    def test_waits_for_workers(self, open_mock, process_mock, sleep_mock):
        open_mock.return_value.read.return_value = '123\n'
        h = Herder()

        # Set up an initial dummy master process for the herder to kill later
        open_mock.return_value.read.return_value = '123\n'
        process = MagicMock(pid=123)
        process.children.return_value = ["forked process", "worker 1", "worker 2"]
        process_mock.return_value = process
        h._loop_inner()

        # Simulate a reloaded Unicorn
        open_mock.return_value.read.return_value = '456\n'
        process = MagicMock(pid=456)
        # First call returns an empty list, the second returns 2 workers
        process.children.side_effect = [[], ["worker 1", "worker 2"]]
        process_mock.return_value = process

        # Simulate SIGHUP, so the Herder thinks it's reloading
        h._handle_HUP(signal.SIGHUP, None)

        h._loop_inner()

        # while waiting for workers
        sleep_mock.asset_any_call(1)
        # overlap of both processes
        sleep_mock.asset_any_call(30)

    @patch('unicornherder.herder.time.sleep')
    @patch('unicornherder.herder.timeout')
    @patch('unicornherder.herder.psutil.Process')
    @patch('%s.open' % builtin_mod)
    def test_recovers_from_less_workers(self, open_mock, process_mock, timeout_mock, sleep_mock):
        timeout_mock.side_effect = fake_timeout_fail
        h = Herder()

        # Set up an initial dummy master process for the herder to kill later
        open_mock.return_value.read.return_value = '123\n'
        old_process = MagicMock(pid=123)
        old_process.children.return_value = ["forked process", "worker 1", "worker 2"]
        process_mock.return_value = old_process
        h._loop_inner()

        # Simulate a reloaded Unicorn
        open_mock.return_value.read.return_value = '456\n'
        new_process = MagicMock(pid=456)
        new_process.children.return_value = ["worker 1"]
        process_mock.return_value = new_process

        # Simulate SIGHUP, so the Herder thinks it's reloading
        h._handle_HUP(signal.SIGHUP, None)

        h._loop_inner()
        old_process.send_signal.assert_called_with(signal.SIGQUIT)

    @patch('unicornherder.herder.time.sleep')
    @patch('unicornherder.herder.psutil.Process')
    @patch('%s.open' % builtin_mod)
    def test_loop_valid_pid(self, open_mock, process_mock, sleep_mock):
        open_mock.return_value.read.return_value = '123\n'
        h = Herder()
        ret = h._loop_inner()
        assert_equal(ret, True)
        process_mock.assert_called_once_with(123)

    @patch('unicornherder.herder.time.sleep')
    @patch('%s.open' % builtin_mod)
    def test_loop_invalid_pid(self, open_mock, sleep_mock):
        open_mock.return_value.read.return_value = 'foobar'
        h = Herder()
        assert_raises(HerderError, h._loop_inner)

    @patch('unicornherder.herder.time.sleep')
    @patch('%s.open' % builtin_mod)
    def test_loop_nonexistent_pidfile(self, open_mock, sleep_mock):
        def _fail():
            raise IOError()
        open_mock.return_value.read.side_effect = _fail
        h = Herder()
        assert_raises(HerderError, h._loop_inner)

    @patch('unicornherder.herder.time.sleep')
    @patch('%s.open' % builtin_mod)
    def test_loop_nonexistent_pidfile_terminating(self, open_mock, sleep_mock):
        def _fail():
            raise IOError()
        open_mock.return_value.read.side_effect = _fail
        h = Herder()
        h.terminating = True
        assert_equal(h._loop_inner(), False)

    @patch('unicornherder.herder.time.sleep')
    @patch('unicornherder.herder.psutil.Process')
    @patch('%s.open' % builtin_mod)
    def test_loop_detects_pidchange(self, open_mock, process_mock, sleep_mock):
        proc1 = MagicMock()
        proc2 = MagicMock()
        proc1.pid = 123
        proc2.pid = 456

        h = Herder()

        open_mock.return_value.read.return_value = '123\n'
        process_mock.return_value = proc1
        ret = h._loop_inner()
        assert_equal(ret, True)

        open_mock.return_value.read.return_value = '456\n'
        process_mock.return_value = proc2
        ret = h._loop_inner()
        assert_equal(ret, True)

        expected_calls = []
        assert_equal(proc1.mock_calls, expected_calls)

    @patch('unicornherder.herder.time.sleep')
    @patch('unicornherder.herder.psutil.Process')
    @patch('%s.open' % builtin_mod)
    def test_loop_reload_pidchange_signals(self, open_mock, process_mock, sleep_mock):
        proc1 = MagicMock()
        proc2 = MagicMock()
        proc1.pid = 123
        proc2.pid = 456
        proc1.children = MagicMock(return_value=[proc2, "worker"])
        proc2.children = MagicMock(return_value=["worker 1"])

        h = Herder()

        open_mock.return_value.read.return_value = '123\n'
        process_mock.return_value = proc1
        ret = h._loop_inner()
        assert_equal(ret, True)

        # Simulate SIGHUP
        h._handle_HUP(signal.SIGHUP, None)

        open_mock.return_value.read.return_value = '456\n'
        process_mock.return_value = proc2
        ret = h._loop_inner()
        assert_equal(ret, True)

        expected_calls = [call.send_signal(signal.SIGUSR2),
                          call.children(),
                          call.send_signal(signal.SIGWINCH),
                          call.send_signal(signal.SIGQUIT)]
        assert_equal(proc1.mock_calls, expected_calls)

    def test_forward_signal(self):
        h = Herder()
        h.master = MagicMock()

        h._handle_signal('INT')(signal.SIGINT, None)
        h.master.send_signal.assert_called_once_with(signal.SIGINT)

    def test_forward_signal_nomaster(self):
        h = Herder()
        h._handle_signal('INT')(signal.SIGINT, None)

    def test_handle_hup_nomaster(self):
        h = Herder()
        h._handle_HUP(signal.SIGHUP, None)
