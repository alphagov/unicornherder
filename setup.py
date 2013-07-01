import os
import sys
from setuptools import setup, find_packages
from unicornherder import __version__

install_requires = [
    'psutil>=0.5.1',
]

if sys.version_info < (2, 7):
    install_requires.append('argparse')

HERE = os.path.dirname(__file__)
try:
    long_description = open(os.path.join(HERE, 'README.rst')).read()
except:
    long_description = None

setup(
    name='unicornherder',
    version=__version__,
    packages=find_packages(),

    # metadata for upload to PyPI
    author='Nick Stenning',
    author_email='nick@whiteink.com',
    maintainer='Government Digital Service',
    url='https://github.com/alphagov/unicornherder',

    description='Unicorn Herder: manage daemonized (g)unicorns',
    long_description=long_description,

    license='MIT',

    keywords='sysadmin process supervision unicorn gunicorn upstart',
    install_requires=install_requires,

    entry_points={
        'console_scripts': [
            'unicornherder = unicornherder.command:main'
        ]
    }
)
