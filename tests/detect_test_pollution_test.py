from __future__ import annotations

import json

import pytest

import detect_test_pollution
from detect_test_pollution import _common_testpath
from detect_test_pollution import _discover_tests
from detect_test_pollution import _format_cmd
from detect_test_pollution import _parse_testids_file
from detect_test_pollution import _passed_with_testlist
from detect_test_pollution import main


def test_pytest_plugin_does_not_crash_when_not_enabled(pytester):
    res = pytester.inline_runsource(
        'def test(): pass',
        '-p', detect_test_pollution.__name__,
    )
    assert res.ret == 0


def test_pytest_plugin_outputs_testids(tmp_path, pytester):
    src = '''\
import pytest

@pytest.mark.parametrize('s', (1, 2, 3))
def test(s): pass
'''
    out = tmp_path.joinpath('testids')
    res = pytester.inline_runsource(
        src,
        '--collect-only', '-q',
        '-p', detect_test_pollution.__name__,
        # use `=` to avoid pytest's basedir detection
        f'--dtp-testids-output-file={out}',
    )
    assert res.ret == 0

    assert out.read_text() == '''\
test_pytest_plugin_outputs_testids.py::test[1]
test_pytest_plugin_outputs_testids.py::test[2]
test_pytest_plugin_outputs_testids.py::test[3]
'''


def test_pytest_plugin_inputs_testids(tmp_path, pytester):
    src = '''\
import pytest

@pytest.mark.parametrize('s', (1, 2, 3))
def test(s): pass
'''
    inp = tmp_path.joinpath('testids')
    inp.write_text('test_pytest_plugin_inputs_testids.py::test[1]')
    res = pytester.inline_runsource(
        src,
        '-p', detect_test_pollution.__name__,
        # use `=` to avoid pytest's basedir detection
        f'--dtp-testids-input-file={inp}',
    )
    assert res.ret == 0
    passed, failed, skipped = res.listoutcomes()
    assert len(passed) == 1
    assert len(failed) == 0
    assert len(skipped) == 0


def test_pytest_plugin_results_output(tmp_path, pytester):
    src = '''
def test1(): assert False
def test2(): pass
'''
    out = tmp_path.joinpath('out.json')
    res = pytester.inline_runsource(
        src,
        '-p', detect_test_pollution.__name__,
        # use `=` to avoid pytest's basedir detection
        f'--dtp-results-output-file={out}',
    )
    assert res.ret == 1

    with open(out) as f:
        contents = json.load(f)

    assert contents == {
        'test_pytest_plugin_results_output.py::test1': False,
        'test_pytest_plugin_results_output.py::test2': True,
    }


def test_pytest_plugin_results_output_error(tmp_path, pytester):
    src = '''\
import pytest

def test1(): pass

@pytest.fixture
def e(): assert False
def test2(e): pass
'''

    out = tmp_path.joinpath('out.json')
    res = pytester.inline_runsource(
        src,
        '-p', detect_test_pollution.__name__,
        # use `=` to avoid pytest's basedir detection
        f'--dtp-results-output-file={out}',
    )
    assert res.ret == 1

    with open(out) as f:
        contents = json.load(f)

    assert contents == {
        'test_pytest_plugin_results_output_error.py::test1': True,
        'test_pytest_plugin_results_output_error.py::test2': False,
    }


def test_parse_testids_file(tmp_path):
    f = tmp_path.joinpath('t.json')
    f.write_text('test.py::test1\ntest.py::test2')

    assert _parse_testids_file(f) == ['test.py::test1', 'test.py::test2']


def test_parse_testids_file_blank_line(tmp_path):
    f = tmp_path.joinpath('t.json')
    f.write_text('test.py::test1\n\ntest.py::test2')

    assert _parse_testids_file(f) == ['test.py::test1', 'test.py::test2']


def test_discover_tests(tmp_path):
    f = tmp_path.joinpath('t.py')
    f.write_text('def test_one(): pass\ndef test_two(): pass\n')

    assert _discover_tests(f) == ['t.py::test_one', 't.py::test_two']


@pytest.mark.parametrize(
    ('inputs', 'expected'),
    (
        ([], '.'),
        (['a', 'a/b'], 'a'),
        (['a', 'b'], '.'),
        (['a/b/c', 'a/b/d', 'a/b/e'], 'a/b'),
        (['a/b/c', 'a/b/c'], 'a/b/c'),
    ),
)
def test_common_testpath(inputs, expected):
    assert _common_testpath(inputs) == expected


def test_passed_with_testlist_failing(tmp_path):
    f = tmp_path.joinpath('t.py')
    f.write_text('def test1(): pass\ndef test2(): assert False\n')
    assert _passed_with_testlist(f, 't.py::test2', ['t.py::test1']) is False


def test_passed_with_testlist_passing(tmp_path):
    f = tmp_path.joinpath('t.py')
    f.write_text('def test1(): pass\ndef test2(): pass\n')
    assert _passed_with_testlist(f, 't.py::test2', ['t.py::test1']) is True


def test_format_cmd_with_tests():
    ret = _format_cmd('t.py::test1', 'this t.py', None)
    assert ret == (
        'detect-test-pollution --failing-test t.py::test1 '
        "--tests 'this t.py'"
    )


def test_format_cmd_with_testids_filename():
    ret = _format_cmd('t.py::test1', None, 't.txt')
    assert ret == (
        'detect-test-pollution --failing-test t.py::test1 '
        '--testids-filename t.txt'
    )


def test_integration_missing_failing_test(tmpdir, capsys):
    f = tmpdir.join('t.py')
    f.write('def test1(): pass')

    with tmpdir.as_cwd():
        ret = main(('--tests', str(f), '--failing-test', 't.py::test2'))
    assert ret == 1

    out, _ = capsys.readouterr()
    assert out == '''\
discovering all tests...
-> discovered 1 tests!
-> failing test was not part of discovered tests!
'''


def test_integration_test_does_not_pass_by_itself(tmpdir, capsys):
    f = tmpdir.join('t.py')
    f.write('def test1(): pass\ndef test2(): assert False')

    with tmpdir.as_cwd():
        ret = main(('--tests', str(f), '--failing-test', 't.py::test2'))
    assert ret == 1

    out, _ = capsys.readouterr()
    assert out == '''\
discovering all tests...
-> discovered 2 tests!
ensuring test passes by itself...
-> test failed! (output printed above)
'''


def test_integration_does_not_fail_with_all_tests(tmpdir, capsys):
    f = tmpdir.join('t.py')
    f.write('def test1(): pass\ndef test2(): pass')

    with tmpdir.as_cwd():
        ret = main(('--tests', str(f), '--failing-test', 't.py::test2'))
    assert ret == 1

    out, _ = capsys.readouterr()
    assert out == '''\
discovering all tests...
-> discovered 2 tests!
ensuring test passes by itself...
-> OK!
ensuring test fails with test group...
-> expected failure -- but it passed?
'''


def test_integration_finds_pollution(tmpdir, capsys):
    src = '''\
k = 1

def test_other():
    pass

def test_other2():
    pass

def test_k():
    assert k == 1

def test_k2():
    global k
    k = 2
    assert k == 2
'''
    f = tmpdir.join('t.py')
    f.write(src)

    with tmpdir.as_cwd():
        ret = main(('--tests', str(f), '--failing-test', 't.py::test_k'))
    assert ret == 0

    out, _ = capsys.readouterr()
    assert out == '''\
discovering all tests...
-> discovered 4 tests!
ensuring test passes by itself...
-> OK!
ensuring test fails with test group...
-> OK!
running step 1:
- 3 tests remaining (about 2 steps)
running step 2:
- 2 tests remaining (about 1 steps)
double checking we found it...
-> the polluting test is: t.py::test_k2
'''


def test_integration_pre_supplied_test_list(tmpdir, capsys):
    src = '''\
k = 1

def test_other():
    pass

def test_other2():
    pass

def test_k():
    assert k == 1

def test_k2():
    global k
    k = 2
    assert k == 2
'''
    testlist = tmpdir.join('testlist')
    testlist.write(
        't.py::test_k\n'
        't.py::test_k2\n'
        't.py::test_other\n',
    )
    f = tmpdir.join('t.py')
    f.write(src)

    with tmpdir.as_cwd():
        ret = main((
            '--testids-file', str(testlist),
            '--failing-test', 't.py::test_k',
        ))
    assert ret == 0

    out, _ = capsys.readouterr()
    assert out == '''\
discovering all tests...
-> pre-discovered 3 tests!
ensuring test passes by itself...
-> OK!
ensuring test fails with test group...
-> OK!
running step 1:
- 2 tests remaining (about 1 steps)
double checking we found it...
-> the polluting test is: t.py::test_k2
'''


def test_integration_fuzz(tmpdir, capsys):
    src = '''\
k = 1

def test_other():
    pass

def test_other2():
    pass

def test_k():
    assert k == 1

def test_k2():
    global k
    k = 2
    assert k == 2
'''

    f = tmpdir.join('t.py')
    f.write(src)

    with tmpdir.as_cwd():
        ret = main(('--fuzz', '--tests', str(f)))
    assert ret == 1

    out, err = capsys.readouterr()
    assert out == f'''\
discovering all tests...
-> discovered 4 tests!
run 1...
-> OK!
run 2...
-> found failing test!
try `detect-test-pollution --failing-test t.py::test_k --tests {f}`!
'''
