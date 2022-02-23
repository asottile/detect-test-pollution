[![Build Status](https://dev.azure.com/asottile/asottile/_apis/build/status/asottile.detect-test-pollution?branchName=main)](https://dev.azure.com/asottile/asottile/_build/latest?definitionId=72&branchName=main)
[![Azure DevOps coverage](https://img.shields.io/azure-devops/coverage/asottile/asottile/72/main.svg)](https://dev.azure.com/asottile/asottile/_build/latest?definitionId=72&branchName=main)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/asottile/detect-test-pollution/main.svg)](https://results.pre-commit.ci/latest/github/asottile/detect-test-pollution/main)

detect-test-pollution
=====================

a tool to detect test pollution

## installation

`pip install detect-test-pollution`

## what is test pollution?

test pollution is where a test fails due to the side-effects of some other test
in the test suite.

it usually appears as a "test flake" something where the test fails
mysteriously but passes when run by itself.

a simple example of this is the following python code:

```python
k = 1

def test_k():
    assert k == 1

def test_k2():
    global k

    k = 2
    assert k == 2
```

now this example is a little bit silly, you probably wouldn't write code this
poorly but helps us demonstrate the problem here.

when run normally -- these tests pass:

```console
$ pytest -q t.py
..                                                                       [100%]
2 passed in 0.00s
```

but, if the tests were run in some other order (due to something like
[pytest-randomly] or [pytest-xdist]) then the pollution would be apparent:

```console
$ pytest -q t.py::test_k2 t.py::test_k
.F                                                                       [100%]
=================================== FAILURES ===================================
____________________________________ test_k ____________________________________

    def test_k():
>       assert k == 1
E       assert 2 == 1

t.py:4: AssertionError
=========================== short test summary info ============================
FAILED t.py::test_k - assert 2 == 1
1 failed, 1 passed in 0.03s
```

often this flake happens in a codebase with hundreds or thousands of tests
and it's difficult to track down which test is causing the global side-effects.

that's where this tool comes in handy!  it helps you find the pair of tests
which error when run in order.

[pytest-randomly]: https://github.com/pytest-dev/pytest-randomly
[pytest-xdist]: https://github.com/pytest-dev/pytest-xdist

## usage

once you have identified a failing test, you'll be able to feed it into
`detect-test-pollution` to find the causal test.

the basic mode is to run:

```bash
detect-test-pollution \
    --failing-test test.py::test_id_here \
    --tests ./tests
```

where `test.py::test_id_here` is the identifier of the failing test and
`./tests` is the directory where your testsuite lives.

if you've already narrowed down the list of testids further than that, you
can specify a `--testids-file` instead of `--tests` to speed up discovery:

```bash
detect-test-pollution \
    --failing-test test.py::test_id_here \
    --testids-file ./testids
```

you can usually get a list of testids via `pytest --collect-only -qq` (though
you'll need to strip the last two lines off since they are unrelated output).

then `detect-test-pollution` will bisect the list of tests to find the failing
one.  here's an example bisection from a [bug in pytest]

```console
$ detect-test-pollution --tests ./testing --failing-test testing/io/test_terminalwriter.py::test_should_do_markup_FORCE_COLOR
discovering all tests...
-> discovered 3140 tests!
ensuring test passes by itself...
-> OK!
ensuring test fails with test group...
-> OK!
running step 1:
- 3139 tests remaining (about 12 steps)
running step 2:
- 1570 tests remaining (about 11 steps)
running step 3:
- 785 tests remaining (about 10 steps)
running step 4:
- 393 tests remaining (about 9 steps)
running step 5:
- 197 tests remaining (about 8 steps)
running step 6:
- 99 tests remaining (about 7 steps)
running step 7:
- 50 tests remaining (about 6 steps)
running step 8:
- 25 tests remaining (about 5 steps)
running step 9:
- 12 tests remaining (about 4 steps)
running step 10:
- 6 tests remaining (about 3 steps)
running step 11:
- 3 tests remaining (about 2 steps)
double checking we found it...
-> the polluting test is: testing/test_terminal.py::TestTerminal::test_report_teststatus_explicit_markup
```

[bug in pytest]: https://github.com/pytest-dev/pytest/issues/9708

## supported test runners

at the moment only `pytest` is supported -- though in theory the tool could
be adapted to support other python test runners, or even other languages.
