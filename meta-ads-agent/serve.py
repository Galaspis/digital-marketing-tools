"""Start the local API server from any working directory:
    meta-ads-agent\\.venv\\Scripts\\python meta-ads-agent\\serve.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from meta_agent.server import main  # noqa: E402

main()
