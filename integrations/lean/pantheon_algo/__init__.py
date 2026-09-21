# pantheon_algo — LEAN algorithm entry point for Pantheon signal consumption
#
# External Pantheon library for LEAN algorithms.
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
from .base import EngineReplayAlgo, PantheonAlgoBase, PersistentLeanObjectStore

__all__ = ["PantheonAlgoBase", "EngineReplayAlgo", "PersistentLeanObjectStore"]
