"""Suite-wide setup.

The season scheduler starts from the app's lifespan. A TestClient that enters
the lifespan would launch a real polling loop against the real database mid-run,
so it is disabled for every test.
"""

import os

os.environ.setdefault("PIGSKIN_DISABLE_SCHEDULER", "1")
