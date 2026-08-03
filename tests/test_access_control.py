import os
import unittest
from unittest.mock import patch

from study_store import AccessDenied, StudyStore


class TestAccessControl(unittest.TestCase):
    def test_local_code_is_bound_to_participant(self):
        with patch.dict(
            os.environ,
            {
                "REQUIRE_ACCESS_CODE": "true",
                "EXPERIMENT_ACCESS_CODES": "P01=alpha,P02=beta",
            },
            clear=True,
        ):
            store = StudyStore()
            grant = store.authorize("alpha", "P01")
            self.assertEqual(grant.participant_id, "P01")
            with self.assertRaises(AccessDenied):
                store.authorize("alpha", "P02")

    def test_participant_id_is_trimmed_and_uppercased(self):
        with patch.dict(
            os.environ,
            {
                "REQUIRE_ACCESS_CODE": "true",
                "EXPERIMENT_ACCESS_CODES": "P21=alpha",
            },
            clear=True,
        ):
            store = StudyStore()
            grant = store.authorize("alpha", " p21 ")
            self.assertEqual(grant.participant_id, "P21")

    def test_required_mode_fails_closed_when_no_codes_exist(self):
        with patch.dict(
            os.environ,
            {"REQUIRE_ACCESS_CODE": "true"},
            clear=True,
        ):
            store = StudyStore()
            with self.assertRaises(AccessDenied):
                store.authorize("anything", "P01")

    def test_development_mode_can_run_without_configured_codes(self):
        with patch.dict(os.environ, {}, clear=True):
            store = StudyStore()
            grant = store.authorize("dev-only", "P01")
            self.assertEqual(grant.participant_id, "P01")


if __name__ == "__main__":
    unittest.main()
