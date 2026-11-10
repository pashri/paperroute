"""Where the repository and its test fixtures are."""

from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).parents[4]
FIXTURES: Final[Path] = ROOT / 'tests' / 'fixtures'
