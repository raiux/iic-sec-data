"""Run the complete integrated suite, fail on skips and reject socket network access."""
import sys
import unittest


def deny_network(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'socket.sendto'}:
        raise AssertionError('Network access forbidden in offline test suite')


def main():
    sys.addaudithook(deny_network)
    suite = unittest.defaultTestLoader.discover('tests', pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print('Integrated suite must not skip tests', file=sys.stderr)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == '__main__':
    raise SystemExit(main())
