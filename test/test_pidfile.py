import tempfile
import unittest

from unicornherder.pidfile import Pidfile, PidfileError


class TestPidfile(unittest.TestCase):

    def test_pidfile_exists(self):
        with tempfile.NamedTemporaryFile() as file:
            file.write('123\n')
            file.flush()
            assert Pidfile(file.name).pid == 123

    def test_oldbin_pidfile_exists(self):
        with tempfile.NamedTemporaryFile(suffix='.oldbin') as file:
            file.write('123\n')
            file.flush()
            assert Pidfile(file.name[:-7]).pid == 123

    def test_second_gunicorn_pidfile_exists(self):
        with tempfile.NamedTemporaryFile(suffix='.2') as file:
            file.write('123\n')
            file.flush()
            assert Pidfile(file.name[:-2]).pid == 123

    def test_pidfile_doesnt_exist(self):
        with self.assertRaises(PidfileError):
            Pidfile('file-does-not-exist').pid
