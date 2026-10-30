"""Run the backend test suite without requiring tests/ to be a package.

Usage (from the backend directory, using the project venv):

    python run_tests.py

Why this exists: `tests/` has no `__init__.py`, so
`python -m unittest discover -s tests` fails with
"Start directory is not importable". This loader imports each test file
directly by path, puts `backend/` on sys.path and chdirs into it so that
`app`, `agent.*` and the `.env` file resolve correctly.
"""

import importlib.util
import os
import pathlib
import sys
import unittest

backend = pathlib.Path(__file__).resolve().parent
os.chdir(backend)
sys.path.insert(0, str(backend))

tests_dir = backend / "tests"
loader = unittest.TestLoader()
suite = unittest.TestSuite()

for path in sorted(tests_dir.glob("test_*.py")):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    suite.addTests(loader.loadTestsFromModule(module))

result = unittest.TextTestRunner(verbosity=2).run(suite)
print(
    "RAN=%d FAILURES=%d ERRORS=%d SKIPPED=%d"
    % (
        result.testsRun,
        len(result.failures),
        len(result.errors),
        len(getattr(result, "skipped", [])),
    )
)
sys.exit(0 if result.wasSuccessful() else 1)
