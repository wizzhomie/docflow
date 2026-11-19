import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / 'tests'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# Importing the server starts a background request that pre-warms the model. Tests must
# not depend on a model being up, and CI has none, so the warm-up is off unless a test
# exercises it directly.
os.environ.setdefault('OPERATOR_WARM_MODEL', '0')
