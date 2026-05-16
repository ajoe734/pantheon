import sys
import os
path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'lean', 'Algorithm.Python'))
sys.path.append(path)
print(sys.path)
try:
    from pantheon_algo.base import PantheonAlgoBase
    print("Success")
except ImportError as e:
    print(f"Error: {e}")
