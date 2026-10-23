"""Start the local API server from any working directory:
    social-agent\\.venv\\Scripts\\python social-agent\\serve.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from social_agent.server import main  # noqa: E402

main()
