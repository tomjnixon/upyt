from subprocess import run

from pathlib import Path

import upyt


def test_with_mypy() -> None:
    paths = [
        str(Path(upyt.__file__).parent),  # upyt dir
        str(Path(__file__).parent),  # tests dir
    ]

    run(["mypy"] + paths, check=True)
