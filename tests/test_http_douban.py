from contextlib import redirect_stdout
from io import StringIO
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import peewee
import requests

from douban_album_catcher import http_douban as http
from douban_album_catcher.data_model import Album, Image, db, init_database
from douban_album_catcher.download_policy import DownloadPolicy


JPEG = b'\xff\xd8\xffphoto'
WEBP = b'RIFF\x04\x00\x00\x00WEBP'


def response(text='', status=200, *, content=None, headers=None, url='https://example.com/photo'):
    return SimpleNamespace(
        text=text, status_code=status, headers=headers or {}, url=url,
        content=text.encode() if content is None else content,
    )


class HttpTests(unittest.TestCase):
    def test_image_download_supplies_douban_referer(self):
        image = Image(id=1, name='p2684343468.webp', order_index=1,
                      url_raw='https://img3.doubanio.com/view/photo/l/public/p2684343468.webp')

        def image_server(url, *, headers, timeout):
            # 复现图片服务器：无豆瓣 Referer 则返回 418。
            allowed = headers.get('Referer') == 'https://www.douban.com/'
            return SimpleNamespace(status_code=200 if allowed else 418,
                                   content=WEBP, headers={'Content-Type': 'image/webp'})

        original_headers = http.headers.copy()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(http.requests, 'get', side_effect=image_server), patch.object(Image, 'update') as update:
                update.return_value.where.return_value.execute.return_value = 1
                self.assertTrue(http.download_image(image, directory))
                self.assertEqual((Path(directory) / '0001-p2684343468.webp').read_bytes(), WEBP)
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
            with patch.object(http.requests, 'get', return_value=response(content=JPEG)), patch.object(Image, 'update') as update:
                update.return_value.where.return_value.execute.return_value = 1
                self.assertTrue(http.download_image(image, directory))
                update.assert_called_once_with(file_size=len(JPEG))
                self.assertEqual((Path(directory) / '0001-test.jpg').read_bytes(), JPEG)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        init_database(str(self.directory / 'album.db'))
        self.addCleanup(db.close)
        album = Album.create(title='test', url='https://example.com/album',
                             description='', count=1, page_total=1, page_size=1)
        self.image = Image.create(
            album_id=album.id, name='photo.jpg', order_index=1, note='', url_large='',
            url_raw='https://img2.doubanio.com/r/photo.jpg?token=private',
            url_normal='https://img2.doubanio.com/m/photo.jpg',
        )
        self.path = self.directory / '0001-photo.jpg'
        self.wait = Mock()

    def download(self, interval=3):
        return http.download_image(
            self.image, self.directory, policy=DownloadPolicy(retry_interval_seconds=interval),
            wait=self.wait,
        )

    def test_first_working_cdn_wins_and_waits_only_between_requests(self):
        with patch.object(http.requests, 'get', side_effect=[
            response(status=404), requests.Timeout(), response(content=JPEG),
        ]) as get:
            self.assertTrue(self.download())
        self.assertEqual([request.args[0] for request in get.call_args_list], [
            f'https://img{number}.doubanio.com/r/photo.jpg?token=private' for number in (2, 1, 3)
        ])
        self.assertEqual(self.wait.call_args_list, [call(3), call(3)])
        self.assertEqual(self.path.read_bytes(), JPEG)
        self.assertEqual(Image.get_by_id(self.image.id).file_size, len(JPEG))

    def test_normal_fallback_after_bad_responses_and_reuse_saved_file(self):
        with patch.object(http.requests, 'get', side_effect=[
            response(), response('<html>error</html>', headers={'Content-Type': 'image/jpeg'}),
            response(content=b'unknown'), response(status=503), response(content=WEBP),
        ]) as get, redirect_stdout(StringIO()) as log:
            self.assertTrue(self.download(0))
            self.assertFalse(self.download())
        self.assertEqual(get.call_count, 5)
        self.assertEqual(get.call_args.args[0], self.image.url_normal)
        self.wait.assert_not_called()
        self.assertIn('普通图回退', log.getvalue())
        self.assertEqual(self.path.read_bytes(), WEBP)

    def test_all_candidates_fail_and_preserve_existing_file_and_size(self):
        self.path.write_bytes(b'old')
        self.image.file_size = 99
        self.image.save()
        with patch.object(http.requests, 'get', return_value=response(status=410)) as get:
            with self.assertRaisesRegex(http.ImageDownloadError, '候选已用尽') as caught:
                self.download()
        self.assertEqual(get.call_count, 5)
        self.assertEqual([attempt.status_code for attempt in caught.exception.attempts], [410] * 5)
        self.assertNotIn('private', str(caught.exception))
        self.assertEqual(self.wait.call_count, 4)
        self.assertEqual(self.path.read_bytes(), b'old')
        self.assertEqual(Image.get_by_id(self.image.id).file_size, 99)

    def test_invalid_sources_fail_without_network(self):
        self.image.url_raw = 'https:///missing-host'
        self.image.url_normal = ''
        with patch.object(http.requests, 'get') as get:
            with self.assertRaisesRegex(http.ImageDownloadError, '没有有效'):
                self.download()
        get.assert_not_called()
        self.wait.assert_not_called()

    def test_access_challenges_and_rate_limit_stop_without_wait(self):
        for result, expected in [
            (response(status=401), http.ImageDownloadError),
            (response(status=403), http.ImageDownloadError),
            (response(status=418), http.ImageDownloadError),
            (response('<input name="captcha-solution">'), http.ImageDownloadError),
            (response('<form></form>', url='https://accounts.douban.com/passport/login'), http.ImageDownloadError),
            (response(status=429), http.DownloadRateLimited),
        ]:
            with self.subTest(result=result), patch.object(http.requests, 'get', return_value=result) as get:
                with self.assertRaises(expected) as caught:
                    self.download()
                self.assertIs(type(caught.exception), expected)
                get.assert_called_once()
                self.wait.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_replace_failure_cleans_temporary_file_without_retrying_http(self):
        self.path.write_bytes(b'old')
        with patch.object(http.requests, 'get', return_value=response(content=JPEG)) as get:
            with patch.object(http.os, 'replace', side_effect=OSError('disk failure')):
                with self.assertRaisesRegex(OSError, 'disk failure'):
                    self.download()
        get.assert_called_once()
        self.assertEqual(list(self.directory.glob('*.tmp')), [])
        self.assertEqual(self.path.read_bytes(), b'old')
        self.assertIsNone(Image.get_by_id(self.image.id).file_size)

    def test_database_failure_after_replace_clears_old_size_and_allows_retry(self):
        self.path.write_bytes(b'old')
        self.image.file_size = len(JPEG)
        self.image.save()
        update = Image.update

        def fail_success_size(**values):
            if values['file_size'] is not None:
                raise peewee.OperationalError('database write failed')
            return update(**values)

        with patch.object(http.requests, 'get', return_value=response(content=JPEG)) as get:
            with patch.object(Image, 'update', side_effect=fail_success_size):
                with self.assertRaisesRegex(peewee.OperationalError, 'database write failed'):
                    self.download()
            get.assert_called_once()
            self.assertEqual(self.path.read_bytes(), JPEG)
            self.assertIsNone(Image.get_by_id(self.image.id).file_size)
            self.assertTrue(self.download())
        self.assertEqual(get.call_count, 2)


if __name__ == '__main__':
    unittest.main()
