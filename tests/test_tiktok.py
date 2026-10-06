import unittest
from utils.urls import extract_supported_links


class TikTokDetectionTests(unittest.TestCase):
    def test_short_domains_and_adjacent_links(self):
        links = extract_supported_links('<https://vm.tiktok.com/ZM123456/><https://vt.tiktok.com/ZM654321/>')
        self.assertEqual(len(links), 2)
        self.assertTrue(all(link.platform == 'tiktok' for link in links))

    def test_invalid_profile_path_is_not_downloaded(self):
        self.assertEqual(extract_supported_links('https://www.tiktok.com/@profile'), [])
