import atexit
import logging
import psutil
import shlex
import signal
import subprocess
import time

from .timeout import timeout, TimeoutError

log = logging.getLogger(__name__)

COMMANDS = {
    'unicorn': 'unicorn -D -P "{pidfile}" {args}',
    'gunicorn': 'gunicorn -D -p "{pidfile}" {args}'
}

MANAGED_PIDS = set([])


class HerderError(Exception):
    pass


class Herder(object):
    """

    The Herder class manages a single unicorn instance and its worker
    children. It has few configuration options: you simply instantiate a
    Herder, spawn unicorn with ``spawn()``, and then start a monitoring loop
    with the ``loop()`` method.

    The ``loop()`` method will exit with a status code, which by default will
    be used as the exit status of the ``unicornherder`` command line utility.

    Example::

        herder = Herder()
        if herder.spawn():
            sys.exit(herder.loop())

    """

    def __init__(self, unicorn='gunicorn', pidfile=None, args=''):
        self.unicorn = unicorn
        self.pidfile = '%s.pid' % self.unicorn if pidfile is None else pidfile
        self.args = args

        try:
            COMMANDS[self.unicorn]
        except KeyError:
            raise HerderError('Unknown unicorn type: %s' % self.unicorn)

        self.master = None
        self.reloading = False

    def spawn(self):
        """

        Spawn a new unicorn instance.

        Returns False if unicorn fails to daemonize, and True otherwise.

        """
        cmd = COMMANDS[self.unicorn]
        cmd = cmd.format(pidfile=self.pidfile, args=self.args)

        log.debug("Calling %s: %s", self.unicorn, cmd)

        process = subprocess.Popen(shlex.split(cmd))

        MANAGED_PIDS.add(process.pid)

        try:
            with timeout(30):
                process.wait()
        except TimeoutError:
            log.error('%s failed to daemonize within 30 seconds. Sending TERM '
                      'and exiting.', self.unicorn)
            if process.poll() is None:
                process.terminate()
            return False

        try:
            with timeout(5):
                self._boot_loop()
        except TimeoutError:
            log.error('%s failed to write a pidfile within 5 seconds. Sending TERM '
                      'and exiting.', self.unicorn)
            if process.poll() is None:
                process.terminate()
            return False

        # If we got this far, unicorn has daemonized, and we no longer need to
        # worry about the original process.
        MANAGED_PIDS.remove(process.pid)

        # The unicorn herder does a graceful unicorn restart on HUP
        signal.signal(signal.SIGHUP, self._handle_HUP)

        # Forward other useful signals to the currently tracked master
        # process.
        #
        # We do NOT forward SIGWINCH, because it is triggered by terminal
        # resize, leading to some *seriously* weird behaviour (resize
        # xterm, unicorn workers are killed).
        for sig in ['INT', 'QUIT', 'TERM', 'TTIN', 'TTOU', 'USR1', 'USR2']:
            signal.signal(getattr(signal, 'SIG%s' % sig),
                          self._handle_signal(sig))

        return True

    def loop(self):
        """Enter the monitoring loop"""
        while True:
            ret = self._loop_inner()
            if ret != 0:
                # The unicorn has died. So should we.
                log.error('Unicorn died. Exiting.')
                return ret
            time.sleep(2)

    def _loop_inner(self):
        old_master = self.master
        pid = self._read_pidfile()

        try:
            self.master = psutil.Process(pid)
        except (psutil.NoSuchProcess, TypeError):
            return 1

        if old_master is None:
            log.info('Unicorn booted (PID %s)',
                     self.master.pid)

            MANAGED_PIDS.add(self.master.pid)

        # Unicorn has forked a new master
        if old_master is not None and self.master.pid != old_master.pid:
            log.info('Unicorn changed PID (was %s, now %s)',
                     old_master.pid,
                     self.master.pid)

            MANAGED_PIDS.add(self.master.pid)

            if self.reloading:
                _wait_for_workers(self.master)
                _kill_old_master(old_master)
                self.reloading = False

            MANAGED_PIDS.remove(old_master.pid)

        return 0

    def _boot_loop(self):
        while True:
            if self._loop_inner() == 0:
                break
            time.sleep(1)

    def _read_pidfile(self):
        try:
            content = open(self.pidfile).read()
        except IOError:
            return None

        try:
            pid = int(content)
        except ValueError:
            return None

        return pid

    def _handle_signal(self, name):
        def _handler(signum, frame):
            if self.master is None:
                log.warn("Caught %s but have no tracked process.", name)
                return

            log.debug("Forwarding %s to PID %s", name, self.master.pid)
            self.master.send_signal(signum)

        return _handler

    def _handle_HUP(self, signum, frame):
        if self.master is None:
            log.warn("Caught HUP but have no tracked process.")
            return

        log.info("Caught HUP: gracefully restarting PID %s", self.master.pid)
        self.reloading = True
        self.master.send_signal(signal.SIGUSR2)


#
# If the unicorn herder exits abnormally, it is essential that unicorn
# dies as well. Register an atexit callback to kill off any surviving
# unicorns.
#
@atexit.register
def _emergency_slaughter():
    for pid in MANAGED_PIDS:
        try:
            proc = psutil.Process(pid)
            proc.kill()
        except:
            pass

def _wait_for_workers(process):
    # TODO: do something smarter here
    time.sleep(120)


def _kill_old_master(process):
    log.debug("Sending WINCH to old master (PID %s)", process.pid)
    process.send_signal(signal.SIGWINCH)
    time.sleep(1)
    log.debug("Sending QUIT to old master (PID %s)", process.pid)
    process.send_signal(signal.SIGQUIT)
