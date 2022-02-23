from __future__ import annotations

import argparse
import contextlib
import json
import math
import os.path
import subprocess
import sys
import tempfile
from typing import Sequence

import pytest

# pytest "plugin" until pytest has a machine-readable output

TESTIDS_INPUT_OPTION = '--dtp-testids-input-file'
TESTIDS_OUTPUT_OPTION = '--dtp-testids-output-file'
RESULTS_OUTPUT_OPTION = '--dtp-results-output-file'
PYTEST_OPTIONS = (
    '-p', __name__,
    # disable known test-randomization plugins
    '-p', 'no:randomly',
    # we don't read the output at all
    '--quiet', '--quiet',
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(TESTIDS_INPUT_OPTION)
    parser.addoption(TESTIDS_OUTPUT_OPTION)
    parser.addoption(RESULTS_OUTPUT_OPTION)


def pytest_collection_modifyitems(
        config: pytest.Config,
        items: list[pytest.Item],
) -> None:
    read_option = config.getoption(TESTIDS_INPUT_OPTION)
    write_option = config.getoption(TESTIDS_OUTPUT_OPTION)
    if read_option is not None:
        by_id = {item.nodeid: item for item in items}
        testids = _parse_testids_file(read_option)
        items[:] = [by_id[testid] for testid in testids]
    elif write_option is not None:
        with open(write_option, 'w', encoding='UTF-8') as f:
            for item in items:
                f.write(f'{item.nodeid}\n')


_PYTEST_KEY = pytest.StashKey[__name__]()


class CollectResults:
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.results: dict[str, bool] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when == 'call':
            self.results[report.nodeid] = report.outcome == 'passed'

    def pytest_terminal_summary(self, config: pytest.Config) -> None:
        with open(self.filename, 'w') as f:
            f.write(json.dumps(self.results, indent=2))


def pytest_configure(config: pytest.Config) -> None:
    results_filename = config.getoption(RESULTS_OUTPUT_OPTION)
    if results_filename is not None:
        config.stash[_PYTEST_KEY] = CollectResults(results_filename)
        config.pluginmanager.register(config.stash[_PYTEST_KEY])


def pytest_unconfigure(config: pytest.Config) -> None:
    plugin = config.stash.get(_PYTEST_KEY, None)
    if plugin is not None:
        del config.stash[_PYTEST_KEY]
        config.pluginmanager.unregister(plugin)


def _run_pytest(*args: str) -> None:
    # XXX: this is potentially difficult to debug? maybe --verbose?
    subprocess.check_call(
        (sys.executable, '-mpytest', *PYTEST_OPTIONS, *args),
        stdout=subprocess.DEVNULL,
    )


def _parse_testids_file(filename: str) -> list[str]:
    with open(filename) as f:
        return [line for line in f.read().splitlines() if line]


def _discover_tests(path: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        testids_filename = os.path.join(tmpdir, 'testids.txt')
        _run_pytest(
            path,
            # use `=` to avoid pytest's basedir detection
            f'{TESTIDS_OUTPUT_OPTION}={testids_filename}',
            '--collect-only',
        )

        return _parse_testids_file(testids_filename)


def _common_testpath(testids: list[str]) -> str:
    paths = [testid.split('::')[0] for testid in testids]
    if not paths:
        return '.'
    else:
        return os.path.commonpath(paths) or '.'


def _passed_with_testlist(path: str, test: str, testids: list[str]) -> bool:
    with tempfile.TemporaryDirectory() as tmpdir:
        testids_filename = os.path.join(tmpdir, 'testids.txt')
        with open(testids_filename, 'w') as f:
            for testid in testids:
                f.write(f'{testid}\n')
            f.write(f'{test}\n')

        results_json = os.path.join(tmpdir, 'results.json')

        with contextlib.suppress(subprocess.CalledProcessError):
            _run_pytest(
                path,
                # use `=` to avoid pytest's basedir detection
                f'{TESTIDS_INPUT_OPTION}={testids_filename}',
                f'{RESULTS_OUTPUT_OPTION}={results_json}',
            )

        with open(results_json) as f:
            contents = json.load(f)

        return contents[test]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--failing-test',
        required=True,
        help=(
            'the identifier of the failing test, '
            'for example `tests/my_test.py::test_name_here`'
        ),
    )
    mutex = parser.add_mutually_exclusive_group(required=True)
    mutex.add_argument(
        '--tests',
        help='where tests will be discovered from, often `--tests=tests/',
    )
    mutex.add_argument(
        '--testids-file',
        help='optional pre-discovered test ids (one per line)',
    )
    args = parser.parse_args(argv)

    # step 1: discover all the tests
    print('discovering all tests...')
    if args.testids_file:
        testids = _parse_testids_file(args.testids_file)
        print(f'-> pre-discovered {len(testids)} tests!')
    else:
        testids = _discover_tests(args.tests)
        print(f'-> discovered {len(testids)} tests!')

    testpath = _common_testpath(testids)

    if args.failing_test not in testids:
        print('-> failing test was not part of discovered tests!')
        return 1

    # step 2: make sure the failing test passes on its own
    print('ensuring test passes by itself...')
    if _passed_with_testlist(testpath, args.failing_test, []):
        print('-> OK!')
    else:
        print('-> test failed! (output printed above)')
        return 1

    # we'll be bisecting testids
    testids.remove(args.failing_test)

    # step 3: ensure test fails
    print('ensuring test fails with test group...')
    if _passed_with_testlist(testpath, args.failing_test, testids):
        print('-> expected failure -- but it passed?')
        return 1
    else:
        print('-> OK!')

    # step 4: bisect time!
    n = 0
    while len(testids) != 1:
        n += 1
        print(f'running step {n}:')
        n_left = len(testids)
        steps_s = f'(about {math.ceil(math.log(n_left, 2))} steps)'
        print(f'- {n_left} tests remaining {steps_s}')

        pivot = len(testids) // 2
        part1 = testids[:pivot]
        part2 = testids[pivot:]

        if _passed_with_testlist(testpath, args.failing_test, part1):
            testids = part2
        else:
            testids = part1

    # step 5: make sure it still fails
    print('double checking we found it...')
    if _passed_with_testlist(testpath, args.failing_test, testids):
        raise AssertionError('unreachable? unexpected pass? report a bug?')
    else:
        print(f'-> the polluting test is: {testids[0]}')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
