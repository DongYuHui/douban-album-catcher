import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from douban_album_catcher import config, startup
from douban_album_catcher.config import AppSettings, ConfigError, ConfigStore


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.defaults = AppSettings(2, self.root / 'data' / 'album.db', self.root / '中文 图片', 3.0)
        self.patcher = patch.object(config, 'default_settings', return_value=self.defaults)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.store = ConfigStore(self.root / 'config' / 'config.json')

    def write(self, value):
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text(json.dumps(value), encoding='utf-8')

    def test_first_run_and_round_trip(self):
        self.assertEqual(self.store.load(), self.defaults)
        self.assertFalse(self.defaults.download_directory.exists())
        settings = replace(self.defaults, request_interval_seconds=1.5)
        self.store.save(settings)
        self.assertEqual(self.store.load(), settings)
        self.assertIn('中文 图片', self.store.path.read_text(encoding='utf-8'))

    def test_missing_fields_and_home_expansion(self):
        self.write({'download_directory': '~/相册'})
        loaded = self.store.load()
        self.assertEqual(loaded.database_path, self.defaults.database_path)
        self.assertEqual(loaded.download_directory, Path.home() / '相册')

    def test_version_one_migrates_in_memory_and_save_writes_version_two(self):
        for version in (None, 1):
            with self.subTest(version=version):
                values = {'database_path': str(self.defaults.database_path),
                          'download_directory': str(self.defaults.download_directory),
                          'request_interval_seconds': 7.5}
                if version is not None:
                    values['schema_version'] = version
                self.write(values)
                original = self.store.path.read_bytes()
                settings = self.store.load()
                self.assertEqual(settings.schema_version, 2)
                self.assertEqual(settings.request_interval_seconds, 7.5)
                self.assertEqual(settings.page_query_interval_seconds, 3)
                self.assertEqual(settings.database_path, self.defaults.database_path)
                self.assertEqual(self.store.path.read_bytes(), original)
                self.store.save(settings)
                saved = json.loads(self.store.path.read_text())
                self.assertEqual(saved['schema_version'], 2)
                self.assertEqual(saved['page_query_interval_seconds'], 3)

    def test_custom_page_interval_round_trip_and_invalid_save(self):
        settings = replace(self.defaults, page_query_interval_seconds=0.25)
        self.store.save(settings)
        self.assertEqual(self.store.load(), settings)
        original = self.store.path.read_bytes()
        with self.assertRaises(ConfigError):
            self.store.save(replace(settings, page_query_interval_seconds=float('nan')))
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_invalid_files_are_preserved(self):
        invalid = [[], {'typo': 1}, {'schema_version': 3}, {'schema_version': True},
                   {'database_path': 'relative.db'}, {'download_directory': ''},
                   {'database_path': 123}, {'database_path': '\0'}]
        invalid += [{field: value} for field in ('request_interval_seconds', 'page_query_interval_seconds')
                    for value in (-1, True, '3', None, float('nan'), float('inf'), -float('inf'), 10 ** 400)]
        for value in invalid:
            with self.subTest(value=value):
                self.write(value)
                original = self.store.path.read_bytes()
                with self.assertRaises(ConfigError):
                    self.store.load()
                self.assertEqual(self.store.path.read_bytes(), original)
        for content in (b'{invalid', b'\xff'):
            self.store.path.write_bytes(content)
            with self.assertRaises(ConfigError):
                self.store.load()
            self.assertEqual(self.store.path.read_bytes(), content)

    def test_wrong_path_targets_and_parent(self):
        file = self.root / 'file'
        file.touch()
        for values in ({'database_path': str(self.root)},
                       {'download_directory': str(file)},
                       {'database_path': str(file / 'album.db')}):
            self.write(values)
            with self.assertRaises(ConfigError):
                self.store.load()

    def test_failed_save_keeps_previous_file_and_cleans_temporary(self):
        self.store.save(self.defaults)
        original = self.store.path.read_bytes()
        for target in ('os.replace', 'json.dump'):
            with self.subTest(target=target), patch(f'douban_album_catcher.config.{target}', side_effect=OSError('denied')):
                with self.assertRaises(ConfigError):
                    self.store.save(replace(self.defaults, request_interval_seconds=2))
            self.assertEqual(self.store.path.read_bytes(), original)
            self.assertEqual(list(self.store.path.parent.iterdir()), [self.store.path])
        with self.assertRaises(ConfigError):
            self.store.save(replace(self.defaults, request_interval_seconds=True))
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_existing_config_is_independent_of_cwd(self):
        self.store.save(self.defaults)
        other = self.root / 'other'
        other.mkdir()
        original = Path.cwd()
        try:
            os.chdir(other)
            with patch.object(startup, 'legacy_resources', side_effect=AssertionError('must not probe')):
                self.assertEqual(startup.load_startup_settings(self.store), self.defaults)
        finally:
            os.chdir(original)

    def test_unwritable_config_directory_does_not_create_config(self):
        with patch.object(Path, 'mkdir', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(ConfigError, '无法保存配置'):
                self.store.load()
        self.assertFalse(self.store.path.exists())

    @unittest.skipIf(os.name == 'nt', 'Windows 创建符号链接可能需要额外权限')
    def test_broken_config_symlink_is_preserved(self):
        self.store.path.parent.mkdir()
        self.store.path.symlink_to(self.root / 'missing.json')
        with self.assertRaises(ConfigError):
            self.store.load()
        self.assertTrue(self.store.path.is_symlink())

    def test_eof_during_selection_does_not_save(self):
        with patch.object(startup, 'legacy_resources', return_value=([], [self.root / 'image'])), patch('sys.stdin.isatty', return_value=True), patch.object(startup, 'default_settings', return_value=self.defaults), patch('builtins.input', side_effect=EOFError):
            with self.assertRaisesRegex(ConfigError, '输入已结束'):
                startup.load_startup_settings(self.store)
        self.assertFalse(self.store.path.exists())

    def test_legacy_selection_and_deduplication(self):
        database = self.root / 'album.db'
        database.touch()
        images = self.root / 'image'
        images.mkdir()
        self.assertEqual(startup.legacy_resources([self.root, self.root / '.']), ([database], [images]))
        with patch.object(startup, 'legacy_resources', return_value=([database], [images])), patch('sys.stdin.isatty', return_value=True), patch.object(startup, 'default_settings', return_value=self.defaults), patch('builtins.input', side_effect=['invalid', '1', '0']):
            settings = startup.load_startup_settings(self.store)
        self.assertEqual(settings.database_path, database)
        self.assertEqual(settings.download_directory, self.defaults.download_directory)
        self.assertEqual(self.store.load(), settings)
        self.assertTrue(database.exists())
        self.assertTrue(images.exists())

    def test_legacy_noninteractive_and_cancel_do_not_save(self):
        with patch.object(startup, 'legacy_resources', return_value=([self.root / 'album.db'], [])):
            with patch('sys.stdin.isatty', return_value=False):
                with self.assertRaisesRegex(ConfigError, '非交互'):
                    startup.load_startup_settings(self.store)
            self.assertFalse(self.store.path.exists())
            with patch('sys.stdin.isatty', return_value=True), patch.object(startup, 'default_settings', return_value=self.defaults), patch('builtins.input', return_value=''):
                with self.assertRaises(ConfigError):
                    startup.load_startup_settings(self.store)
            self.assertFalse(self.store.path.exists())

    def test_no_legacy_creates_defaults(self):
        with patch.object(startup, 'legacy_resources', return_value=([], [])):
            self.assertEqual(startup.load_startup_settings(self.store), self.defaults)
        self.assertTrue(self.store.path.is_file())

    def test_default_platform_paths(self):
        # 验证跨平台目录适配接口，真实 Windows 行为仍需在 Windows 实测。
        with patch.object(config, 'user_config_path', return_value=self.root / 'config') as get:
            self.assertEqual(config.default_config_path(), self.root / 'config' / 'config.json')
            get.assert_called_once_with(config.APP_NAME, appauthor=False)
        self.patcher.stop()
        with patch.object(config, 'user_data_path', return_value=self.root / 'data'), patch.object(config, 'user_pictures_path', return_value=self.root / 'pictures'):
            self.assertEqual(config.default_settings().download_directory, self.root / 'pictures' / 'DoubanAlbums')
        self.patcher.start()


if __name__ == '__main__':
    unittest.main()
