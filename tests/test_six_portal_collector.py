import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_six_portals.py"
SPEC = importlib.util.spec_from_file_location("run_six_portals", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SixPortalCollectorTests(unittest.TestCase):
    def test_catalog_contains_five_remaining_new_sources(self):
        names = {p["name"] for p in MODULE.JOB_PORTALS}
        self.assertTrue({
            "Jobicy", "Arbeitnow",
            "We Work Remotely", "Remote OK", "Working Nomads"
        }.issubset(names))

    def test_it_title_filter_accepts_technical_roles(self):
        self.assertTrue(MODULE.is_it("Senior Python Developer"))
        self.assertTrue(MODULE.is_it("Data Engineer", tags=["SQL", "Airflow"]))
        self.assertTrue(MODULE.is_it("QA Automation Engineer"))

    def test_it_title_filter_rejects_unrelated_roles(self):
        self.assertFalse(MODULE.is_it("Regional Sales Manager"))
        self.assertFalse(MODULE.is_it("Restaurant General Manager"))

    def test_location_filter_accepts_india_and_worldwide(self):
        self.assertTrue(MODULE.eligible("Bengaluru, India"))
        self.assertTrue(MODULE.eligible("Anywhere"))
        self.assertTrue(MODULE.eligible("Worldwide remote"))

    def test_location_filter_rejects_explicit_foreign_restrictions(self):
        self.assertFalse(MODULE.eligible("Remote - US only", remote=True))
        self.assertFalse(MODULE.eligible("Berlin, Germany", remote=False))

    def test_unrestricted_remote_board_role_is_accepted(self):
        self.assertTrue(MODULE.eligible("Remote", remote=True))


if __name__ == "__main__":
    unittest.main()
