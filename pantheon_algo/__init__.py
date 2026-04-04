# pantheon_algo — LEAN algorithm entry point for Pantheon signal consumption
#
# This is the ONLY place where Pantheon Python code lives inside the LEAN repo.
# Everything else (control-plane, registry, research adapters) lives in services/.
#
# Usage in a QCAlgorithm:
#
#   from pantheon_algo import PantheonAlgoBase
#
#   class MyStrategy(PantheonAlgoBase):
#       def Initialize(self):
#           super().Initialize()
#           # your strategy setup here
#
# The base class wires SignalConsumer.drain() into a scheduled event
# and handles the LEAN Object Store artifact loading bootstrap.
from .base import PantheonAlgoBase

__all__ = ["PantheonAlgoBase"]
