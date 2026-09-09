"""Run with the shipped Governance image, without pytest or shared test deps.

Both lazy candidate paths must reach schema rejection, not fail with an import
or missing schema-file error. The broader owner suite proves positive admission;
this focused check closes the image dependency boundary missed by that suite.
No credentials, network, owner mutation or test-only package installation.
"""
import unittest

from services.governance.paper_approval_scope import (
    PaperCandidateExpectation,
    PaperCandidateInvalid,
    _verify_paper_bundle_payload,
    _verify_paper_spec_payload,
)


class GovernanceRuntimeImageContract(unittest.TestCase):
    def setUp(self):
        self.expectation = PaperCandidateExpectation(
            tenant_id="tenant-dev", persona_id="synthetic-image-persona",
            capital_pool_id="synthetic-image-pool", target_id="synthetic-image-artifact",
            target_version="1.0.0", candidate_digest="sha256:" + "0" * 64,
        )

    def test_spec_lazy_validator_and_schema_are_available(self):
        with self.assertRaisesRegex(PaperCandidateInvalid, "strategy_spec failed schema validation"):
            _verify_paper_spec_payload(
                {}, {"strategy_spec": {}}, "synthetic", expectation=self.expectation,
            )

    def test_bundle_lazy_validator_and_schema_are_available(self):
        def no_owner_read(_registry_id):
            self.fail("Malformed synthetic artifact must fail before any owner read")

        with self.assertRaisesRegex(PaperCandidateInvalid, "strategy_artifact validation failed"):
            _verify_paper_bundle_payload(
                {}, {"strategy_artifact": {}}, expectation=self.expectation,
                read_entry_view=no_owner_read,
            )


if __name__ == "__main__":
    unittest.main()
