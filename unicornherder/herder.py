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
    'unicorn_rails': 'unicorn_rails -D {args}',
    'unicorn_bin': '{unicorn_bin} -D -P "{pidfile}" {args}',
    'gunicorn': 'gunicorn -D -p "{pidfile}" {args}',
    'gunicorn_django': 'gunicorn_django -D -p "{pidfile}" {args}',
    'gunicorn_bin': '{gunicorn_bin} -D -p "{pidfile}" {args}'
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

    def __init__(self, unicorn='gunicorn', unicorn_bin=None, gunicorn_bin=None,
                 pidfile=None, boot_timeout=30, overlap=120, args=''):
        """

        Creates a new Herder instance.

        unicorn      - the type of unicorn to herd; either 'unicorn' or 'gunicorn'
                       (Default: gunicorn)
        unicorn_bin  - path of specific unicorn to run
                       (Default: None)
        gunicorn_bin - path of specific gunicorn to run
                       (Default: None)
        pidfile      - path of the pidfile to write
                       (Default: gunicorn.pid or unicorn.pid depending on the value of
                        the unicorn parameter)
        boot_timeout - how long to wait for the new process to daemonize itself
        overlap      - how long to wait before killing the old unicorns when reloading
        args         - any additional arguments to pass to the unicorn executable
                       (Default: '')

        """

        self.unicorn_bin = unicorn_bin
        self.gunicorn_bin = gunicorn_bin

        if unicorn_bin:
            self.unicorn = unicorn_bin
        elif gunicorn_bin:
            self.unicorn = gunicorn_bin
        else:
            self.unicorn = unicorn
        self.pidfile = '%s.pid' % self.unicorn if pidfile is None else pidfile
        self.args = args
        self.boot_timeout = boot_timeout
        self.overlap = overlap

        try:
            if not unicorn_bin and not gunicorn_bin:
                COMMANDS[self.unicorn]
        except KeyError:
            raise HerderError('Unknown unicorn type: %s' % self.unicorn)

        self.master = None
        self.reloading = False
        self.terminating = False

    def spawn(self):
        """

        Spawn a new unicorn instance.

        Returns False if unicorn fails to daemonize, and True otherwise.

        """
        if self.unicorn in COMMANDS:
            cmd = COMMANDS[self.unicorn]
            cmd = cmd.format(pidfile=self.pidfile, args=self.args)
        elif self.unicorn_bin:
            cmd = COMMANDS['unicorn_bin']
            cmd = cmd.format(unicorn_bin=self.unicorn, pidfile=self.pidfile, args=self.args)
        elif self.gunicorn_bin:
            cmd = COMMANDS['gunicorn_bin']
            cmd = cmd.format(gunicorn_bin=self.unicorn, pidfile=self.pidfile, args=self.args)
        else:
            return False

        log.debug("Calling %s: %s", self.unicorn, cmd)

        cmd = shlex.split(cmd)
        try:
            process = subprocess.Popen(cmd)
        except OSError as e:
            if e.errno == 2:
                log.error("Command '%s' not found. Is it installed?", cmd[0])
                return False
            else:
                raise

        MANAGED_PIDS.add(process.pid)

        try:
            with timeout(self.boot_timeout):
                process.wait()
        except TimeoutError:
            log.error('%s failed to daemonize within %s seconds. Sending TERM '
                      'and exiting.', self.unicorn, self.boot_timeout)
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
            if not self._loop_inner():
                # The unicorn has died. So should we.
                log.error('%s died. Exiting.', self.unicorn.title())
                return 1
            time.sleep(2)

    def _loop_inner(self):
        old_master = self.master
        pid = self._read_pidfile()

        if pid is None:
            return False

        try:
            self.master = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return False

        if old_master is None:
            log.info('%s booted (PID %s)', self.unicorn.title(), self.master.pid)

            MANAGED_PIDS.add(self.master.pid)

        # Unicorn has forked a new master
        if old_master is not None and self.master.pid != old_master.pid:
            log.info('%s changed PID (was %s, now %s)',
                     self.unicorn.title(),
                     old_master.pid,
                     self.master.pid)

            MANAGED_PIDS.add(self.master.pid)

            if self.reloading:
                _wait_for_workers(self.overlap)
                _kill_old_master(old_master)
                self.reloading = False

            MANAGED_PIDS.remove(old_master.pid)

        return True

    def _read_pidfile(self):
        for _ in range(5):
            try:
                content = open(self.pidfile).read()
            except IOError as e:
                try:
                    log.debug('pidfile missing, checking for %s.oldbin', self.pidfile)
                    content = open(self.pidfile + ".oldbin").read()
                except IOError as e:
                    # If we are expecting unicorn to die, then this is normal, and
                    # we can just return None, thus triggering a clean exit of the
                    # Herder.
                    if self.terminating:
                        return None
                    else:
                        log.debug('Got IOError while attempting to read pidfile: %s', e)
                        log.debug('This is usually not fatal. Retrying in a moment...')
                        time.sleep(1)
                        continue
            try:
                pid = int(content)
            except ValueError as e:
                log.debug('Got ValueError while reading pidfile. Is "%s" an integer? %s',
                          content, e)
                log.debug('This is usually not fatal. Retrying in a moment...')
                time.sleep(1)
                continue

            return pid

        raise HerderError('Failed to read pidfile %s after 5 attempts, aborting!' %
                          self.pidfile)

    def _handle_signal(self, name):
        def _handler(signum, frame):
            if self.master is None:
                log.warn("Caught %s but have no tracked process.", name)
                return

            if signum in [signal.SIGINT, signal.SIGQUIT, signal.SIGTERM]:
                log.debug("Caught %s: expecting termination.", name)
                self.terminating = True

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


def _wait_for_workers(overlap):
    # TODO: do something smarter here
    time.sleep(overlap)


def _kill_old_master(process):
    """Shut down the old server gracefully.

    There's a bit of extra complexity here, because Unicorn and Gunicorn handle
    signals differently : both respond to SIGWINCH by gracefully stopping their
    workers, but while Unicorn treats SIGQUIT as a graceful shutdown and
    SIGTERM as a quick shutdown, Gunicorn reverses the meaning of these two.

    <http://unicorn.bogomips.org/SIGNALS.html>
    <http://gunicorn-docs.readthedocs.org/en/latest/signals.html>

    We get around this by sending SIGWINCH first, giving the worker processes
    some time to shut themselves down first.

    """
    log.debug("Sending WINCH to old master (PID %s)", process.pid)
    process.send_signal(signal.SIGWINCH)
    time.sleep(1)
    log.debug("Sending QUIT to old master (PID %s)", process.pid)
    process.send_signal(signal.SIGQUIT)
