# `test_generate_with_batteries` fails on Windows (`/dev/null` as output path)

## Description

`tests/test_generate.py::TestGenerate::test_generate_with_batteries` calls `generate.py` via
`subprocess` with `--output /dev/null` to discard the generated scenario. `/dev/null` only exists
on Linux/macOS, so on Windows `generate.py` fails to open the output file and exits with code 1:

```
FileNotFoundError: [Errno 2] No such file or directory: '/dev/null'
```

The test then fails with `assert 1 == 0`. CI runs on Linux, so the failure only shows up when
running the tests locally on Windows.

## Proposed solution

Write the output to a file in the test's temporary directory instead of `/dev/null`:

```python
"statistics", "--output", tmp_path / "scenario.json",
```

`tmp_path` is already a fixture argument of the test, works on all platforms and is cleaned up
by pytest.

Implemented on branch `fix/test_generate_windows`.
