import unittest
from urllib.parse import urlparse

from collectors.source_catalog import JOB_PORTALS, PORTAL_BY_KEY, UI_PORTAL_KEYS, REMOTE_MULTI_SOURCE_KEYS


class SourceCatalogTests(unittest.TestCase):
    def test_catalog_contains_20_portals_with_unique_keys(self):
        expected = {
            "LinkedIn", "Glassdoor", "Naukri", "Indeed", "Foundit",
            "Internshala", "TimesJobs", "Wellfound", "CutShort", "Hirist",
            "Instahyre", "Freshersworld", "Unstop", "Bayt", "Shine",
            "Jobicy", "Arbeitnow", "We Work Remotely",
            "Remote OK", "Working Nomads",
        }
        self.assertEqual(len(JOB_PORTALS), 20)
        self.assertEqual({portal["name"] for portal in JOB_PORTALS}, expected)
        self.assertEqual(len({portal["key"] for portal in JOB_PORTALS}), 20)
        self.assertEqual(len({portal["name"].lower() for portal in JOB_PORTALS}), 20)

    def test_all_catalogued_portals_are_in_ui_allowlist(self):
        self.assertEqual(UI_PORTAL_KEYS, {portal["key"] for portal in JOB_PORTALS})

    def test_remote_boards_use_shared_multi_board_collector(self):
        self.assertEqual(REMOTE_MULTI_SOURCE_KEYS, {
            "JOBICY", "ARBEITNOW", "WE_WORK_REMOTELY", "REMOTE_OK", "WORKING_NOMADS"
        })
        self.assertTrue(REMOTE_MULTI_SOURCE_KEYS.issubset(UI_PORTAL_KEYS))

    def test_all_portals_have_valid_https_homepages_and_keys(self):
        for portal in JOB_PORTALS:
            with self.subTest(portal=portal["name"]):
                parsed = urlparse(portal["base_url"])
                self.assertEqual(parsed.scheme, "https")
                self.assertTrue(parsed.netloc)
                self.assertIn(portal["key"].lower(), PORTAL_BY_KEY)

    def test_popular_portals_are_ordered_first(self):
        self.assertEqual(
            [portal["name"] for portal in JOB_PORTALS[:4]],
            ["Naukri", "Indeed", "Foundit", "TimesJobs"],
        )
        self.assertTrue(all(portal["popular"] for portal in JOB_PORTALS[:4]))
        self.assertFalse(any(portal["popular"] for portal in JOB_PORTALS[4:]))


if __name__ == "__main__":
    unittest.main()
