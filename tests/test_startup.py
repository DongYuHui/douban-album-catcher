import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from douban_album_catcher import main as app, http_douban as http, album_service as service
from douban_album_catcher.config import AppSettings, ConfigStore
from douban_album_catcher.data_model import Album, Image, db


class StartupTests(unittest.TestCase):
    def test_task_failure_and_interrupt_return_nonzero_and_close_database(self):
        from douban_album_catcher.album_service import AlbumTaskError, AlbumTaskInterrupted, DownloadResult

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            store = ConfigStore(root / 'config.json')
            store.save(AppSettings(2, root / 'album.db', root / 'images', 3))
            for failure, code in ((AlbumTaskError('第 2 页失败'), 1),
                                  (AlbumTaskInterrupted('等待中断'), 130)):
                with self.subTest(failure=failure), patch.object(app, 'run_album_task', side_effect=failure):
                    with self.assertRaises(SystemExit) as error:
                        app.main(['--config', str(store.path)])
                    self.assertEqual(error.exception.code, code)
                    self.assertTrue(db.is_closed())
            with patch.object(app, 'run_album_task', return_value=DownloadResult(1, 0, 0, (('bad.jpg', 'HTTP 403'),))):
                with self.assertRaises(SystemExit) as error:
                    app.main(['--config', str(store.path)])
                self.assertEqual(error.exception.code, 1)
                self.assertTrue(db.is_closed())

    def test_entry_uses_configured_paths_and_downloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            settings = AppSettings(2, root / '数据库' / 'album.db', root / '图片 文件', 1.5)
            store = ConfigStore(root / 'config.json')
            store.save(settings)
            album = Album(title='测试/旅行:照片? . ', url='https://example.com/album', description='', count=1, page_total=1, page_size=18)
            image = Image(order_index=1, name='test.jpg', note='', url_normal='', url_large='', url_raw='https://example.com/photo')

            def page(saved_album, start):
                image.album_id = saved_album.id
                return [image]

            content = b'\xff\xd8\xffimage'
            response = SimpleNamespace(
                status_code=200, content=content, headers={'Content-Length': str(len(content))}
            )
            try:
                with patch.object(service, 'catch_album_info', return_value=album), patch.object(service, 'catch_album_page', side_effect=page), patch.object(http.requests, 'get', return_value=response) as request:
                    app.main(['--config', str(store.path)])
                    self.assertEqual(db.database, str(settings.database_path))
                    db.connect()
                    self.assertEqual(Image.get(Image.name == image.name).file_size, len(content))
                    self.assertEqual((settings.download_directory / '测试旅行照片' / '0001-test.jpg').read_bytes(), content)
                    db.close()
                    app.main(['--config', str(store.path)])
                    self.assertEqual(request.call_count, 1)
            finally:
                if not db.is_closed():
                    db.close()

    def test_invalid_config_stops_before_database_or_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'bad.json'
            path.write_text('{broken', encoding='utf-8')
            with patch.object(app, 'init_database') as initialize, patch.object(service, 'catch_album_info') as fetch:
                with self.assertRaises(SystemExit) as error:
                    app.main(['--config', str(path)])
                self.assertEqual(error.exception.code, 1)
                initialize.assert_not_called()
                fetch.assert_not_called()
            self.assertEqual(path.read_text(), '{broken')


if __name__ == '__main__':
    unittest.main()
