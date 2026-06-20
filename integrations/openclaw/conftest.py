"""Make integrations/openclaw modules importable by bare name in tests
(e.g. `import persona_agent_sync`) regardless of pytest's invocation cwd."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
