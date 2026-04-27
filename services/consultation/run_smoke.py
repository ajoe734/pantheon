import os
import sys
import unittest

# Ensure we can import the local modules
sys.path.insert(0, os.path.dirname(__file__))

from smoke_test import ConsultationSmokeTest

if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(ConsultationSmokeTest)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
