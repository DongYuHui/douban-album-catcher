import unittest

from douban_album_catcher.download_policy import DownloadPolicy


class DownloadPolicyTests(unittest.TestCase):
    def test_cdn_order_preserves_path_and_query_and_deduplicates_normal(self):
        raw = 'https://IMG2.doubanio.com/r/photo.webp?img2.doubanio.com=x'
        normal = 'https://img3.doubanio.com/r/photo.webp?img2.doubanio.com=x'
        candidates = DownloadPolicy().candidates(raw, normal)
        self.assertEqual([(item.url, item.source) for item in candidates], [
            (raw, 'raw'),
            ('https://img1.doubanio.com/r/photo.webp?img2.doubanio.com=x', 'raw_cdn'),
            (normal, 'raw_cdn'),
            ('https://img9.doubanio.com/r/photo.webp?img2.doubanio.com=x', 'raw_cdn'),
        ])

    def test_invalid_and_non_cdn_urls(self):
        policy = DownloadPolicy()
        normal = 'https://example.com/m/photo.jpg'
        for raw in (None, '', 'ftp://example.com/photo', 'https:///photo', 'https://[bad', 'https://example.com:bad'):
            with self.subTest(raw=raw):
                self.assertEqual([(c.url, c.source) for c in policy.candidates(raw, normal)], [(normal, 'normal')])
        raw = 'https://img2.doubanio.com.example.org/r/photo.jpg'
        self.assertEqual([c.url for c in policy.candidates(raw, normal)], [raw, normal])
        self.assertEqual(policy.candidates(None, '/relative.jpg'), ())
