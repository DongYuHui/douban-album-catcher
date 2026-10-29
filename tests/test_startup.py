import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from douban_album_catcher import main as app, http_douban as http
from douban_album_catcher.config import AppSettings, ConfigStore
from douban_album_catcher.data_model import Album, Image, db


class StartupTests(unittest.TestCase):
    def test_entry_uses_configured_paths_and_downloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            settings = AppSettings(1, root / '数据库' / 'album.db', root / '图片 文件', 1.5)
            store = ConfigStore(root / 'config.json')
            store.save(settings)
            album = Album(title='测试', url='https://example.com/album', description='', count=1, page_total=1, page_size=18)
            image = Image(order_index=1, name='test.jpg', note='', url_normal='', url_large='', url_raw='https://example.com/photo')

            def page(saved_album, start):
                image.album_id = saved_album.id
                return [image]

            response = SimpleNamespace(status_code=200, content=b'image', headers={'Content-Length': '5'})
            try:
                with patch.object(app, 'catch_album_info', return_value=album), patch.object(app, 'catch_album_page', side_effect=page), patch.object(http.requests, 'get', return_value=response) as request:
                    app.main(['--config', str(store.path)])
                    self.assertEqual(db.database, str(settings.database_path))
                    self.assertEqual(Image.get_by_id(image.id).file_size, 5)
                    self.assertEqual((settings.download_directory / '0001-test.jpg').read_bytes(), b'image')
                    app.main(['--config', str(store.path)])
                    self.assertEqual(request.call_count, 1)
            finally:
                if not db.is_closed():
                    db.close()

    def test_invalid_config_stops_before_database_or_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'bad.json'
            path.write_text('{broken', encoding='utf-8')
            with patch.object(app, 'init_database') as initialize, patch.object(app, 'catch_album_info') as fetch:
                with self.assertRaises(SystemExit) as error:
                    app.main(['--config', str(path)])
                self.assertEqual(error.exception.code, 1)
                initialize.assert_not_called()
                fetch.assert_not_called()
            self.assertEqual(path.read_text(), '{broken')


if __name__ == '__main__':
    unittest.main()
