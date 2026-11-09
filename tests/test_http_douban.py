import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from douban_album_catcher import http_douban as http
from douban_album_catcher.data_model import Image


def response(text='', status=200, **kwargs):
    return SimpleNamespace(text=text, status_code=status, headers={}, url='https://example.com/photo', **kwargs)


class HttpTests(unittest.TestCase):
    def test_image_download_supplies_douban_referer(self):
        image = Image(id=1, name='p2684343468.webp', order_index=1,
                      url_raw='https://img3.doubanio.com/view/photo/l/public/p2684343468.webp')

        def image_server(url, *, headers, timeout):
            # 复现图片服务器：无豆瓣 Referer 则返回 418。
            allowed = headers.get('Referer') == 'https://www.douban.com/'
            return SimpleNamespace(status_code=200 if allowed else 418,
                                   content=b'webp', headers={'Content-Type': 'image/webp'})

        original_headers = http.headers.copy()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(http.requests, 'get', side_effect=image_server), patch.object(Image, 'update') as update:
                update.return_value.where.return_value.execute.return_value = 1
                self.assertTrue(http.download_image(image, directory))
                self.assertEqual((Path(directory) / '0001-p2684343468.webp').read_bytes(), b'webp')
        self.assertEqual(http.headers, original_headers)

    def test_detail_missing_note_is_empty_but_interception_fails(self):
        for text in ('<div id="content"><div class="photo-show"></div></div>',
                     '<div id="content"><img id="image"><div class="photo_descri">说明</div></div>'):
            with patch.object(http.requests, 'get', return_value=response(text)):
                self.assertEqual(http.catch_image_note('https://example.com/photo'), '说明' if '说明' in text else '')
        for result in (response('<form>login</form>'), response(status=403),
                       response('<input name="captcha-solution">')):
            with patch.object(http.requests, 'get', return_value=result):
                with self.assertRaises(http.DoubanResponseError):
                    http.catch_image_note('https://example.com/photo')

    def test_page_structure_and_details(self):
        album = SimpleNamespace(id=1, url='https://example.com/album')
        page = '<div class="photolst"><a class="photolst_photo" href="/photo"><img src="https://example.com/m/test.jpg"></a></div>'
        detail = '<div id="content"><img id="image"></div>'
        with patch.object(http.requests, 'get', side_effect=[response(page), response(detail)]):
            images = http.catch_album_page(album, 18)
        self.assertEqual(images[0].order_index, 19)
        self.assertEqual(images[0].url_raw, 'https://example.com/r/test.jpg')
        for page in ('<html>login</html>', '<div class="photolst"><a class="photolst_photo"></a></div>'):
            with patch.object(http.requests, 'get', return_value=response(page)):
                with self.assertRaises(http.DoubanResponseError):
                    http.catch_album_page(album, 0)
        with patch.object(http.requests, 'get', side_effect=[response('<div class="photolst"><a class="photolst_photo" href="/photo"><img src="a.jpg"></a></div>'), response(status=429)]):
            with self.assertRaises(http.DoubanResponseError):
                http.catch_album_page(album, 0)

    def test_album_structure_and_empty_album(self):
        html = '<title>测试</title><div class="photitle"><span>0张照片</span></div>'
        with patch.object(http.requests, 'get', return_value=response(html)):
            album = http.catch_album_info('https://example.com/album')
        self.assertEqual((album.count, album.page_total), (0, 0))
        with patch.object(http.requests, 'get', return_value=response('<title>验证</title>')):
            with self.assertRaises(http.DoubanResponseError):
                http.catch_album_info('https://example.com/album')

    def test_bad_http_does_not_overwrite_file_or_update_database(self):
        image = Image(id=1, name='test.jpg', order_index=1, url_raw='https://example.com/image')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '0001-test.jpg'
            path.write_bytes(b'original')
            with patch.object(http.requests, 'get', return_value=response(status=403)), patch.object(Image, 'update') as update:
                with self.assertRaises(http.DoubanResponseError):
                    http.download_image(image, directory)
                update.assert_not_called()
            self.assertEqual(path.read_bytes(), b'original')

    def test_download_without_content_length_uses_received_size(self):
        image = Image(id=1, name='test.jpg', order_index=1, url_raw='https://example.com/image')
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(http.requests, 'get', return_value=response(content=b'photo')), patch.object(Image, 'update') as update:
                update.return_value.where.return_value.execute.return_value = 1
                self.assertTrue(http.download_image(image, directory))
                update.assert_called_once_with(file_size=5)
                self.assertEqual((Path(directory) / '0001-test.jpg').read_bytes(), b'photo')


if __name__ == '__main__':
    unittest.main()
