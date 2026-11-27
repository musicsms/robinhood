"""Unit tests for modules.listen.filters."""

import unittest

from modules.listen.filters import match_vn_finance, normalize


class TestNormalize(unittest.TestCase):

    def test_normalize_strips_diacritics_and_casefolds(self):
        self.assertEqual(normalize("Ngân Hàng"), "ngan hang")


class TestMatchVNFinance(unittest.TestCase):

    def test_match_hits_bank_class(self):
        matched, reasons = match_vn_finance("Full logs including Vietcombank creds")
        self.assertTrue(matched)
        self.assertTrue(any(r.startswith("bank:vietcombank") for r in reasons))

    def test_match_hits_geo_and_vn_text(self):
        matched, reasons = match_vn_finance(
            "Rò rỉ tài khoản ngân hàng Việt Nam"
        )
        self.assertTrue(matched)
        classes = {r.split(":", 1)[0] for r in reasons}
        self.assertIn("vn_text", classes)
        self.assertIn("geo", classes)

    def test_match_hits_phone_e164_spaced_and_national(self):
        self.assertTrue(match_vn_finance("contact +84 912 345 678")[0])
        self.assertTrue(match_vn_finance("call 0912345678 now")[0])

    def test_filter_rejects_substring_matches_inside_hashes(self):
        # 'acb' and 'vcb' appear inside onion hashes in deepdarkCTI; must NOT match.
        noise = (
            "pear peargxn3oki34c4savcbcfqofjjwjnnyrlrbszfv6ujlx36mhrh57did.onion "
            "acb_in_hash"
        )
        matched, reasons = match_vn_finance(noise)
        self.assertFalse(matched, f"expected no match, got reasons={reasons}")

    def test_filter_rejects_empty_and_whitespace(self):
        self.assertFalse(match_vn_finance("")[0])
        self.assertFalse(match_vn_finance("   \n  ")[0])

    def test_geo_vn_tld_boundary(self):
        # .vn at the end of a hostname DOES match
        self.assertTrue(match_vn_finance("leaks dumped at vuln.vn")[0])
        # .vn inside a longer alnum run DOES NOT (spec §3 Review Focus)
        self.assertFalse(match_vn_finance("evn.vnctl internal mirror")[0])


if __name__ == "__main__":
    unittest.main()
