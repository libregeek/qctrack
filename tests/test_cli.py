import subprocess
import sys

import pytest

from qctrack import __version__
from qctrack.cli.main import main


def test_version_flag() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0


def test_module_runs_and_prints_version() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "qctrack", "--version"],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": "src", "PATH": ""},
    )
    assert __version__ in out.stdout
