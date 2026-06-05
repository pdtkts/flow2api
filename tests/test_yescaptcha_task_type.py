import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.core.config import (
    Config,
    get_runtime_data_dir,
    get_runtime_tmp_dir,
    get_yescaptcha_min_score,
    normalize_yescaptcha_task_type,
)
from src.core.database import Database


class YesCaptchaTaskTypeTests(unittest.TestCase):
    def test_supported_task_types_are_preserved(self):
        self.assertEqual(
            normalize_yescaptcha_task_type("RecaptchaV3TaskProxyless"),
            "RecaptchaV3TaskProxyless",
        )
        self.assertEqual(
            normalize_yescaptcha_task_type("RecaptchaV3TaskProxylessM1S9"),
            "RecaptchaV3TaskProxylessM1S9",
        )

    def test_unknown_task_type_falls_back_to_m1(self):
        self.assertEqual(
            normalize_yescaptcha_task_type("bad-type"),
            "RecaptchaV3TaskProxylessM1",
        )

    def test_s7_s9_force_expected_min_score(self):
        self.assertEqual(get_yescaptcha_min_score("RecaptchaV3TaskProxylessM1S7"), 0.7)
        self.assertEqual(get_yescaptcha_min_score("RecaptchaV3TaskProxylessM1S9"), 0.9)
        self.assertIsNone(get_yescaptcha_min_score("RecaptchaV3TaskProxylessM1"))


class RailwayRuntimeConfigTests(unittest.TestCase):
    def test_port_env_overrides_config_port(self):
        with patch.dict(os.environ, {"PORT": "4567"}, clear=False):
            self.assertEqual(Config().server_port, 4567)

    def test_invalid_port_env_falls_back_to_config_port(self):
        with patch.dict(os.environ, {"PORT": "not-a-port"}, clear=False):
            self.assertEqual(Config().server_port, 8000)

    def test_railway_volume_mount_sets_runtime_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"RAILWAY_VOLUME_MOUNT_PATH": tmp}, clear=False):
                self.assertEqual(get_runtime_data_dir(), Path(tmp) / "data")
                self.assertEqual(get_runtime_tmp_dir(), Path(tmp) / "tmp")

    def test_database_default_path_uses_railway_volume(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"RAILWAY_VOLUME_MOUNT_PATH": tmp}, clear=False):
                db = Database()
                self.assertEqual(Path(db.db_path), Path(tmp) / "data" / "flow.db")
                self.assertTrue((Path(tmp) / "data").is_dir())

    def test_debug_env_overrides_config_values(self):
        env = {
            "FLOW2API_DEBUG_ENABLED": "true",
            "FLOW2API_DEBUG_LOG_REQUESTS": "false",
            "FLOW2API_DEBUG_LOG_RESPONSES": "0",
            "FLOW2API_DEBUG_MASK_TOKEN": "yes",
            "FLOW2API_DEBUG_RECAPTCHA_TRACE": "on",
            "FLOW2API_DEBUG_RECAPTCHA_CONSOLE": "off",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = Config()
            self.assertTrue(cfg.debug_enabled)
            self.assertFalse(cfg.debug_log_requests)
            self.assertFalse(cfg.debug_log_responses)
            self.assertTrue(cfg.debug_mask_token)
            self.assertTrue(cfg.debug_recaptcha_trace)
            self.assertFalse(cfg.debug_recaptcha_console)

    def test_debug_env_wins_after_runtime_db_reload(self):
        with patch.dict(os.environ, {"FLOW2API_DEBUG_ENABLED": "true"}, clear=False):
            cfg = Config()
            cfg.set_debug_enabled(False)
            self.assertTrue(cfg.debug_enabled)

    def test_invalid_debug_env_bool_raises(self):
        with patch.dict(os.environ, {"FLOW2API_DEBUG_ENABLED": "maybe"}, clear=False):
            with self.assertRaises(ValueError):
                Config()

    def test_admin_env_wins_after_runtime_db_reload(self):
        env = {
            "FLOW2API_ADMIN_USERNAME": "railadmin",
            "FLOW2API_ADMIN_PASSWORD": "rail-secret",
            "FLOW2API_API_KEY": "rail-api-key",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = Config()
            cfg.set_admin_username_from_db("admin")
            cfg.set_admin_password_from_db("admin")
            cfg.api_key = "db-api-key"

            self.assertEqual(cfg.admin_username, "railadmin")
            self.assertEqual(cfg.admin_password, "rail-secret")
            self.assertEqual(cfg.api_key, "rail-api-key")

    def test_cache_base_url_env_wins_after_runtime_db_reload(self):
        with patch.dict(
            os.environ,
            {"FLOW2API_CACHE_BASE_URL": "https://flow-api.prismacreative.online/"},
            clear=False,
        ):
            cfg = Config()
            cfg.set_cache_base_url("https://admin-flow.prismacreative.online")
            self.assertEqual(cfg.cache_base_url, "https://flow-api.prismacreative.online")


class ApiOnlyHostRouteTests(unittest.TestCase):
    def test_api_only_host_allows_public_api_cache_and_extension_routes(self):
        from src.main import _path_allowed_on_api_only_host

        self.assertTrue(_path_allowed_on_api_only_host("/health"))
        self.assertTrue(_path_allowed_on_api_only_host("/v1/chat/completions"))
        self.assertTrue(_path_allowed_on_api_only_host("/v1beta/models/foo:generateContent"))
        self.assertTrue(_path_allowed_on_api_only_host("/models/foo"))
        self.assertTrue(_path_allowed_on_api_only_host("/api/cache/blob/file.png"))
        self.assertTrue(_path_allowed_on_api_only_host("/captcha_ws"))

    def test_api_only_host_blocks_admin_ui_and_admin_api_routes(self):
        from src.main import _path_allowed_on_api_only_host

        self.assertFalse(_path_allowed_on_api_only_host("/"))
        self.assertFalse(_path_allowed_on_api_only_host("/login"))
        self.assertFalse(_path_allowed_on_api_only_host("/assets/index.js"))
        self.assertFalse(_path_allowed_on_api_only_host("/api/admin/config"))


class RailwayFreshSeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_startup_config_rows_use_flow2api_env_seed_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "RAILWAY_VOLUME_MOUNT_PATH": tmp,
                "FLOW2API_ADMIN_USERNAME": "railadmin",
                "FLOW2API_ADMIN_PASSWORD": "rail-secret",
                "FLOW2API_API_KEY": "rail-api-key",
                "FLOW2API_CAPTCHA_METHOD": "extension",
                "FLOW2API_CACHE_BASE_URL": "https://flow-api.prismacreative.online",
                "FLOW2API_DEBUG_ENABLED": "true",
                "FLOW2API_DEBUG_LOG_REQUESTS": "false",
            }
            with patch.dict(os.environ, env, clear=False):
                cfg = Config()
                db = Database()
                await db.init_db()
                await db.init_config_from_toml(cfg.get_raw_config(), is_first_startup=True)

                admin = await db.get_admin_config()
                captcha = await db.get_captcha_config()
                cache = await db.get_cache_config()
                debug = await db.get_debug_config()

                self.assertEqual(admin.username, "railadmin")
                self.assertEqual(admin.password, "rail-secret")
                self.assertEqual(admin.api_key, "rail-api-key")
                self.assertEqual(captcha.captcha_method, "extension")
                self.assertEqual(cache.cache_base_url, "https://flow-api.prismacreative.online")
                self.assertTrue(debug.enabled)
                self.assertFalse(debug.log_requests)


if __name__ == "__main__":
    unittest.main()
