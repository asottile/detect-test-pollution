from __future__ import annotations

import argparse
import contextlib
import json
import math
import os.path
import random
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from typing import Protocol
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest
    from types import TracebackType

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
        testids = PytestFramework()._parse_testids_file(read_option)
        items[:] = [by_id[testid] for testid in testids]
    elif write_option is not None:
        with open(write_option, 'w', encoding='UTF-8') as f:
            for item in items:
                f.write(f'{item.nodeid}\n')


class CollectResults:
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.results: dict[str, bool] = {}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when == 'call':
            self.results[report.nodeid] = report.outcome == 'passed'
        elif report.outcome == 'failed':
            self.results[report.nodeid] = False

    def pytest_terminal_summary(self, config: pytest.Config) -> None:
        with open(self.filename, 'w') as f:
            f.write(json.dumps(self.results, indent=2))

    def pytest_unconfigure(self, config: pytest.Config) -> None:
        config.pluginmanager.unregister(self)


def pytest_configure(config: pytest.Config) -> None:
    results_filename = config.getoption(RESULTS_OUTPUT_OPTION)
    if results_filename is not None:
        config.pluginmanager.register(CollectResults(results_filename))


class FrameWork(Protocol):

    def discover_tests(self, path: str) -> list[str]:
        ...

    def __enter__(self) -> FrameWork:
        ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        value: BaseException | None, traceback: TracebackType | None,
    ) -> None:
        ...

    def does_test_list_pass(
        self,
        path: str,
        test: str,
        testids: list[str],
    ) -> bool:
        ...

    def create_cmd_to_run(
        self,
        victim: str,
        cmd_tests: str | None,
        cmd_testids_filename: str | None,
    ) -> str:
        ...

    def fast_fail(
        self,
            path: str,
            testids: list[str],
            rng: random.Random,
    ) -> str:
        ...


class PytestFramework:

    def __init__(self) -> None:
        self._tempdir_manager = tempfile.TemporaryDirectory()
        self._tempdir: None | str = None

    def __enter__(self) -> PytestFramework:
        self._tempdir = self._tempdir_manager.__enter__()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        value: BaseException | None, traceback: TracebackType | None,
    ) -> None:
        self._tempdir_manager.__exit__(exception_type, value, traceback)

    def _run_pytest(self, *args: str) -> None:
        # XXX: this is potentially difficult to debug? maybe --verbose?
        subprocess.check_call(
            (sys.executable, '-mpytest', *PYTEST_OPTIONS, *args),
            stdout=subprocess.DEVNULL,
        )

    def _parse_testids_file(self, filename: str) -> list[str]:
        with open(filename) as f:
            return [line for line in f.read().splitlines() if line]

    def fast_fail(
        self,
            path: str,
            testids: list[str],
            prng: random.Random,
    ) -> str:
        """Runs tests, retruns first test that failed or empty string"""
        assert self._tempdir is not None
        testids_filename = os.path.join(self._tempdir, 'testids.txt')
        results_json = os.path.join(self._tempdir, 'results.json')

        prng.shuffle(testids)

        with open(testids_filename, 'w') as f:
            for testid in testids:
                f.write(f'{testid}\n')
        try:
            self._run_pytest(
                path,
                '--maxfail=1',
                # use `=` to avoid pytest's basedir detection
                f'{TESTIDS_INPUT_OPTION}={testids_filename}',
                f'{RESULTS_OUTPUT_OPTION}={results_json}',
            )
        except subprocess.CalledProcessError:
            with open(results_json) as f:
                contents = json.load(f)

            testids = list(contents)
            return testids[-1]
        else:
            return ''

    def discover_tests(self, path: str) -> list[str]:
        assert self._tempdir is not None
        testids_filename = os.path.join(self._tempdir, 'testids.txt')
        self._run_pytest(
            path,
            # use `=` to avoid pytest's basedir detection
            f'{TESTIDS_OUTPUT_OPTION}={testids_filename}',
            '--collect-only',
        )

        return self._parse_testids_file(testids_filename)

    def does_test_list_pass(
        self,
            path: str,
            test: str | None,
            testids: list[str],
    ) -> bool:
        assert self._tempdir is not None
        testids_filename = os.path.join(self._tempdir, 'testids.txt')

        with open(testids_filename, 'w') as f:
            for testid in testids:
                f.write(f'{testid}\n')
            f.write(f'{test}\n')

        results_json = os.path.join(self._tempdir, 'results.json')

        with contextlib.suppress(subprocess.CalledProcessError):
            self._run_pytest(
                path,
                # use `=` to avoid pytest's basedir detection
                f'{TESTIDS_INPUT_OPTION}={testids_filename}',
                f'{RESULTS_OUTPUT_OPTION}={results_json}',
            )

        with open(results_json) as f:
            contents = json.load(f)

        return contents[test]

    def create_cmd_to_run(
            self,
            victim: str,
            cmd_tests: str | None,
            cmd_testids_filename: str | None,
    ) -> str:
        args = ['detect-test-pollution', '--failing-test', victim]
        if cmd_tests is not None:
            args.extend(('--tests', cmd_tests))
        elif cmd_testids_filename is not None:
            args.extend(('--testids-filename', cmd_testids_filename))
        else:
            raise AssertionError('unreachable?')
        return shlex.join(args)


def _common_testpath(testids: list[str]) -> str:
    paths = [testid.split('::')[0] for testid in testids]
    if not paths:
        return '.'
    else:
        return os.path.commonpath(paths) or '.'


def _fuzz(
        testpath: str,
        testids: list[str],
        cmd_tests: str | None,
        cmd_testids_filename: str | None,
        framework: FrameWork,
) -> int:
    # make shuffling "deterministic"
    r = random.Random()
    r.seed(1542676187, version=2)

    i = 0
    while True:
        i += 1
        print(f'run {i}...')

        failing_test = framework.fast_fail(testpath, testids, r)
        if failing_test == '':
            print('-> OK!')
            continue
        else:
            print('-> found failing test!')

        last_test_ran = testids.index(failing_test)
        testids = testids[:last_test_ran]
        victim = failing_test

        cmd = framework.create_cmd_to_run(
            victim, cmd_tests, cmd_testids_filename,
        )
        print(f'try `{cmd}`!')
        return 1


def _bisect(
    testpath: str,
    failing_test: str,
    testids: list[str],
    framework: FrameWork,
) -> int:
    if failing_test not in testids:
        print('-> failing test was not part of discovered tests!')
        return 1

    # step 2: make sure the failing test passes on its own
    print('ensuring test passes by itself...')
    if framework.does_test_list_pass(testpath, failing_test, []):
        print('-> OK!')
    else:
        print('-> test failed! (output printed above)')
        return 1

    # we'll be bisecting testids
    testids.remove(failing_test)

    # step 3: ensure test fails
    print('ensuring test fails with test group...')
    if framework.does_test_list_pass(testpath, failing_test, testids):
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

        if framework.does_test_list_pass(testpath, failing_test, part1):
            testids = part2
        else:
            testids = part1

    # step 5: make sure it still fails
    print('double checking we found it...')
    if framework.does_test_list_pass(testpath, failing_test, testids):
        raise AssertionError('unreachable? unexpected pass? report a bug?')
    else:
        print(f'-> the polluting test is: {testids[0]}')
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()

    mutex1 = parser.add_mutually_exclusive_group(required=True)
    mutex1.add_argument(
        '--fuzz',
        action='store_true',
        help='repeatedly shuffle the test suite searching for failures',
    )
    mutex1.add_argument(
        '--failing-test',
        help=(
            'the identifier of the failing test, '
            'for example `tests/my_test.py::test_name_here`'
        ),
    )

    mutex2 = parser.add_mutually_exclusive_group(required=True)
    mutex2.add_argument(
        '--tests',
        help='where tests will be discovered from, often `--tests=tests/',
    )
    mutex2.add_argument(
        '--testids-file',
        help='optional pre-discovered test ids (one per line)',
    )
    args = parser.parse_args(argv)

    with PytestFramework() as pytest_framework:
        # step 1: discover all the tests
        print('discovering all tests...')
        if args.testids_file:
            testids = pytest_framework._parse_testids_file(args.testids_file)
            print(f'-> pre-discovered {len(testids)} tests!')
        else:
            testids = pytest_framework.discover_tests(args.tests)
            print(f'-> discovered {len(testids)} tests!')

        testpath = _common_testpath(testids)

        if args.fuzz:
            return _fuzz(
                testpath,
                testids,
                args.tests,
                args.testids_file,
                pytest_framework,
            )
        else:
            return _bisect(
                testpath,
                args.failing_test,
                testids,
                pytest_framework,
            )


if __name__ == '__main__':
    raise SystemExit(main())
