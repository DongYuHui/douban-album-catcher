import tempfile
from contextlib import closing
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from douban_album_catcher import album_service as service
from douban_album_catcher.config import AppSettings
from douban_album_catcher.data_model import Album, Image, db, init_database


class AlbumServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        init_database(str(root / 'album.db'))
        self.addCleanup(db.close)
        self.settings = AppSettings(2, root / 'album.db', root / 'images', 3, 2)
        self.album = Album(title='测试', url='https://example.com/album', description='',
                           count=3, page_total=3, page_size=1)
        self.fetch = patch.object(service, 'catch_album_info', return_value=self.album)
        self.fetch.start()
        self.addCleanup(self.fetch.stop)

    def image(self, name, order):
        return Image(name=name, album_id=self.album.id, order_index=order, note='',
                     url_normal='normal', url_large='large', url_raw=f'https://example.com/{name}')

    def run_task(self, **kwargs):
        return service.run_album_task(self.album.url, self.settings, **kwargs)

    def test_pages_commit_before_wait_and_download(self):
        events = []

        def page(album, start):
            self.assertEqual(Image.select().count(), start)
            self.assertFalse(db.in_transaction())
            events.append(f'page{start + 1}')
            return [self.image(f'{start}.jpg', start + 1)]

        def wait(seconds):
            self.assertEqual(seconds, 2)
            self.assertFalse(db.in_transaction())
            # 使用独立连接确认已经提交，而非只对当前事务可见。
            import sqlite3
            with closing(sqlite3.connect(db.database)) as connection:
                self.assertEqual(connection.execute('select count(*) from image').fetchone()[0], events.count('wait') + 1)
            events.append('wait')

        def download(image, directory):
            self.assertEqual(Image.select().count(), 3)
            events.append(f'download{image.order_index}')
            return True

        with patch.object(service, 'catch_album_page', side_effect=page), patch.object(service, 'download_image', side_effect=download):
            result = self.run_task(wait=wait)
        self.assertEqual(events, ['page1', 'wait', 'page2', 'wait', 'page3', 'download1', 'download2', 'download3'])
        self.assertEqual((result.downloaded, result.skipped, result.failures), (3, 0, ()))

    def test_failed_second_page_keeps_first_and_never_downloads(self):
        for failure in (None, [], OSError('network')):
            with self.subTest(failure=failure):
                Image.delete().execute()
                self.album.id = None
                Album.delete().execute()
                wait = Mock()
                with patch.object(service, 'catch_album_page', side_effect=[[self.image('first.jpg', 1)], failure]) as query, patch.object(service, 'download_image') as download:
                    with self.assertRaisesRegex(service.AlbumTaskError, '第 2/3 页'):
                        self.run_task(wait=wait)
                    self.assertEqual(query.call_count, 2)
                    download.assert_not_called()
                self.assertEqual(Image.select().count(), 1)
                wait.assert_called_once_with(2)

    def test_transaction_rolls_back_failed_page_and_prevents_later_pages(self):
        other = Album.create(title='other', url='https://example.com/other', description='', count=1, page_total=1, page_size=1)
        Image.create(name='conflict.jpg', album_id=other.id, order_index=1, note='', url_normal='', url_large='', url_raw='')
        pages = [[self.image('first.jpg', 1)], [self.image('partial.jpg', 2), self.image('conflict.jpg', 3)]]
        with patch.object(service, 'catch_album_page', side_effect=pages) as query, patch.object(service, 'download_image') as download:
            with self.assertRaisesRegex(service.AlbumTaskError, '其他相册'):
                self.run_task(wait=Mock())
            self.assertEqual(query.call_count, 2)
            download.assert_not_called()
        self.assertIsNone(Image.get_or_none(Image.name == 'partial.jpg'))
        self.assertIsNotNone(Image.get_or_none(Image.name == 'first.jpg'))
        self.assertEqual(Image.get(Image.name == 'conflict.jpg').album_id, other.id)

    def test_database_error_rolls_back_page(self):
        import peewee

        create = Image.create
        calls = 0

        def fail_third_create(**values):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise peewee.IntegrityError('write failed')
            return create(**values)

        with patch.object(service, 'catch_album_page', side_effect=[
            [self.image('first', 1)], [self.image('second', 2), self.image('third', 3)]
        ]), patch.object(Image, 'create', side_effect=fail_third_create), patch.object(service, 'download_image') as download:
            with self.assertRaisesRegex(service.AlbumTaskError, '第 2/3 页.*write failed'):
                self.run_task(wait=Mock())
            download.assert_not_called()
        self.assertEqual([image.name for image in Image.select()], ['first'])

    def test_interrupted_wait_retains_page_and_reports_stage(self):
        with patch.object(service, 'catch_album_page', return_value=[self.image('first.jpg', 1)]) as query, patch.object(service, 'download_image') as download:
            with self.assertRaisesRegex(service.AlbumTaskInterrupted, '第 1 页后等待'):
                self.run_task(wait=Mock(side_effect=KeyboardInterrupt))
            self.assertEqual(query.call_count, 1)
            download.assert_not_called()
        self.assertEqual(Image.select().count(), 1)

    def test_zero_interval_never_sleeps(self):
        self.settings = replace(self.settings, page_query_interval_seconds=0)
        wait = Mock()
        with patch.object(service, 'catch_album_page', side_effect=lambda album, start: [self.image(str(start), start + 1)]), patch.object(service, 'download_image', return_value=True):
            self.run_task(wait=wait)
        wait.assert_not_called()

    def test_single_page_no_wait_and_failed_downloads_are_summarized(self):
        self.album.count = 3
        self.album.page_total = 1
        self.album.page_size = 18
        wait = Mock()
        with patch.object(service, 'catch_album_page', return_value=[self.image(str(i), i) for i in range(3)]), patch.object(service, 'download_image', side_effect=[OSError('disk full'), False, True]) as download:
            result = self.run_task(wait=wait)
        self.assertEqual(download.call_count, 3)
        self.assertEqual((result.downloaded, result.skipped), (1, 1))
        self.assertEqual(result.failures, (('0', 'disk full'),))
        wait.assert_not_called()

    def test_empty_and_invalid_album(self):
        self.album.count = 0
        self.album.page_total = 0
        with patch.object(service, 'catch_album_page') as query, patch.object(service, 'download_image') as download:
            result = self.run_task(wait=Mock(side_effect=AssertionError('no wait')))
            query.assert_not_called()
            download.assert_not_called()
        self.assertEqual(result.collected, 0)
        for field, value in [('page_total', -1), ('page_size', 0), ('count', -1), ('count', None)]:
            with self.subTest(field=field, value=value):
                original = getattr(self.album, field)
                setattr(self.album, field, value)
                with self.assertRaises(service.AlbumTaskError):
                    self.run_task()
                setattr(self.album, field, original)

    def test_incomplete_unique_count_blocks_download(self):
        with patch.object(service, 'catch_album_page', return_value=[self.image('same.jpg', 1)]), patch.object(service, 'download_image') as download:
            with self.assertRaisesRegex(service.AlbumTaskError, '数据不完整'):
                self.run_task(wait=Mock())
            download.assert_not_called()

    def test_updates_metadata_preserves_size_and_excludes_stale_records(self):
        self.album.save()
        existing = self.image('same.jpg', 99)
        existing.file_size = 123
        existing.save()
        self.image('stale.jpg', 100).save()
        self.album.count, self.album.page_total = 2, 2
        pages = [[self.image('new.jpg', 2)], [self.image('same.jpg', 1), self.image('same.jpg', 1)]]
        seen = []
        with patch.object(service, 'catch_album_page', side_effect=pages), patch.object(service, 'download_image', side_effect=lambda image, directory: seen.append(image) or True):
            self.run_task(wait=Mock())
        self.assertEqual([image.name for image in seen], ['same.jpg', 'new.jpg'])
        self.assertEqual(seen[0].id, existing.id)
        self.assertEqual(seen[0].file_size, 123)
        self.assertEqual(Image.select().count(), 3)
        self.album.count = self.album.page_total = 1
        changed = self.image('same.jpg', 1)
        changed.url_raw = 'https://example.com/changed'
        changed.note = 'updated'
        with patch.object(service, 'catch_album_page', return_value=[changed]), patch.object(service, 'download_image', return_value=True):
            self.run_task()
        saved = Image.get_by_id(existing.id)
        self.assertIsNone(saved.file_size)
        self.assertEqual(saved.note, 'updated')


if __name__ == '__main__':
    unittest.main()
