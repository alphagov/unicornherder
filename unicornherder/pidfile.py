import logging


log = logging.getLogger(__name__)


class PidfileError(OSError):
    pass


class Pidfile:

    def __init__(self, filename):
        self.filenames = [
            '{filename}.2'.format(filename=filename),
            filename,
            '{filename}.oldbin'.format(filename=filename)
        ]

    @property
    def pid(self):
        for filename in self.filenames:
            pid = self.try_read_pidfile(filename)
            if pid is not None:
                return pid

        raise PidfileError('Could not read pid from {filenames}'.format(filenames=self.filenames))

    def try_read_pidfile(self, filename):
        try:
            return int(open(filename).read())
        except IOError as error:
            log.debug('Got IOError while attempting to read %s: %s', filename, error)
        except ValueError as error:
            log.debug('Got ValueError while attempting to parse %s: %s', filename, error)
