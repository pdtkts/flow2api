"""Database storage layer for Flow2API"""
import asyncio
import aiosqlite
import json
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional, List, Dict, Any
from pathlib import Path
from .models import Token, TokenStats, Task, RequestLog, AdminConfig, ProxyConfig, GenerationConfig, CacheConfig, Project, CaptchaConfig, PluginConfig, CallLogicConfig


class Database:
    """SQLite database manager"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            # Store database in data directory
            data_dir = Path(__file__).parent.parent.parent / "data"
            data_dir.mkdir(exist_ok=True)
            db_path = str(data_dir / "flow.db")
        self.db_path = db_path
        self._write_lock = asyncio.Lock()
        self._connect_timeout = 30
        self._busy_timeout_ms = 30000

    def db_exists(self) -> bool:
        """Check if database file exists"""
        return Path(self.db_path).exists()

    async def _configure_connection(self, db):
        """Apply SQLite runtime settings for better concurrent behavior."""
        await db.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        await db.execute("PRAGMA foreign_keys = ON")

    def _current_stats_date(self) -> str:
        """Return the logical date used by daily token statistics."""
        return date.today().isoformat()

    @asynccontextmanager
    async def _connect(self, *, write: bool = False):
        """Open a configured SQLite connection and optionally serialize writes."""
        if write:
            async with self._write_lock:
                async with aiosqlite.connect(self.db_path, timeout=self._connect_timeout) as db:
                    await self._configure_connection(db)
                    yield db
            return

        async with aiosqlite.connect(self.db_path, timeout=self._connect_timeout) as db:
            await self._configure_connection(db)
            yield db

    async def _table_exists(self, db, table_name: str) -> bool:
        """Check if a table exists in the database"""
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        result = await cursor.fetchone()
        return result is not None

    async def _column_exists(self, db, table_name: str, column_name: str) -> bool:
        """Check if a column exists in a table"""
        try:
            cursor = await db.execute(f"PRAGMA table_info({table_name})")
            columns = await cursor.fetchall()
            return any(col[1] == column_name for col in columns)
        except:
            return False

    async def _ensure_config_rows(self, db, config_dict: dict = None):
        """Ensure all config tables have their default rows

        Args:
            db: Database connection
            config_dict: Configuration dictionary from setting.toml (optional)
                        If None, use default values instead of reading from TOML.
        """
        # Ensure admin_config has a row
        cursor = await db.execute("SELECT COUNT(*) FROM admin_config")
        count = await cursor.fetchone()
        if count[0] == 0:
            admin_username = "admin"
            admin_password = "admin"
            api_key = "han1234"
            error_ban_threshold = 3

            if config_dict:
                global_config = config_dict.get("global", {})
                admin_username = global_config.get("admin_username", "admin")
                admin_password = global_config.get("admin_password", "admin")
                api_key = global_config.get("api_key", "han1234")

                admin_config = config_dict.get("admin", {})
                error_ban_threshold = admin_config.get("error_ban_threshold", 3)

            await db.execute("""
                INSERT INTO admin_config (id, username, password, api_key, error_ban_threshold)
                VALUES (1, ?, ?, ?, ?)
            """, (admin_username, admin_password, api_key, error_ban_threshold))

        # Ensure proxy_config has a row
        cursor = await db.execute("SELECT COUNT(*) FROM proxy_config")
        count = await cursor.fetchone()
        if count[0] == 0:
            proxy_enabled = False
            proxy_url = None
            media_proxy_enabled = False
            media_proxy_url = None

            if config_dict:
                proxy_config = config_dict.get("proxy", {})
                proxy_enabled = proxy_config.get("proxy_enabled", False)
                proxy_url = proxy_config.get("proxy_url", "")
                proxy_url = proxy_url if proxy_url else None
                media_proxy_enabled = proxy_config.get(
                    "media_proxy_enabled",
                    proxy_config.get("image_io_proxy_enabled", False)
                )
                media_proxy_url = proxy_config.get(
                    "media_proxy_url",
                    proxy_config.get("image_io_proxy_url", "")
                )
                media_proxy_url = media_proxy_url if media_proxy_url else None

            await db.execute("""
                INSERT INTO proxy_config (id, enabled, proxy_url, media_proxy_enabled, media_proxy_url)
                VALUES (1, ?, ?, ?, ?)
            """, (proxy_enabled, proxy_url, media_proxy_enabled, media_proxy_url))

        # Ensure generation_config has a row
        cursor = await db.execute("SELECT COUNT(*) FROM generation_config")
        count = await cursor.fetchone()
        if count[0] == 0:
            image_timeout = 300
            video_timeout = 1500
            max_retries = 3

            if config_dict:
                generation_config = config_dict.get("generation", {})
                flow_config = config_dict.get("flow", {})
                image_timeout = generation_config.get("image_timeout", 300)
                video_timeout = generation_config.get("video_timeout", 1500)
                max_retries = flow_config.get("max_retries", 3)

            try:
                max_retries = max(1, int(max_retries))
            except Exception:
                max_retries = 3

            await db.execute("""
                INSERT INTO generation_config (id, image_timeout, video_timeout, max_retries)
                VALUES (1, ?, ?, ?)
            """, (image_timeout, video_timeout, max_retries))

        # Ensure call_logic_config has a row
        cursor = await db.execute("SELECT COUNT(*) FROM call_logic_config")
        count = await cursor.fetchone()
        if count[0] == 0:
            call_mode = "default"
            polling_mode_enabled = False

            if config_dict:
                call_logic_config = config_dict.get("call_logic", {})
                call_mode = call_logic_config.get("call_mode", "default")
                if call_mode not in ("default", "polling"):
                    polling_mode_enabled = call_logic_config.get("polling_mode_enabled", False)
                    call_mode = "polling" if polling_mode_enabled else "default"
                else:
                    polling_mode_enabled = call_mode == "polling"

            await db.execute("""
                INSERT INTO call_logic_config (id, call_mode, polling_mode_enabled)
                VALUES (1, ?, ?)
            """, (call_mode, polling_mode_enabled))

        # Ensure cache_config has a row
        cursor = await db.execute("SELECT COUNT(*) FROM cache_config")
        count = await cursor.fetchone()
        if count[0] == 0:
            cache_enabled = False
            cache_timeout = 7200
            cache_base_url = None

            if config_dict:
                cache_config = config_dict.get("cache", {})
                cache_enabled = cache_config.get("enabled", False)
                cache_timeout = cache_config.get("timeout", 7200)
                cache_base_url = cache_config.get("base_url", "")
                # Convert empty string to None
                cache_base_url = cache_base_url if cache_base_url else None

            await db.execute("""
                INSERT INTO cache_config (id, cache_enabled, cache_timeout, cache_base_url)
                VALUES (1, ?, ?, ?)
            """, (cache_enabled, cache_timeout, cache_base_url))

        # Ensure debug_config has a row
        cursor = await db.execute("SELECT COUNT(*) FROM debug_config")
        count = await cursor.fetchone()
        if count[0] == 0:
            debug_enabled = False
            log_requests = True
            log_responses = True
            mask_token = True

            if config_dict:
                debug_config = config_dict.get("debug", {})
                debug_enabled = debug_config.get("enabled", False)
                log_requests = debug_config.get("log_requests", True)
                log_responses = debug_config.get("log_responses", True)
                mask_token = debug_config.get("mask_token", True)

            await db.execute("""
                INSERT INTO debug_config (id, enabled, log_requests, log_responses, mask_token)
                VALUES (1, ?, ?, ?, ?)
            """, (debug_enabled, log_requests, log_responses, mask_token))

        # Ensure captcha_config has a row
        cursor = await db.execute("SELECT COUNT(*) FROM captcha_config")
        count = await cursor.fetchone()
        if count[0] == 0:
            captcha_method = "browser"
            yescaptcha_api_key = ""
            yescaptcha_base_url = "https://api.yescaptcha.com"
            remote_browser_base_url = ""
            remote_browser_api_key = ""
            remote_browser_timeout = 60
            browser_fallback_to_remote_browser = True
            browser_count = 1
            personal_project_pool_size = 4
            personal_max_resident_tabs = 5
            personal_idle_tab_ttl_seconds = 600
            session_refresh_enabled = True
            session_refresh_browser_first = True
            session_refresh_inject_st_cookie = True
            session_refresh_warmup_urls = "https://labs.google/fx/tools/flow,https://labs.google/fx"
            session_refresh_wait_seconds_per_url = 60
            session_refresh_overall_timeout_seconds = 180
            session_refresh_update_st_from_cookie = True
            session_refresh_fail_if_st_refresh_fails = True
            session_refresh_local_only = True
            session_refresh_scheduler_enabled = False
            session_refresh_scheduler_interval_minutes = 30
            session_refresh_scheduler_batch_size = 10
            session_refresh_scheduler_only_expiring_within_minutes = 60
            extension_queue_wait_timeout_seconds = 20
            dedicated_extension_enabled = False
            dedicated_extension_captcha_timeout_seconds = 25
            dedicated_extension_st_refresh_timeout_seconds = 45

            if config_dict:
                captcha_config = config_dict.get("captcha", {})
                captcha_method = captcha_config.get("captcha_method", "browser")
                yescaptcha_api_key = captcha_config.get("yescaptcha_api_key", "")
                yescaptcha_base_url = captcha_config.get("yescaptcha_base_url", "https://api.yescaptcha.com")
                remote_browser_base_url = captcha_config.get("remote_browser_base_url", "")
                remote_browser_api_key = captcha_config.get("remote_browser_api_key", "")
                remote_browser_timeout = captcha_config.get("remote_browser_timeout", 60)
                browser_fallback_to_remote_browser = captcha_config.get(
                    "browser_fallback_to_remote_browser",
                    True,
                )
                browser_count = captcha_config.get("browser_count", 1)
                personal_project_pool_size = captcha_config.get("personal_project_pool_size", 4)
                personal_max_resident_tabs = captcha_config.get("personal_max_resident_tabs", 5)
                personal_idle_tab_ttl_seconds = captcha_config.get("personal_idle_tab_ttl_seconds", 600)
                session_refresh_enabled = captcha_config.get("session_refresh_enabled", True)
                session_refresh_browser_first = captcha_config.get("session_refresh_browser_first", True)
                session_refresh_inject_st_cookie = captcha_config.get("session_refresh_inject_st_cookie", True)
                session_refresh_warmup_urls = captcha_config.get(
                    "session_refresh_warmup_urls",
                    "https://labs.google/fx/tools/flow,https://labs.google/fx",
                )
                session_refresh_wait_seconds_per_url = captcha_config.get("session_refresh_wait_seconds_per_url", 60)
                session_refresh_overall_timeout_seconds = captcha_config.get("session_refresh_overall_timeout_seconds", 180)
                session_refresh_update_st_from_cookie = captcha_config.get("session_refresh_update_st_from_cookie", True)
                session_refresh_fail_if_st_refresh_fails = captcha_config.get("session_refresh_fail_if_st_refresh_fails", True)
                session_refresh_local_only = captcha_config.get("session_refresh_local_only", True)
                session_refresh_scheduler_enabled = captcha_config.get("session_refresh_scheduler_enabled", False)
                session_refresh_scheduler_interval_minutes = captcha_config.get("session_refresh_scheduler_interval_minutes", 30)
                session_refresh_scheduler_batch_size = captcha_config.get("session_refresh_scheduler_batch_size", 10)
                session_refresh_scheduler_only_expiring_within_minutes = captcha_config.get(
                    "session_refresh_scheduler_only_expiring_within_minutes",
                    60,
                )
                extension_queue_wait_timeout_seconds = captcha_config.get(
                    "extension_queue_wait_timeout_seconds",
                    20,
                )
                dedicated_extension_enabled = captcha_config.get("dedicated_extension_enabled", False)
                dedicated_extension_captcha_timeout_seconds = captcha_config.get(
                    "dedicated_extension_captcha_timeout_seconds",
                    25,
                )
                dedicated_extension_st_refresh_timeout_seconds = captcha_config.get(
                    "dedicated_extension_st_refresh_timeout_seconds",
                    45,
                )
            try:
                remote_browser_timeout = max(5, int(remote_browser_timeout))
            except Exception:
                remote_browser_timeout = 60
            try:
                browser_count = max(1, int(browser_count))
            except Exception:
                browser_count = 1
            try:
                personal_project_pool_size = max(1, min(50, int(personal_project_pool_size)))
            except Exception:
                personal_project_pool_size = 4
            try:
                personal_max_resident_tabs = max(1, min(50, int(personal_max_resident_tabs)))
            except Exception:
                personal_max_resident_tabs = 5
            try:
                personal_idle_tab_ttl_seconds = max(60, int(personal_idle_tab_ttl_seconds))
            except Exception:
                personal_idle_tab_ttl_seconds = 600

            await db.execute("""
                INSERT INTO captcha_config (
                    id, captcha_method, yescaptcha_api_key, yescaptcha_base_url,
                    remote_browser_base_url, remote_browser_api_key, remote_browser_timeout,
                    browser_fallback_to_remote_browser,
                    browser_count, personal_project_pool_size,
                    personal_max_resident_tabs, personal_idle_tab_ttl_seconds,
                    session_refresh_enabled, session_refresh_browser_first, session_refresh_inject_st_cookie,
                    session_refresh_warmup_urls, session_refresh_wait_seconds_per_url,
                    session_refresh_overall_timeout_seconds, session_refresh_update_st_from_cookie,
                    session_refresh_fail_if_st_refresh_fails, session_refresh_local_only,
                    session_refresh_scheduler_enabled, session_refresh_scheduler_interval_minutes,
                    session_refresh_scheduler_batch_size, session_refresh_scheduler_only_expiring_within_minutes,
                    extension_queue_wait_timeout_seconds,
                    dedicated_extension_enabled, dedicated_extension_captcha_timeout_seconds,
                    dedicated_extension_st_refresh_timeout_seconds
                )
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                captcha_method,
                yescaptcha_api_key,
                yescaptcha_base_url,
                remote_browser_base_url,
                remote_browser_api_key,
                remote_browser_timeout,
                bool(browser_fallback_to_remote_browser),
                browser_count,
                personal_project_pool_size,
                personal_max_resident_tabs,
                personal_idle_tab_ttl_seconds,
                bool(session_refresh_enabled),
                bool(session_refresh_browser_first),
                bool(session_refresh_inject_st_cookie),
                str(session_refresh_warmup_urls or "").strip()
                or "https://labs.google/fx/tools/flow,https://labs.google/fx",
                max(0, int(session_refresh_wait_seconds_per_url or 60)),
                max(10, int(session_refresh_overall_timeout_seconds or 180)),
                bool(session_refresh_update_st_from_cookie),
                bool(session_refresh_fail_if_st_refresh_fails),
                bool(session_refresh_local_only),
                bool(session_refresh_scheduler_enabled),
                max(1, int(session_refresh_scheduler_interval_minutes or 30)),
                max(1, int(session_refresh_scheduler_batch_size or 10)),
                max(1, int(session_refresh_scheduler_only_expiring_within_minutes or 60)),
                max(1, min(120, int(extension_queue_wait_timeout_seconds or 20))),
                bool(dedicated_extension_enabled),
                max(5, min(180, int(dedicated_extension_captcha_timeout_seconds or 25))),
                max(10, min(300, int(dedicated_extension_st_refresh_timeout_seconds or 45))),
            ))

        # Ensure plugin_config has a row
        cursor = await db.execute("SELECT COUNT(*) FROM plugin_config")
        count = await cursor.fetchone()
        if count[0] == 0:
            await db.execute("""
                INSERT INTO plugin_config (id, connection_token, auto_enable_on_update)
                VALUES (1, '', 1)
            """)

    async def check_and_migrate_db(self, config_dict: dict = None):
        """Check database integrity and perform migrations if needed

        This method is called during upgrade mode to:
        1. Create missing tables (if they don't exist)
        2. Add missing columns to existing tables
        3. Ensure all config tables have default rows

        Args:
            config_dict: Configuration dictionary from setting.toml (optional)
                        Used only to initialize missing config rows with default values.
                        Existing config rows will NOT be overwritten.
        """
        async with self._connect(write=True) as db:
            print("Checking database integrity and performing migrations...")
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA synchronous = NORMAL")

            # ========== Step 1: Create missing tables ==========
            # Check and create cache_config table if missing
            if not await self._table_exists(db, "cache_config"):
                print("  ✓ Creating missing table: cache_config")
                await db.execute("""
                    CREATE TABLE cache_config (
                        id INTEGER PRIMARY KEY DEFAULT 1,
                        cache_enabled BOOLEAN DEFAULT 0,
                        cache_timeout INTEGER DEFAULT 7200,
                        cache_base_url TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

            # Check and create proxy_config table if missing
            if not await self._table_exists(db, "proxy_config"):
                print("  ✓ Creating missing table: proxy_config")
                await db.execute("""
                    CREATE TABLE proxy_config (
                        id INTEGER PRIMARY KEY DEFAULT 1,
                        enabled BOOLEAN DEFAULT 0,
                        proxy_url TEXT,
                        media_proxy_enabled BOOLEAN DEFAULT 0,
                        media_proxy_url TEXT,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

            # Check and create call_logic_config table if missing
            if not await self._table_exists(db, "call_logic_config"):
                print("  Creating missing table: call_logic_config")
                await db.execute("""
                    CREATE TABLE call_logic_config (
                        id INTEGER PRIMARY KEY DEFAULT 1,
                        call_mode TEXT DEFAULT 'default',
                        polling_mode_enabled BOOLEAN DEFAULT 0,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

            # Check and create captcha_config table if missing
            if not await self._table_exists(db, "captcha_config"):
                print("  ✓ Creating missing table: captcha_config")
                await db.execute("""
                    CREATE TABLE captcha_config (
                        id INTEGER PRIMARY KEY DEFAULT 1,
                        captcha_method TEXT DEFAULT 'browser',
                        yescaptcha_api_key TEXT DEFAULT '',
                        yescaptcha_base_url TEXT DEFAULT 'https://api.yescaptcha.com',
                        capmonster_api_key TEXT DEFAULT '',
                        capmonster_base_url TEXT DEFAULT 'https://api.capmonster.cloud',
                        ezcaptcha_api_key TEXT DEFAULT '',
                        ezcaptcha_base_url TEXT DEFAULT 'https://api.ez-captcha.com',
                        capsolver_api_key TEXT DEFAULT '',
                        capsolver_base_url TEXT DEFAULT 'https://api.capsolver.com',
                        remote_browser_base_url TEXT DEFAULT '',
                        remote_browser_api_key TEXT DEFAULT '',
                        remote_browser_timeout INTEGER DEFAULT 60,
                        extension_queue_wait_timeout_seconds INTEGER DEFAULT 20,
                        dedicated_extension_enabled BOOLEAN DEFAULT 0,
                        dedicated_extension_captcha_timeout_seconds INTEGER DEFAULT 25,
                        dedicated_extension_st_refresh_timeout_seconds INTEGER DEFAULT 45,
                        website_key TEXT DEFAULT '6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV',
                        page_action TEXT DEFAULT 'IMAGE_GENERATION',
                        browser_proxy_enabled BOOLEAN DEFAULT 0,
                        browser_proxy_url TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

            # Check and create plugin_config table if missing
            if not await self._table_exists(db, "plugin_config"):
                print("  ✓ Creating missing table: plugin_config")
                await db.execute("""
                    CREATE TABLE plugin_config (
                        id INTEGER PRIMARY KEY DEFAULT 1,
                        connection_token TEXT DEFAULT '',
                        auto_enable_on_update BOOLEAN DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

            if not await self._table_exists(db, "extension_worker_bindings"):
                print("  ✓ Creating missing table: extension_worker_bindings")
                await db.execute("""
                    CREATE TABLE extension_worker_bindings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        route_key TEXT NOT NULL UNIQUE,
                        api_key_id INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                    )
                """)

            if not await self._table_exists(db, "dedicated_extension_workers"):
                print("  ✓ Creating missing table: dedicated_extension_workers")
                await db.execute("""
                    CREATE TABLE dedicated_extension_workers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        worker_key_prefix TEXT NOT NULL UNIQUE,
                        worker_key_hash TEXT NOT NULL UNIQUE,
                        label TEXT DEFAULT '',
                        token_id INTEGER,
                        route_key TEXT,
                        last_instance_id TEXT,
                        is_active BOOLEAN DEFAULT 1,
                        last_seen_at TIMESTAMP,
                        last_error TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (token_id) REFERENCES tokens(id)
                    )
                """)

            if not await self._table_exists(db, "api_clients"):
                print("  ✓ Creating missing table: api_clients")
                await db.execute("""
                    CREATE TABLE api_clients (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        is_active BOOLEAN DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

            if not await self._table_exists(db, "api_keys"):
                print("  ✓ Creating missing table: api_keys")
                await db.execute("""
                    CREATE TABLE api_keys (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        client_id INTEGER NOT NULL,
                        label TEXT NOT NULL,
                        key_prefix TEXT NOT NULL,
                        key_plaintext TEXT,
                        key_hash TEXT NOT NULL UNIQUE,
                        scopes TEXT DEFAULT '*',
                        is_active BOOLEAN DEFAULT 1,
                        expires_at TIMESTAMP,
                        last_used_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (client_id) REFERENCES api_clients(id)
                    )
                """)

            if not await self._table_exists(db, "api_key_accounts"):
                print("  ✓ Creating missing table: api_key_accounts")
                await db.execute("""
                    CREATE TABLE api_key_accounts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        api_key_id INTEGER NOT NULL,
                        account_id INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(api_key_id, account_id),
                        FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                    )
                """)

            if not await self._table_exists(db, "api_key_rate_limits"):
                print("  ✓ Creating missing table: api_key_rate_limits")
                await db.execute("""
                    CREATE TABLE api_key_rate_limits (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        api_key_id INTEGER NOT NULL,
                        endpoint TEXT NOT NULL,
                        rpm INTEGER DEFAULT 0,
                        rph INTEGER DEFAULT 0,
                        burst INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(api_key_id, endpoint),
                        FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                    )
                """)

            if not await self._table_exists(db, "api_key_audit_logs"):
                print("  ✓ Creating missing table: api_key_audit_logs")
                await db.execute("""
                    CREATE TABLE api_key_audit_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        api_key_id INTEGER,
                        endpoint TEXT NOT NULL,
                        account_id INTEGER,
                        status_code INTEGER NOT NULL,
                        detail TEXT,
                        ip TEXT,
                        user_agent TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                    )
                """)

            if not await self._table_exists(db, "cache_files"):
                print("  ✓ Creating missing table: cache_files")
                await db.execute("""
                    CREATE TABLE cache_files (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL UNIQUE,
                        api_key_id INTEGER NOT NULL,
                        token_id INTEGER,
                        flow_project_id TEXT,
                        media_type TEXT,
                        source_url TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (api_key_id) REFERENCES api_keys(id),
                        FOREIGN KEY (token_id) REFERENCES tokens(id)
                    )
                """)

            # ========== Step 2: Add missing columns to existing tables ==========
            # Check and add missing columns to tokens table
            if await self._table_exists(db, "tokens"):
                columns_to_add = [
                    ("at", "TEXT"),  # Access Token
                    ("at_expires", "TIMESTAMP"),  # AT expiration time
                    ("credits", "INTEGER DEFAULT 0"),  # Balance
                    ("user_paygate_tier", "TEXT"),  # User tier
                    ("current_project_id", "TEXT"),  # Current project UUID
                    ("current_project_name", "TEXT"),  # Project name
                    ("image_enabled", "BOOLEAN DEFAULT 1"),
                    ("video_enabled", "BOOLEAN DEFAULT 1"),
                    ("image_concurrency", "INTEGER DEFAULT -1"),
                    ("video_concurrency", "INTEGER DEFAULT -1"),
                    ("captcha_proxy_url", "TEXT"),  # token级打码代理
                    ("extension_route_key", "TEXT"),  # extension 模式路由键
                    ("ban_reason", "TEXT"),  # 禁用原因
                    ("banned_at", "TIMESTAMP"),  # 禁用时间
                ]

                for col_name, col_type in columns_to_add:
                    if not await self._column_exists(db, "tokens", col_name):
                        try:
                            await db.execute(f"ALTER TABLE tokens ADD COLUMN {col_name} {col_type}")
                            print(f"  ✓ Added column '{col_name}' to tokens table")
                        except Exception as e:
                            print(f"  ✗ Failed to add column '{col_name}': {e}")

            # Check and add missing columns to admin_config table
            if await self._table_exists(db, "admin_config"):
                if not await self._column_exists(db, "admin_config", "error_ban_threshold"):
                    try:
                        await db.execute("ALTER TABLE admin_config ADD COLUMN error_ban_threshold INTEGER DEFAULT 3")
                        print("  ✓ Added column 'error_ban_threshold' to admin_config table")
                    except Exception as e:
                        print(f"  ✗ Failed to add column 'error_ban_threshold': {e}")

            # Check and add missing columns to proxy_config table
            if await self._table_exists(db, "proxy_config"):
                proxy_columns_to_add = [
                    ("media_proxy_enabled", "BOOLEAN DEFAULT 0"),
                    ("media_proxy_url", "TEXT"),
                ]

                for col_name, col_type in proxy_columns_to_add:
                    if not await self._column_exists(db, "proxy_config", col_name):
                        try:
                            await db.execute(f"ALTER TABLE proxy_config ADD COLUMN {col_name} {col_type}")
                            print(f"  ✓ Added column '{col_name}' to proxy_config table")
                        except Exception as e:
                            print(f"  ✗ Failed to add column '{col_name}': {e}")

            # Check and add missing columns to generation_config table
            if await self._table_exists(db, "generation_config"):
                generation_columns_to_add = [
                    ("max_retries", "INTEGER DEFAULT 3"),
                ]

                for col_name, col_type in generation_columns_to_add:
                    if not await self._column_exists(db, "generation_config", col_name):
                        try:
                            await db.execute(f"ALTER TABLE generation_config ADD COLUMN {col_name} {col_type}")
                            print(f"  ✓ Added column '{col_name}' to generation_config table")
                        except Exception as e:
                            print(f"  ✗ Failed to add column '{col_name}': {e}")

            if await self._table_exists(db, "projects"):
                if not await self._column_exists(db, "projects", "api_key_id"):
                    try:
                        await db.execute("ALTER TABLE projects ADD COLUMN api_key_id INTEGER")
                        print("  ✓ Added column 'api_key_id' to projects table")
                    except Exception as e:
                        print(f"  ✗ Failed to add column 'api_key_id' to projects: {e}")

            if await self._table_exists(db, "tasks"):
                if not await self._column_exists(db, "tasks", "api_key_id"):
                    try:
                        await db.execute("ALTER TABLE tasks ADD COLUMN api_key_id INTEGER")
                        print("  ✓ Added column 'api_key_id' to tasks table")
                    except Exception as e:
                        print(f"  ✗ Failed to add column 'api_key_id' to tasks: {e}")

            if await self._table_exists(db, "request_logs"):
                if not await self._column_exists(db, "request_logs", "api_key_id"):
                    try:
                        await db.execute("ALTER TABLE request_logs ADD COLUMN api_key_id INTEGER")
                        print("  ✓ Added column 'api_key_id' to request_logs table")
                    except Exception as e:
                        print(f"  ✗ Failed to add column 'api_key_id' to request_logs: {e}")

            # Check and add missing columns to captcha_config table
            if await self._table_exists(db, "captcha_config"):
                captcha_columns_to_add = [
                    ("browser_proxy_enabled", "BOOLEAN DEFAULT 0"),
                    ("browser_proxy_url", "TEXT"),
                    ("capmonster_api_key", "TEXT DEFAULT ''"),
                    ("capmonster_base_url", "TEXT DEFAULT 'https://api.capmonster.cloud'"),
                    ("ezcaptcha_api_key", "TEXT DEFAULT ''"),
                    ("ezcaptcha_base_url", "TEXT DEFAULT 'https://api.ez-captcha.com'"),
                    ("capsolver_api_key", "TEXT DEFAULT ''"),
                    ("capsolver_base_url", "TEXT DEFAULT 'https://api.capsolver.com'"),
                    ("browser_count", "INTEGER DEFAULT 1"),
                    ("remote_browser_base_url", "TEXT DEFAULT ''"),
                    ("remote_browser_api_key", "TEXT DEFAULT ''"),
                    ("remote_browser_timeout", "INTEGER DEFAULT 60"),
                    ("browser_fallback_to_remote_browser", "BOOLEAN DEFAULT 1"),
                    ("session_refresh_enabled", "BOOLEAN DEFAULT 1"),
                    ("session_refresh_browser_first", "BOOLEAN DEFAULT 1"),
                    ("session_refresh_inject_st_cookie", "BOOLEAN DEFAULT 1"),
                    ("session_refresh_warmup_urls", "TEXT DEFAULT 'https://labs.google/fx/tools/flow,https://labs.google/fx'"),
                    ("session_refresh_wait_seconds_per_url", "INTEGER DEFAULT 60"),
                    ("session_refresh_overall_timeout_seconds", "INTEGER DEFAULT 180"),
                    ("session_refresh_update_st_from_cookie", "BOOLEAN DEFAULT 1"),
                    ("session_refresh_fail_if_st_refresh_fails", "BOOLEAN DEFAULT 1"),
                    ("session_refresh_local_only", "BOOLEAN DEFAULT 1"),
                    ("session_refresh_scheduler_enabled", "BOOLEAN DEFAULT 0"),
                    ("session_refresh_scheduler_interval_minutes", "INTEGER DEFAULT 30"),
                    ("session_refresh_scheduler_batch_size", "INTEGER DEFAULT 10"),
                    ("session_refresh_scheduler_only_expiring_within_minutes", "INTEGER DEFAULT 60"),
                    ("extension_queue_wait_timeout_seconds", "INTEGER DEFAULT 20"),
                    ("dedicated_extension_enabled", "BOOLEAN DEFAULT 0"),
                    ("dedicated_extension_captcha_timeout_seconds", "INTEGER DEFAULT 25"),
                    ("dedicated_extension_st_refresh_timeout_seconds", "INTEGER DEFAULT 45"),
                    ("extension_fallback_to_managed_on_dedicated_failure", "BOOLEAN DEFAULT 0"),
                ]

                for col_name, col_type in captcha_columns_to_add:
                    if not await self._column_exists(db, "captcha_config", col_name):
                        try:
                            await db.execute(f"ALTER TABLE captcha_config ADD COLUMN {col_name} {col_type}")
                            print(f"  ✓ Added column '{col_name}' to captcha_config table")
                        except Exception as e:
                            print(f"  ✗ Failed to add column '{col_name}': {e}")

            # Check and add missing columns to token_stats table
            if await self._table_exists(db, "token_stats"):
                stats_columns_to_add = [
                    ("today_image_count", "INTEGER DEFAULT 0"),
                    ("today_video_count", "INTEGER DEFAULT 0"),
                    ("today_error_count", "INTEGER DEFAULT 0"),
                    ("today_date", "DATE"),
                    ("consecutive_error_count", "INTEGER DEFAULT 0"),  # 🆕 连续错误计数
                ]

                for col_name, col_type in stats_columns_to_add:
                    if not await self._column_exists(db, "token_stats", col_name):
                        try:
                            await db.execute(f"ALTER TABLE token_stats ADD COLUMN {col_name} {col_type}")
                            print(f"  ✓ Added column '{col_name}' to token_stats table")
                        except Exception as e:
                            print(f"  ✗ Failed to add column '{col_name}': {e}")

            # Check and add missing columns to plugin_config table
            if await self._table_exists(db, "plugin_config"):
                plugin_columns_to_add = [
                    ("auto_enable_on_update", "BOOLEAN DEFAULT 1"),  # 默认开启
                ]

                for col_name, col_type in plugin_columns_to_add:
                    if not await self._column_exists(db, "plugin_config", col_name):
                        try:
                            await db.execute(f"ALTER TABLE plugin_config ADD COLUMN {col_name} {col_type}")
                            print(f"  ✓ Added column '{col_name}' to plugin_config table")
                        except Exception as e:
                            print(f"  ✗ Failed to add column '{col_name}': {e}")

            # Check and add missing columns to api_keys table
            if await self._table_exists(db, "api_keys"):
                api_keys_columns_to_add = [
                    ("key_plaintext", "TEXT"),
                ]

                for col_name, col_type in api_keys_columns_to_add:
                    if not await self._column_exists(db, "api_keys", col_name):
                        try:
                            await db.execute(f"ALTER TABLE api_keys ADD COLUMN {col_name} {col_type}")
                            print(f"  ✓ Added column '{col_name}' to api_keys table")
                        except Exception as e:
                            print(f"  ✗ Failed to add column '{col_name}': {e}")

            # Check and add missing columns to captcha_config table
            if await self._table_exists(db, "captcha_config"):
                captcha_columns_to_add = [
                    ("personal_project_pool_size", "INTEGER DEFAULT 4"),
                    ("personal_max_resident_tabs", "INTEGER DEFAULT 5"),
                    ("personal_idle_tab_ttl_seconds", "INTEGER DEFAULT 600"),
                    ("browser_captcha_page_url", "TEXT DEFAULT 'https://labs.google/fx/api/auth/providers'"),
                ]

                for col_name, col_type in captcha_columns_to_add:
                    if not await self._column_exists(db, "captcha_config", col_name):
                        try:
                            await db.execute(f"ALTER TABLE captcha_config ADD COLUMN {col_name} {col_type}")
                            print(f"  ✓ Added column '{col_name}' to captcha_config table")
                        except Exception as e:
                            print(f"  ✗ Failed to add column '{col_name}': {e}")

            if await self._table_exists(db, "cache_files"):
                if not await self._column_exists(db, "cache_files", "flow_project_id"):
                    try:
                        await db.execute("ALTER TABLE cache_files ADD COLUMN flow_project_id TEXT")
                        print("  ✓ Added column 'flow_project_id' to cache_files table")
                    except Exception as e:
                        print(f"  ✗ Failed to add column 'flow_project_id' to cache_files: {e}")

            # ========== Step 3: Ensure all config tables have default rows ==========
            # Note: This will NOT overwrite existing config rows
            # It only ensures missing rows are created with default values from setting.toml
            await self._ensure_config_rows(db, config_dict=config_dict)

            await db.execute("CREATE INDEX IF NOT EXISTS idx_projects_api_key_created_at ON projects(api_key_id, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_api_key_id_created_at ON request_logs(api_key_id, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_cache_files_api_key_filename ON cache_files(api_key_id, filename)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_cache_files_api_key_project ON cache_files(api_key_id, flow_project_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_extension_worker_bindings_api_key_id ON extension_worker_bindings(api_key_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_dedicated_extension_workers_token_id ON dedicated_extension_workers(token_id)")

            await db.commit()
            print("Database migration check completed.")

    async def init_db(self):
        """Initialize database tables"""
        async with self._connect(write=True) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA synchronous = NORMAL")
            # Tokens table (Flow2API版本)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    st TEXT UNIQUE NOT NULL,
                    at TEXT,
                    at_expires TIMESTAMP,
                    email TEXT NOT NULL,
                    name TEXT,
                    remark TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP,
                    use_count INTEGER DEFAULT 0,
                    credits INTEGER DEFAULT 0,
                    user_paygate_tier TEXT,
                    current_project_id TEXT,
                    current_project_name TEXT,
                    image_enabled BOOLEAN DEFAULT 1,
                    video_enabled BOOLEAN DEFAULT 1,
                    image_concurrency INTEGER DEFAULT -1,
                    video_concurrency INTEGER DEFAULT -1,
                    captcha_proxy_url TEXT,
                    extension_route_key TEXT,
                    ban_reason TEXT,
                    banned_at TIMESTAMP
                )
            """)

            # Projects table (新增)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT UNIQUE NOT NULL,
                    token_id INTEGER NOT NULL,
                    api_key_id INTEGER,
                    project_name TEXT NOT NULL,
                    tool_name TEXT DEFAULT 'PINHOLE',
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES tokens(id),
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                )
            """)

            # Token stats table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS token_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id INTEGER NOT NULL,
                    image_count INTEGER DEFAULT 0,
                    video_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    last_success_at TIMESTAMP,
                    last_error_at TIMESTAMP,
                    today_image_count INTEGER DEFAULT 0,
                    today_video_count INTEGER DEFAULT 0,
                    today_error_count INTEGER DEFAULT 0,
                    today_date DATE,
                    consecutive_error_count INTEGER DEFAULT 0,
                    FOREIGN KEY (token_id) REFERENCES tokens(id)
                )
            """)

            # Tasks table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT UNIQUE NOT NULL,
                    token_id INTEGER NOT NULL,
                    api_key_id INTEGER,
                    project_id TEXT,
                    model TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'processing',
                    progress INTEGER DEFAULT 0,
                    result_urls TEXT,
                    base_result_urls TEXT,
                    delivery_urls TEXT,
                    requested_resolution TEXT,
                    output_resolution TEXT,
                    upscale_status TEXT,
                    upscale_error_message TEXT,
                    error_message TEXT,
                    scene_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES tokens(id),
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                )
            """)

            # Request logs table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS request_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id INTEGER,
                    api_key_id INTEGER,
                    operation TEXT NOT NULL,
                    request_body TEXT,
                    response_body TEXT,
                    status_code INTEGER NOT NULL,
                    duration FLOAT NOT NULL,
                    status_text TEXT DEFAULT '',
                    progress INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES tokens(id),
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS cache_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL UNIQUE,
                    api_key_id INTEGER NOT NULL,
                    token_id INTEGER,
                    flow_project_id TEXT,
                    media_type TEXT,
                    source_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id),
                    FOREIGN KEY (token_id) REFERENCES tokens(id)
                )
            """)

            # Admin config table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS admin_config (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    username TEXT DEFAULT 'admin',
                    password TEXT DEFAULT 'admin',
                    api_key TEXT DEFAULT 'han1234',
                    error_ban_threshold INTEGER DEFAULT 3,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Admin UI session tokens (survive process restarts)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    last_used_at INTEGER
                )
            """)

            # Proxy config table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS proxy_config (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    enabled BOOLEAN DEFAULT 0,
                    proxy_url TEXT,
                    media_proxy_enabled BOOLEAN DEFAULT 0,
                    media_proxy_url TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Generation config table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS generation_config (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    image_timeout INTEGER DEFAULT 300,
                    video_timeout INTEGER DEFAULT 1500,
                    max_retries INTEGER DEFAULT 3,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Call logic config table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS call_logic_config (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    call_mode TEXT DEFAULT 'default',
                    polling_mode_enabled BOOLEAN DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Cache config table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cache_config (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    cache_enabled BOOLEAN DEFAULT 0,
                    cache_timeout INTEGER DEFAULT 7200,
                    cache_base_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Debug config table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS debug_config (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    enabled BOOLEAN DEFAULT 0,
                    log_requests BOOLEAN DEFAULT 1,
                    log_responses BOOLEAN DEFAULT 1,
                    mask_token BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Captcha config table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS captcha_config (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    captcha_method TEXT DEFAULT 'browser',
                    yescaptcha_api_key TEXT DEFAULT '',
                    yescaptcha_base_url TEXT DEFAULT 'https://api.yescaptcha.com',
                    capmonster_api_key TEXT DEFAULT '',
                    capmonster_base_url TEXT DEFAULT 'https://api.capmonster.cloud',
                    ezcaptcha_api_key TEXT DEFAULT '',
                    ezcaptcha_base_url TEXT DEFAULT 'https://api.ez-captcha.com',
                    capsolver_api_key TEXT DEFAULT '',
                    capsolver_base_url TEXT DEFAULT 'https://api.capsolver.com',
                    remote_browser_base_url TEXT DEFAULT '',
                    remote_browser_api_key TEXT DEFAULT '',
                    remote_browser_timeout INTEGER DEFAULT 60,
                    extension_queue_wait_timeout_seconds INTEGER DEFAULT 20,
                    browser_fallback_to_remote_browser BOOLEAN DEFAULT 1,
                    website_key TEXT DEFAULT '6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV',
                    page_action TEXT DEFAULT 'IMAGE_GENERATION',

                    browser_proxy_enabled BOOLEAN DEFAULT 0,
                    browser_proxy_url TEXT,
                    browser_count INTEGER DEFAULT 1,
                    personal_project_pool_size INTEGER DEFAULT 4,
                    personal_max_resident_tabs INTEGER DEFAULT 5,
                    personal_idle_tab_ttl_seconds INTEGER DEFAULT 600,
                    browser_captcha_page_url TEXT DEFAULT 'https://labs.google/fx/api/auth/providers',
                    session_refresh_enabled BOOLEAN DEFAULT 1,
                    session_refresh_browser_first BOOLEAN DEFAULT 1,
                    session_refresh_inject_st_cookie BOOLEAN DEFAULT 1,
                    session_refresh_warmup_urls TEXT DEFAULT 'https://labs.google/fx/tools/flow,https://labs.google/fx',
                    session_refresh_wait_seconds_per_url INTEGER DEFAULT 60,
                    session_refresh_overall_timeout_seconds INTEGER DEFAULT 180,
                    session_refresh_update_st_from_cookie BOOLEAN DEFAULT 1,
                    session_refresh_fail_if_st_refresh_fails BOOLEAN DEFAULT 1,
                    session_refresh_local_only BOOLEAN DEFAULT 1,
                    session_refresh_scheduler_enabled BOOLEAN DEFAULT 0,
                    session_refresh_scheduler_interval_minutes INTEGER DEFAULT 30,
                    session_refresh_scheduler_batch_size INTEGER DEFAULT 10,
                    session_refresh_scheduler_only_expiring_within_minutes INTEGER DEFAULT 60,
                    dedicated_extension_enabled BOOLEAN DEFAULT 0,
                    dedicated_extension_captcha_timeout_seconds INTEGER DEFAULT 25,
                    dedicated_extension_st_refresh_timeout_seconds INTEGER DEFAULT 45,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Plugin config table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS plugin_config (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    connection_token TEXT DEFAULT '',
                    auto_enable_on_update BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # API key manager tables
            await db.execute("""
                CREATE TABLE IF NOT EXISTS api_clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER NOT NULL,
                    label TEXT NOT NULL,
                    key_prefix TEXT NOT NULL,
                    key_plaintext TEXT,
                    key_hash TEXT NOT NULL UNIQUE,
                    scopes TEXT DEFAULT '*',
                    is_active BOOLEAN DEFAULT 1,
                    expires_at TIMESTAMP,
                    last_used_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (client_id) REFERENCES api_clients(id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS api_key_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_key_id INTEGER NOT NULL,
                    account_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(api_key_id, account_id),
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS extension_worker_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    route_key TEXT NOT NULL UNIQUE,
                    api_key_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS dedicated_extension_workers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    worker_key_prefix TEXT NOT NULL UNIQUE,
                    worker_key_hash TEXT NOT NULL UNIQUE,
                    label TEXT DEFAULT '',
                    token_id INTEGER,
                    route_key TEXT,
                    last_instance_id TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    last_seen_at TIMESTAMP,
                    last_error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES tokens(id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS api_key_rate_limits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_key_id INTEGER NOT NULL,
                    endpoint TEXT NOT NULL,
                    rpm INTEGER DEFAULT 0,
                    rph INTEGER DEFAULT 0,
                    burst INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(api_key_id, endpoint),
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS api_key_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_key_id INTEGER,
                    endpoint TEXT NOT NULL,
                    account_id INTEGER,
                    status_code INTEGER NOT NULL,
                    detail TEXT,
                    ip TEXT,
                    user_agent TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                )
            """)

            # Existing DBs: tables were created before api_key_id; CREATE IF NOT EXISTS does not add columns.
            await self._ensure_api_key_ownership_columns(db)
            await self._ensure_task_async_columns(db)

            # Create indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_task_id ON tasks(task_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_token_st ON tokens(st)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_project_id ON projects(project_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_projects_api_key_created_at ON projects(api_key_id, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_tokens_email ON tokens(email)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_tokens_is_active_last_used_at ON tokens(is_active, last_used_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_client_id ON api_keys(client_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_api_key_accounts_key_id ON api_key_accounts(api_key_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_api_key_rl_key_endpoint ON api_key_rate_limits(api_key_id, endpoint)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_api_key_audit_created_at ON api_key_audit_logs(created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_dedicated_extension_workers_token_id ON dedicated_extension_workers(token_id)")

            # Migrate request_logs table if needed
            await self._migrate_request_logs(db)

            # Request logs query indexes (列表按 created_at 排序 / token 过滤)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_created_at ON request_logs(created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_token_id_created_at ON request_logs(token_id, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_api_key_id_created_at ON request_logs(api_key_id, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_cache_files_api_key_filename ON cache_files(api_key_id, filename)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_cache_files_api_key_project ON cache_files(api_key_id, flow_project_id)")

            # Token stats lookup index
            await db.execute("CREATE INDEX IF NOT EXISTS idx_token_stats_token_id ON token_stats(token_id)")

            await db.commit()

    async def _ensure_api_key_ownership_columns(self, db):
        """Add api_key_id to core tables when upgrading from older schemas (before indexes on those columns)."""
        try:
            if await self._table_exists(db, "projects"):
                if not await self._column_exists(db, "projects", "api_key_id"):
                    await db.execute("ALTER TABLE projects ADD COLUMN api_key_id INTEGER")
                    print("  ✓ Added column 'api_key_id' to projects (init_db upgrade)")
            if await self._table_exists(db, "tasks"):
                if not await self._column_exists(db, "tasks", "api_key_id"):
                    await db.execute("ALTER TABLE tasks ADD COLUMN api_key_id INTEGER")
                    print("  ✓ Added column 'api_key_id' to tasks (init_db upgrade)")
            if await self._table_exists(db, "request_logs"):
                if not await self._column_exists(db, "request_logs", "api_key_id"):
                    await db.execute("ALTER TABLE request_logs ADD COLUMN api_key_id INTEGER")
                    print("  ✓ Added column 'api_key_id' to request_logs (init_db upgrade)")
            if await self._table_exists(db, "cache_files"):
                if not await self._column_exists(db, "cache_files", "flow_project_id"):
                    await db.execute("ALTER TABLE cache_files ADD COLUMN flow_project_id TEXT")
                    print("  ✓ Added column 'flow_project_id' to cache_files (init_db upgrade)")
        except Exception as e:
            print(f"  ✗ api_key_id column upgrade failed: {e}")
            raise

    async def _ensure_task_async_columns(self, db):
        """Add async job metadata columns to tasks for polling payload enrichment."""
        try:
            if not await self._table_exists(db, "tasks"):
                return
            async_columns = {
                "project_id": "TEXT",
                "base_result_urls": "TEXT",
                "delivery_urls": "TEXT",
                "requested_resolution": "TEXT",
                "output_resolution": "TEXT",
                "upscale_status": "TEXT",
                "upscale_error_message": "TEXT",
                "job_phase": "TEXT",
                "captcha_status": "TEXT",
                "captcha_detail": "TEXT",
            }
            for column_name, column_type in async_columns.items():
                if not await self._column_exists(db, "tasks", column_name):
                    await db.execute(f"ALTER TABLE tasks ADD COLUMN {column_name} {column_type}")
                    print(f"  ✓ Added column '{column_name}' to tasks (init_db upgrade)")
        except Exception as e:
            print(f"  ✗ tasks async columns upgrade failed: {e}")
            raise

    async def _migrate_request_logs(self, db):
        """Migrate request_logs table from old schema to new schema"""
        try:
            has_model = await self._column_exists(db, "request_logs", "model")
            has_operation = await self._column_exists(db, "request_logs", "operation")

            if has_model and not has_operation:
                print("?? ?????request_logs???,????...")
                await db.execute("ALTER TABLE request_logs RENAME TO request_logs_old")
                await db.execute("""
                    CREATE TABLE request_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        token_id INTEGER,
                        api_key_id INTEGER,
                        operation TEXT NOT NULL,
                        request_body TEXT,
                        response_body TEXT,
                        status_code INTEGER NOT NULL,
                        duration FLOAT NOT NULL,
                        status_text TEXT DEFAULT '',
                        progress INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (token_id) REFERENCES tokens(id),
                        FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
                    )
                """)
                await db.execute("""
                    INSERT INTO request_logs (token_id, api_key_id, operation, request_body, status_code, duration, status_text, progress, created_at, updated_at)
                    SELECT
                        token_id,
                        NULL AS api_key_id,
                        model as operation,
                        json_object('model', model, 'prompt', substr(prompt, 1, 100)) as request_body,
                        CASE
                            WHEN status = 'completed' THEN 200
                            WHEN status = 'failed' THEN 500
                            ELSE 102
                        END as status_code,
                        response_time as duration,
                        CASE
                            WHEN status = 'completed' THEN 'completed'
                            WHEN status = 'failed' THEN 'failed'
                            ELSE 'processing'
                        END as status_text,
                        CASE
                            WHEN status = 'completed' THEN 100
                            WHEN status = 'failed' THEN 0
                            ELSE 0
                        END as progress,
                        created_at,
                        created_at
                    FROM request_logs_old
                """)
                await db.execute("DROP TABLE request_logs_old")
                print("? request_logs?????")

            if not await self._column_exists(db, "request_logs", "status_text"):
                await db.execute("ALTER TABLE request_logs ADD COLUMN status_text TEXT DEFAULT ''")
            if not await self._column_exists(db, "request_logs", "progress"):
                await db.execute("ALTER TABLE request_logs ADD COLUMN progress INTEGER DEFAULT 0")
            if not await self._column_exists(db, "request_logs", "updated_at"):
                await db.execute("ALTER TABLE request_logs ADD COLUMN updated_at TIMESTAMP")
            if not await self._column_exists(db, "request_logs", "api_key_id"):
                await db.execute("ALTER TABLE request_logs ADD COLUMN api_key_id INTEGER")
            await db.execute("UPDATE request_logs SET updated_at = created_at WHERE updated_at IS NULL")
        except Exception as e:
            print(f"?? request_logs?????: {e}")
            # Continue even if migration fails

    # Token operations
    async def add_token(self, token: Token) -> int:
        """Add a new token"""
        async with self._connect(write=True) as db:
            cursor = await db.execute("""
                INSERT INTO tokens (st, at, at_expires, email, name, remark, is_active,
                                   credits, user_paygate_tier, current_project_id, current_project_name,
                                   image_enabled, video_enabled, image_concurrency, video_concurrency, captcha_proxy_url, extension_route_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (token.st, token.at, token.at_expires, token.email, token.name, token.remark,
                  token.is_active, token.credits, token.user_paygate_tier,
                  token.current_project_id, token.current_project_name,
                  token.image_enabled, token.video_enabled,
                  token.image_concurrency, token.video_concurrency, token.captcha_proxy_url, token.extension_route_key))
            await db.commit()
            token_id = cursor.lastrowid

            # Create stats entry
            await db.execute("""
                INSERT INTO token_stats (token_id) VALUES (?)
            """, (token_id,))
            await db.commit()

            return token_id

    async def get_token(self, token_id: int) -> Optional[Token]:
        """Get token by ID"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM tokens WHERE id = ?", (token_id,))
            row = await cursor.fetchone()
            if row:
                return Token(**dict(row))
            return None

    async def get_token_by_st(self, st: str) -> Optional[Token]:
        """Get token by ST"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM tokens WHERE st = ?", (st,))
            row = await cursor.fetchone()
            if row:
                return Token(**dict(row))
            return None

    async def get_token_by_email(self, email: str) -> Optional[Token]:
        """Get token by email"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM tokens WHERE email = ?", (email,))
            row = await cursor.fetchone()
            if row:
                return Token(**dict(row))
            return None

    async def get_all_tokens(self) -> List[Token]:
        """Get all tokens"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM tokens ORDER BY created_at DESC")
            rows = await cursor.fetchall()
            return [Token(**dict(row)) for row in rows]

    async def get_all_tokens_with_stats(self) -> List[Dict[str, Any]]:
        """Get all tokens with merged statistics in one query"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            today = self._current_stats_date()
            cursor = await db.execute("""
                SELECT
                    t.*,
                    COALESCE(ts.image_count, 0) AS image_count,
                    COALESCE(ts.video_count, 0) AS video_count,
                    COALESCE(ts.error_count, 0) AS error_count,
                    COALESCE(CASE WHEN ts.today_date = ? THEN ts.today_image_count ELSE 0 END, 0) AS today_image_count,
                    COALESCE(CASE WHEN ts.today_date = ? THEN ts.today_video_count ELSE 0 END, 0) AS today_video_count,
                    COALESCE(CASE WHEN ts.today_date = ? THEN ts.today_error_count ELSE 0 END, 0) AS today_error_count,
                    COALESCE(ts.consecutive_error_count, 0) AS consecutive_error_count,
                    ts.last_error_at AS last_error_at
                FROM tokens t
                LEFT JOIN token_stats ts ON ts.token_id = t.id
                ORDER BY t.created_at DESC
            """, (today, today, today))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_dashboard_stats(self) -> Dict[str, int]:
        """Get dashboard counters with aggregated SQL queries"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            today = self._current_stats_date()

            token_cursor = await db.execute("""
                SELECT
                    COUNT(*) AS total_tokens,
                    COALESCE(SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END), 0) AS active_tokens
                FROM tokens
            """)
            token_row = await token_cursor.fetchone()

            stats_cursor = await db.execute("""
                SELECT
                    COALESCE(SUM(image_count), 0) AS total_images,
                    COALESCE(SUM(video_count), 0) AS total_videos,
                    COALESCE(SUM(error_count), 0) AS total_errors,
                    COALESCE(SUM(CASE WHEN today_date = ? THEN today_image_count ELSE 0 END), 0) AS today_images,
                    COALESCE(SUM(CASE WHEN today_date = ? THEN today_video_count ELSE 0 END), 0) AS today_videos,
                    COALESCE(SUM(CASE WHEN today_date = ? THEN today_error_count ELSE 0 END), 0) AS today_errors
                FROM token_stats
            """, (today, today, today))
            stats_row = await stats_cursor.fetchone()

            token_data = dict(token_row) if token_row else {}
            stats_data = dict(stats_row) if stats_row else {}

            return {
                "total_tokens": int(token_data.get("total_tokens") or 0),
                "active_tokens": int(token_data.get("active_tokens") or 0),
                "total_images": int(stats_data.get("total_images") or 0),
                "total_videos": int(stats_data.get("total_videos") or 0),
                "total_errors": int(stats_data.get("total_errors") or 0),
                "today_images": int(stats_data.get("today_images") or 0),
                "today_videos": int(stats_data.get("today_videos") or 0),
                "today_errors": int(stats_data.get("today_errors") or 0)
            }

    async def get_system_info_stats(self) -> Dict[str, int]:
        """Get lightweight system counters used by admin dashboard"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT
                    COUNT(*) AS total_tokens,
                    COALESCE(SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END), 0) AS active_tokens,
                    COALESCE(SUM(CASE WHEN is_active = 1 THEN credits ELSE 0 END), 0) AS total_credits
                FROM tokens
            """)
            row = await cursor.fetchone()
            data = dict(row) if row else {}
            return {
                "total_tokens": int(data.get("total_tokens") or 0),
                "active_tokens": int(data.get("active_tokens") or 0),
                "total_credits": int(data.get("total_credits") or 0)
            }

    async def get_active_tokens(self) -> List[Token]:
        """Get all active tokens"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM tokens WHERE is_active = 1 ORDER BY last_used_at ASC")
            rows = await cursor.fetchall()
            return [Token(**dict(row)) for row in rows]

    async def update_token(self, token_id: int, **kwargs):
        """Update token fields"""
        async with self._connect(write=True) as db:
            updates = []
            params = []

            for key, value in kwargs.items():
                updates.append(f"{key} = ?")
                params.append(value)

            if updates:
                params.append(token_id)
                query = f"UPDATE tokens SET {', '.join(updates)} WHERE id = ?"
                await db.execute(query, params)
                await db.commit()

    async def delete_token(self, token_id: int):
        """Delete token and related data"""
        async with self._connect(write=True) as db:
            await db.execute("UPDATE request_logs SET token_id = NULL WHERE token_id = ?", (token_id,))
            await db.execute("UPDATE cache_files SET token_id = NULL WHERE token_id = ?", (token_id,))
            # Dedicated extension workers can outlive a token; detach ownership first
            # so deleting token rows does not violate FK constraints.
            await db.execute("UPDATE dedicated_extension_workers SET token_id = NULL WHERE token_id = ?", (token_id,))
            await db.execute("DELETE FROM tasks WHERE token_id = ?", (token_id,))
            await db.execute("DELETE FROM token_stats WHERE token_id = ?", (token_id,))
            await db.execute("DELETE FROM projects WHERE token_id = ?", (token_id,))
            await db.execute("DELETE FROM api_key_accounts WHERE account_id = ?", (token_id,))
            await db.execute("DELETE FROM tokens WHERE id = ?", (token_id,))
            await db.commit()

    # Project operations
    async def add_project(self, project: Project) -> int:
        """Add a new project"""
        async with self._connect(write=True) as db:
            cursor = await db.execute("""
                INSERT INTO projects (project_id, token_id, api_key_id, project_name, tool_name, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (project.project_id, project.token_id, project.api_key_id, project.project_name,
                  project.tool_name, project.is_active))
            await db.commit()
            return cursor.lastrowid

    async def deactivate_projects_for_token_scope(
        self, token_id: int, api_key_id: Optional[int] = None
    ) -> int:
        """Set prior projects inactive for one token scope.

        Scope is token + api_key when api_key_id is provided; otherwise token + NULL api_key.
        Returns affected row count.
        """
        async with self._connect(write=True) as db:
            if api_key_id is None:
                cursor = await db.execute(
                    """
                    UPDATE projects
                    SET is_active = 0
                    WHERE token_id = ? AND api_key_id IS NULL AND is_active = 1
                    """,
                    (token_id,),
                )
            else:
                cursor = await db.execute(
                    """
                    UPDATE projects
                    SET is_active = 0
                    WHERE token_id = ? AND api_key_id = ? AND is_active = 1
                    """,
                    (token_id, api_key_id),
                )
            await db.commit()
            return int(cursor.rowcount or 0)

    async def get_project_by_id(self, project_id: str, api_key_id: Optional[int] = None) -> Optional[Project]:
        """Get project by UUID"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            if api_key_id is None:
                cursor = await db.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,))
            else:
                cursor = await db.execute(
                    "SELECT * FROM projects WHERE project_id = ? AND api_key_id = ?",
                    (project_id, api_key_id),
                )
            row = await cursor.fetchone()
            if row:
                return Project(**dict(row))
            return None

    async def get_projects_by_token(self, token_id: int, api_key_id: Optional[int] = None) -> List[Project]:
        """Get all projects for a token"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            if api_key_id is None:
                cursor = await db.execute(
                    "SELECT * FROM projects WHERE token_id = ? ORDER BY created_at DESC",
                    (token_id,)
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM projects WHERE token_id = ? AND api_key_id = ? ORDER BY created_at DESC",
                    (token_id, api_key_id)
                )
            rows = await cursor.fetchall()
            return [Project(**dict(row)) for row in rows]

    async def count_projects_by_api_key(self, api_key_id: int) -> int:
        """Count projects rows scoped to a managed API key."""
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM projects WHERE api_key_id = ?",
                (api_key_id,),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    async def count_projects_for_api_key_account(self, api_key_id: int, token_id: int) -> int:
        """Count active projects for one token scoped to a managed API key."""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM projects
                WHERE api_key_id = ? AND token_id = ? AND is_active = 1
                """,
                (api_key_id, token_id),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    async def list_projects_for_api_key_account(
        self, api_key_id: int, token_id: int, limit: int = 100, offset: int = 0
    ) -> List[Project]:
        """Paginated active projects for a token under a managed API key."""
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM projects
                WHERE api_key_id = ? AND token_id = ? AND is_active = 1
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (api_key_id, token_id, limit, offset),
            )
            rows = await cursor.fetchall()
            return [Project(**dict(row)) for row in rows]

    async def list_projects_by_api_key(
        self, api_key_id: int, limit: int = 10, offset: int = 0
    ) -> List[Project]:
        """Paginated projects for a managed API key, newest first."""
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM projects
                WHERE api_key_id = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (api_key_id, limit, offset),
            )
            rows = await cursor.fetchall()
            return [Project(**dict(row)) for row in rows]

    async def delete_project(self, project_id: str):
        """Delete project"""
        async with self._connect(write=True) as db:
            await db.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
            await db.commit()

    # Task operations
    async def create_task(self, task: Task) -> int:
        """Create a new task"""
        async with self._connect(write=True) as db:
            cursor = await db.execute("""
                INSERT INTO tasks (
                    task_id, token_id, api_key_id, project_id, model, prompt, status, progress,
                    result_urls, base_result_urls, delivery_urls,
                    requested_resolution, output_resolution, upscale_status, upscale_error_message,
                    scene_id, job_phase, captcha_status, captcha_detail
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.task_id,
                task.token_id,
                task.api_key_id,
                task.project_id,
                task.model,
                task.prompt,
                task.status,
                task.progress,
                json.dumps(task.result_urls) if isinstance(task.result_urls, list) else task.result_urls,
                json.dumps(task.base_result_urls) if isinstance(task.base_result_urls, list) else task.base_result_urls,
                json.dumps(task.delivery_urls) if isinstance(task.delivery_urls, list) else task.delivery_urls,
                task.requested_resolution,
                task.output_resolution,
                task.upscale_status,
                task.upscale_error_message,
                task.scene_id,
                task.job_phase,
                task.captcha_status,
                task.captcha_detail,
            ))
            await db.commit()
            return cursor.lastrowid

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
            row = await cursor.fetchone()
            if row:
                task_dict = dict(row)
                # Parse result_urls from JSON
                if task_dict.get("result_urls"):
                    task_dict["result_urls"] = json.loads(task_dict["result_urls"])
                if task_dict.get("base_result_urls"):
                    task_dict["base_result_urls"] = json.loads(task_dict["base_result_urls"])
                if task_dict.get("delivery_urls"):
                    task_dict["delivery_urls"] = json.loads(task_dict["delivery_urls"])
                return Task(**task_dict)
            return None

    async def update_task(self, task_id: str, **kwargs):
        """Update task"""
        async with self._connect(write=True) as db:
            updates = []
            params = []

            for key, value in kwargs.items():
                if value is not None:
                    # Convert list to JSON string for result_urls
                    if key in {"result_urls", "base_result_urls", "delivery_urls"} and isinstance(value, list):
                        value = json.dumps(value)
                    updates.append(f"{key} = ?")
                    params.append(value)

            if updates:
                params.append(task_id)
                query = f"UPDATE tasks SET {', '.join(updates)} WHERE task_id = ?"
                await db.execute(query, params)
                await db.commit()

    # Token stats operations (kept for compatibility, now delegates to specific methods)
    async def increment_token_stats(self, token_id: int, stat_type: str):
        """Increment token statistics (delegates to specific methods)"""
        if stat_type == "image":
            await self.increment_image_count(token_id)
        elif stat_type == "video":
            await self.increment_video_count(token_id)
        elif stat_type == "error":
            await self.increment_error_count(token_id)

    async def get_token_stats(self, token_id: int) -> Optional[TokenStats]:
        """Get token statistics"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM token_stats WHERE token_id = ?", (token_id,))
            row = await cursor.fetchone()
            if row:
                return TokenStats(**dict(row))
            return None

    async def increment_image_count(self, token_id: int):
        """Increment image generation count with daily reset"""
        async with self._connect(write=True) as db:
            today = self._current_stats_date()
            # Get current stats
            cursor = await db.execute("SELECT today_date FROM token_stats WHERE token_id = ?", (token_id,))
            row = await cursor.fetchone()

            # If date changed, reset all daily counters before recording today's image usage.
            if row and row[0] != today:
                await db.execute("""
                    UPDATE token_stats
                    SET image_count = image_count + 1,
                        today_image_count = 1,
                        today_video_count = 0,
                        today_error_count = 0,
                        today_date = ?
                    WHERE token_id = ?
                """, (today, token_id))
            else:
                # Same day, just increment both
                await db.execute("""
                    UPDATE token_stats
                    SET image_count = image_count + 1,
                        today_image_count = today_image_count + 1,
                        today_date = ?
                    WHERE token_id = ?
                """, (today, token_id))
            await db.commit()

    async def increment_video_count(self, token_id: int):
        """Increment video generation count with daily reset"""
        async with self._connect(write=True) as db:
            today = self._current_stats_date()
            # Get current stats
            cursor = await db.execute("SELECT today_date FROM token_stats WHERE token_id = ?", (token_id,))
            row = await cursor.fetchone()

            # If date changed, reset all daily counters before recording today's video usage.
            if row and row[0] != today:
                await db.execute("""
                    UPDATE token_stats
                    SET video_count = video_count + 1,
                        today_image_count = 0,
                        today_video_count = 1,
                        today_error_count = 0,
                        today_date = ?
                    WHERE token_id = ?
                """, (today, token_id))
            else:
                # Same day, just increment both
                await db.execute("""
                    UPDATE token_stats
                    SET video_count = video_count + 1,
                        today_video_count = today_video_count + 1,
                        today_date = ?
                    WHERE token_id = ?
                """, (today, token_id))
            await db.commit()

    async def increment_error_count(self, token_id: int):
        """Increment error count with daily reset

        Updates two counters:
        - error_count: Historical total errors (never reset)
        - consecutive_error_count: Consecutive errors (reset on success/enable)
        - today_error_count: Today's errors (reset on date change)
        """
        async with self._connect(write=True) as db:
            today = self._current_stats_date()
            # Get current stats
            cursor = await db.execute("SELECT today_date FROM token_stats WHERE token_id = ?", (token_id,))
            row = await cursor.fetchone()

            # If date changed, reset all daily counters before recording today's error.
            if row and row[0] != today:
                await db.execute("""
                    UPDATE token_stats
                    SET error_count = error_count + 1,
                        consecutive_error_count = consecutive_error_count + 1,
                        today_image_count = 0,
                        today_video_count = 0,
                        today_error_count = 1,
                        today_date = ?,
                        last_error_at = CURRENT_TIMESTAMP
                    WHERE token_id = ?
                """, (today, token_id))
            else:
                # Same day, just increment all counters
                await db.execute("""
                    UPDATE token_stats
                    SET error_count = error_count + 1,
                        consecutive_error_count = consecutive_error_count + 1,
                        today_error_count = today_error_count + 1,
                        today_date = ?,
                        last_error_at = CURRENT_TIMESTAMP
                    WHERE token_id = ?
                """, (today, token_id))
            await db.commit()

    async def reset_error_count(self, token_id: int):
        """Reset consecutive error count (only reset consecutive_error_count, keep error_count and today_error_count)

        This is called when:
        - Token is manually enabled by admin
        - Request succeeds (resets consecutive error counter)

        Note: error_count (total historical errors) is NEVER reset
        """
        async with self._connect(write=True) as db:
            await db.execute("""
                UPDATE token_stats SET consecutive_error_count = 0 WHERE token_id = ?
            """, (token_id,))
            await db.commit()

    # Config operations
    async def get_admin_config(self) -> Optional[AdminConfig]:
        """Get admin configuration"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM admin_config WHERE id = 1")
            row = await cursor.fetchone()
            if row:
                return AdminConfig(**dict(row))
            return None

    async def update_admin_config(self, **kwargs):
        """Update admin configuration"""
        async with self._connect(write=True) as db:
            updates = []
            params = []

            for key, value in kwargs.items():
                if value is not None:
                    updates.append(f"{key} = ?")
                    params.append(value)

            if updates:
                updates.append("updated_at = CURRENT_TIMESTAMP")
                query = f"UPDATE admin_config SET {', '.join(updates)} WHERE id = 1"
                await db.execute(query, params)
                await db.commit()

    async def insert_admin_session(self, token: str, expires_at_unix: int) -> None:
        now = int(datetime.now().timestamp())
        async with self._connect(write=True) as db:
            await db.execute(
                """
                INSERT INTO admin_sessions (token, created_at, expires_at, last_used_at)
                VALUES (?, ?, ?, ?)
                """,
                (token, now, expires_at_unix, now),
            )
            await db.commit()

    async def is_admin_session_valid(self, token: str) -> bool:
        now = int(datetime.now().timestamp())
        async with self._connect(write=True) as db:
            cursor = await db.execute(
                "SELECT expires_at FROM admin_sessions WHERE token = ?",
                (token,),
            )
            row = await cursor.fetchone()
            if not row:
                return False
            expires_at = int(row[0])
            if expires_at <= now:
                await db.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
                await db.commit()
                return False
            await db.execute(
                "UPDATE admin_sessions SET last_used_at = ? WHERE token = ?",
                (now, token),
            )
            await db.commit()
            return True

    async def delete_admin_session(self, token: str) -> None:
        async with self._connect(write=True) as db:
            await db.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
            await db.commit()

    async def delete_all_admin_sessions(self) -> None:
        async with self._connect(write=True) as db:
            await db.execute("DELETE FROM admin_sessions")
            await db.commit()

    async def get_proxy_config(self) -> Optional[ProxyConfig]:
        """Get proxy configuration"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM proxy_config WHERE id = 1")
            row = await cursor.fetchone()
            if row:
                return ProxyConfig(**dict(row))
            return None

    async def update_proxy_config(
        self,
        enabled: bool,
        proxy_url: Optional[str] = None,
        media_proxy_enabled: Optional[bool] = None,
        media_proxy_url: Optional[str] = None
    ):
        """Update proxy configuration"""
        async with self._connect(write=True) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM proxy_config WHERE id = 1")
            row = await cursor.fetchone()

            if row:
                current = dict(row)
                new_media_proxy_enabled = (
                    media_proxy_enabled
                    if media_proxy_enabled is not None
                    else current.get("media_proxy_enabled", False)
                )
                new_media_proxy_url = (
                    media_proxy_url
                    if media_proxy_url is not None
                    else current.get("media_proxy_url")
                )

                await db.execute("""
                    UPDATE proxy_config
                    SET enabled = ?, proxy_url = ?,
                        media_proxy_enabled = ?, media_proxy_url = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """, (enabled, proxy_url, new_media_proxy_enabled, new_media_proxy_url))
            else:
                new_media_proxy_enabled = media_proxy_enabled if media_proxy_enabled is not None else False
                new_media_proxy_url = media_proxy_url
                await db.execute("""
                    INSERT INTO proxy_config (id, enabled, proxy_url, media_proxy_enabled, media_proxy_url)
                    VALUES (1, ?, ?, ?, ?)
                """, (enabled, proxy_url, new_media_proxy_enabled, new_media_proxy_url))

            await db.commit()

    async def get_generation_config(self) -> Optional[GenerationConfig]:
        """Get generation configuration"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM generation_config WHERE id = 1")
            row = await cursor.fetchone()
            if row:
                return GenerationConfig(**dict(row))
            return None

    async def update_generation_config(
        self,
        image_timeout: Optional[int] = None,
        video_timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
    ):
        """Update generation configuration"""
        async with self._connect(write=True) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM generation_config WHERE id = 1")
            row = await cursor.fetchone()
            current = dict(row) if row else {}

            normalized_image_timeout = (
                image_timeout
                if image_timeout is not None
                else current.get("image_timeout", 300)
            )
            normalized_video_timeout = (
                video_timeout
                if video_timeout is not None
                else current.get("video_timeout", 1500)
            )
            try:
                normalized_max_retries = (
                    max(1, int(max_retries))
                    if max_retries is not None
                    else max(1, int(current.get("max_retries", 3)))
                )
            except Exception:
                normalized_max_retries = 3

            if row:
                await db.execute("""
                    UPDATE generation_config
                    SET image_timeout = ?, video_timeout = ?, max_retries = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """, (normalized_image_timeout, normalized_video_timeout, normalized_max_retries))
            else:
                await db.execute("""
                    INSERT INTO generation_config (id, image_timeout, video_timeout, max_retries)
                    VALUES (1, ?, ?, ?)
                """, (normalized_image_timeout, normalized_video_timeout, normalized_max_retries))
            await db.commit()

    async def get_call_logic_config(self) -> CallLogicConfig:
        """Get token call logic configuration."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM call_logic_config WHERE id = 1")
            row = await cursor.fetchone()
            if row:
                row_dict = dict(row)
                mode = row_dict.get("call_mode")
                if mode not in ("default", "polling"):
                    row_dict["call_mode"] = "polling" if row_dict.get("polling_mode_enabled") else "default"
                return CallLogicConfig(**row_dict)
            return CallLogicConfig(call_mode="default", polling_mode_enabled=False)

    async def update_call_logic_config(self, call_mode: str):
        """Update token call logic configuration."""
        normalized = "polling" if call_mode == "polling" else "default"
        polling_mode_enabled = normalized == "polling"
        async with self._connect(write=True) as db:
            await db.execute("""
                INSERT OR REPLACE INTO call_logic_config (id, call_mode, polling_mode_enabled, updated_at)
                VALUES (1, ?, ?, CURRENT_TIMESTAMP)
            """, (normalized, polling_mode_enabled))
            await db.commit()

    # Request log operations
    async def add_request_log(self, log: RequestLog) -> int:
        """Add request log and return log id"""
        async with self._connect(write=True) as db:
            cursor = await db.execute("""
                INSERT INTO request_logs (token_id, api_key_id, operation, request_body, response_body, status_code, duration, status_text, progress)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                log.token_id,
                log.api_key_id,
                log.operation,
                log.request_body,
                log.response_body,
                log.status_code,
                log.duration,
                log.status_text or "",
                log.progress,
            ))
            await db.commit()
            return cursor.lastrowid

    async def update_request_log(self, log_id: int, **kwargs):
        """Update an existing request log row."""
        if not kwargs:
            return

        allowed_fields = {
            "token_id",
            "api_key_id",
            "operation",
            "request_body",
            "response_body",
            "status_code",
            "duration",
            "status_text",
            "progress",
        }
        update_fields = {key: value for key, value in kwargs.items() if key in allowed_fields}
        if not update_fields:
            return

        clauses = []
        values = []
        for key, value in update_fields.items():
            clauses.append(f"{key} = ?")
            values.append(value)
        clauses.append("updated_at = CURRENT_TIMESTAMP")
        values.append(log_id)

        async with self._connect(write=True) as db:
            await db.execute(
                f"UPDATE request_logs SET {', '.join(clauses)} WHERE id = ?",
                values,
            )
            await db.commit()

    async def count_request_logs(
        self,
        token_id: Optional[int] = None,
        api_key_id: Optional[int] = None,
    ) -> int:
        """Count rows matching the same filters as get_logs."""
        async with self._connect() as db:
            if token_id is not None:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM request_logs WHERE token_id = ?",
                    (token_id,),
                )
            elif api_key_id is not None:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM request_logs WHERE api_key_id = ?",
                    (api_key_id,),
                )
            else:
                cursor = await db.execute("SELECT COUNT(*) FROM request_logs")
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    async def get_logs(
        self,
        limit: int = 100,
        offset: int = 0,
        token_id: Optional[int] = None,
        include_payload: bool = False,
        api_key_id: Optional[int] = None,
    ):
        """Get request logs with token info, optionally including payload fields"""
        safe_offset = max(0, int(offset or 0))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            payload_columns = "rl.request_body, rl.response_body," if include_payload else ""
            response_excerpt_column = "substr(COALESCE(rl.response_body, ''), 1, 2048) as response_body_excerpt,"
            has_status_text = await self._column_exists(db, "request_logs", "status_text")
            has_progress = await self._column_exists(db, "request_logs", "progress")
            has_updated_at = await self._column_exists(db, "request_logs", "updated_at")
            status_text_column = "rl.status_text," if has_status_text else "'' as status_text,"
            progress_column = "rl.progress," if has_progress else "0 as progress,"
            updated_at_column = "rl.updated_at," if has_updated_at else "rl.created_at as updated_at,"

            if token_id:
                cursor = await db.execute(f"""
                    SELECT
                        rl.id,
                        rl.token_id,
                        rl.api_key_id,
                        rl.operation,
                        {payload_columns}
                        {response_excerpt_column}
                        rl.status_code,
                        rl.duration,
                        {status_text_column}
                        {progress_column}
                        rl.created_at,
                        {updated_at_column}
                        t.email as token_email,
                        t.name as token_username
                    FROM request_logs rl
                    LEFT JOIN tokens t ON rl.token_id = t.id
                    WHERE rl.token_id = ?
                    ORDER BY rl.created_at DESC
                    LIMIT ? OFFSET ?
                """, (token_id, limit, safe_offset))
            elif api_key_id is not None:
                cursor = await db.execute(f"""
                    SELECT
                        rl.id,
                        rl.token_id,
                        rl.api_key_id,
                        rl.operation,
                        {payload_columns}
                        {response_excerpt_column}
                        rl.status_code,
                        rl.duration,
                        {status_text_column}
                        {progress_column}
                        rl.created_at,
                        {updated_at_column}
                        t.email as token_email,
                        t.name as token_username
                    FROM request_logs rl
                    LEFT JOIN tokens t ON rl.token_id = t.id
                    WHERE rl.api_key_id = ?
                    ORDER BY rl.created_at DESC
                    LIMIT ? OFFSET ?
                """, (api_key_id, limit, safe_offset))
            else:
                cursor = await db.execute(f"""
                    SELECT
                        rl.id,
                        rl.token_id,
                        rl.api_key_id,
                        rl.operation,
                        {payload_columns}
                        {response_excerpt_column}
                        rl.status_code,
                        rl.duration,
                        {status_text_column}
                        {progress_column}
                        rl.created_at,
                        {updated_at_column}
                        t.email as token_email,
                        t.name as token_username
                    FROM request_logs rl
                    LEFT JOIN tokens t ON rl.token_id = t.id
                    ORDER BY rl.created_at DESC
                    LIMIT ? OFFSET ?
                """, (limit, safe_offset))

            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_log_detail(self, log_id: int, api_key_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Get single request log detail including payload fields"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            has_status_text = await self._column_exists(db, "request_logs", "status_text")
            has_progress = await self._column_exists(db, "request_logs", "progress")
            has_updated_at = await self._column_exists(db, "request_logs", "updated_at")
            status_text_column = "rl.status_text," if has_status_text else "'' as status_text,"
            progress_column = "rl.progress," if has_progress else "0 as progress,"
            updated_at_column = "rl.updated_at," if has_updated_at else "rl.created_at as updated_at,"
            if api_key_id is None:
                cursor = await db.execute(f"""
                    SELECT
                        rl.id,
                        rl.token_id,
                        rl.api_key_id,
                        rl.operation,
                        rl.request_body,
                        rl.response_body,
                        rl.status_code,
                        rl.duration,
                        {status_text_column}
                        {progress_column}
                        rl.created_at,
                        {updated_at_column}
                        t.email as token_email,
                        t.name as token_username
                    FROM request_logs rl
                    LEFT JOIN tokens t ON rl.token_id = t.id
                    WHERE rl.id = ?
                    LIMIT 1
                """, (log_id,))
            else:
                cursor = await db.execute(f"""
                    SELECT
                        rl.id,
                        rl.token_id,
                        rl.api_key_id,
                        rl.operation,
                        rl.request_body,
                        rl.response_body,
                        rl.status_code,
                        rl.duration,
                        {status_text_column}
                        {progress_column}
                        rl.created_at,
                        {updated_at_column}
                        t.email as token_email,
                        t.name as token_username
                    FROM request_logs rl
                    LEFT JOIN tokens t ON rl.token_id = t.id
                    WHERE rl.id = ? AND rl.api_key_id = ?
                    LIMIT 1
                """, (log_id, api_key_id))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def clear_all_logs(self):
        """Clear all request logs"""
        async with self._connect(write=True) as db:
            await db.execute("DELETE FROM request_logs")
            await db.commit()

    async def upsert_cache_file(
        self,
        *,
        filename: str,
        api_key_id: int,
        token_id: Optional[int],
        media_type: str,
        source_url: Optional[str] = None,
        flow_project_id: Optional[str] = None,
    ):
        """Upsert cache file ownership metadata."""
        async with self._connect(write=True) as db:
            await db.execute(
                """
                INSERT INTO cache_files (filename, api_key_id, token_id, flow_project_id, media_type, source_url, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(filename) DO UPDATE SET
                    api_key_id = excluded.api_key_id,
                    token_id = excluded.token_id,
                    flow_project_id = excluded.flow_project_id,
                    media_type = excluded.media_type,
                    source_url = excluded.source_url,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (filename, api_key_id, token_id, flow_project_id, media_type, source_url),
            )
            await db.commit()

    async def get_cache_file(self, filename: str) -> Optional[Dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM cache_files WHERE filename = ? LIMIT 1",
                (filename,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_cache_file_for_api_key(self, filename: str, api_key_id: int) -> Optional[Dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM cache_files WHERE filename = ? AND api_key_id = ? LIMIT 1",
                (filename, api_key_id),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list_cache_files_for_api_key(
        self,
        api_key_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List cache_files rows for a managed API key (newest first)."""
        lim = max(1, min(int(limit), 500))
        off = max(0, int(offset))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM cache_files
                WHERE api_key_id = ?
                ORDER BY datetime(updated_at) DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (api_key_id, lim, off),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def count_cache_files_for_api_key(self, api_key_id: int) -> int:
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM cache_files WHERE api_key_id = ?",
                (api_key_id,),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def list_cache_files_for_api_key_project(
        self,
        api_key_id: int,
        flow_project_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List cache_files rows for a managed API key scoped to one Flow project UUID."""
        lim = max(1, min(int(limit), 500))
        off = max(0, int(offset))
        pid = (flow_project_id or "").strip()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM cache_files
                WHERE api_key_id = ? AND flow_project_id = ?
                ORDER BY datetime(updated_at) DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (api_key_id, pid, lim, off),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def count_cache_files_for_api_key_project(
        self, api_key_id: int, flow_project_id: str
    ) -> int:
        pid = (flow_project_id or "").strip()
        async with self._connect() as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM cache_files
                WHERE api_key_id = ? AND flow_project_id = ?
                """,
                (api_key_id, pid),
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def init_config_from_toml(self, config_dict: dict, is_first_startup: bool = True):
        """
        Initialize database configuration from setting.toml

        Args:
            config_dict: Configuration dictionary from setting.toml
            is_first_startup: If True, initialize all config rows from setting.toml.
                            If False (upgrade mode), only ensure missing config rows exist with default values.
        """
        async with self._connect(write=True) as db:
            if is_first_startup:
                # First startup: Initialize all config tables with values from setting.toml
                await self._ensure_config_rows(db, config_dict)
            else:
                # Upgrade mode: Only ensure missing config rows exist (with default values, not from TOML)
                await self._ensure_config_rows(db, config_dict=None)

            await db.commit()

    async def reload_config_to_memory(self):
        """
        Reload all configuration from database to in-memory Config instance.
        This should be called after any configuration update to ensure hot-reload.

        Includes:
        - Admin config (username, password, api_key)
        - Cache config (enabled, timeout, base_url)
        - Generation config (image_timeout, video_timeout)
        - Proxy config will be handled by ProxyManager
        """
        from .config import config

        # Reload admin config
        admin_config = await self.get_admin_config()
        if admin_config:
            config.set_admin_username_from_db(admin_config.username)
            config.set_admin_password_from_db(admin_config.password)
            config.api_key = admin_config.api_key

        # Reload cache config
        cache_config = await self.get_cache_config()
        if cache_config:
            config.set_cache_enabled(cache_config.cache_enabled)
            config.set_cache_timeout(cache_config.cache_timeout)
            config.set_cache_base_url(cache_config.cache_base_url or "")

        # Reload generation config
        generation_config = await self.get_generation_config()
        if generation_config:
            config.set_image_timeout(generation_config.image_timeout)
            config.set_video_timeout(generation_config.video_timeout)
            config.set_flow_max_retries(generation_config.max_retries)

        # Reload call logic config
        call_logic_config = await self.get_call_logic_config()
        if call_logic_config:
            config.set_call_logic_mode(call_logic_config.call_mode)

        # Reload debug config
        debug_config = await self.get_debug_config()
        if debug_config:
            config.set_debug_enabled(debug_config.enabled)

        # Reload captcha config
        captcha_config = await self.get_captcha_config()
        if captcha_config:
            config.set_captcha_method(captcha_config.captcha_method)
            config.set_yescaptcha_api_key(captcha_config.yescaptcha_api_key)
            config.set_yescaptcha_base_url(captcha_config.yescaptcha_base_url)
            config.set_capmonster_api_key(captcha_config.capmonster_api_key)
            config.set_capmonster_base_url(captcha_config.capmonster_base_url)
            config.set_ezcaptcha_api_key(captcha_config.ezcaptcha_api_key)
            config.set_ezcaptcha_base_url(captcha_config.ezcaptcha_base_url)
            config.set_capsolver_api_key(captcha_config.capsolver_api_key)
            config.set_capsolver_base_url(captcha_config.capsolver_base_url)
            config.set_remote_browser_base_url(captcha_config.remote_browser_base_url)
            config.set_remote_browser_api_key(captcha_config.remote_browser_api_key)
            config.set_remote_browser_timeout(captcha_config.remote_browser_timeout)
            config.set_browser_fallback_to_remote_browser(
                bool(getattr(captcha_config, "browser_fallback_to_remote_browser", True))
            )
            config.set_personal_project_pool_size(captcha_config.personal_project_pool_size)
            config.set_personal_max_resident_tabs(captcha_config.personal_max_resident_tabs)
            config.set_personal_idle_tab_ttl_seconds(captcha_config.personal_idle_tab_ttl_seconds)
            config.set_browser_captcha_page_url(
                getattr(captcha_config, "browser_captcha_page_url", None) or ""
            )
            config.set_session_refresh_enabled(bool(getattr(captcha_config, "session_refresh_enabled", True)))
            config.set_session_refresh_browser_first(bool(getattr(captcha_config, "session_refresh_browser_first", True)))
            config.set_session_refresh_inject_st_cookie(bool(getattr(captcha_config, "session_refresh_inject_st_cookie", True)))
            config.set_session_refresh_warmup_urls(getattr(captcha_config, "session_refresh_warmup_urls", ""))
            config.set_session_refresh_wait_seconds_per_url(
                int(getattr(captcha_config, "session_refresh_wait_seconds_per_url", 60) or 60)
            )
            config.set_session_refresh_overall_timeout_seconds(
                int(getattr(captcha_config, "session_refresh_overall_timeout_seconds", 180) or 180)
            )
            config.set_session_refresh_update_st_from_cookie(
                bool(getattr(captcha_config, "session_refresh_update_st_from_cookie", True))
            )
            config.set_session_refresh_fail_if_st_refresh_fails(
                bool(getattr(captcha_config, "session_refresh_fail_if_st_refresh_fails", True))
            )
            config.set_session_refresh_local_only(bool(getattr(captcha_config, "session_refresh_local_only", True)))
            config.set_session_refresh_scheduler_enabled(
                bool(getattr(captcha_config, "session_refresh_scheduler_enabled", False))
            )
            config.set_session_refresh_scheduler_interval_minutes(
                int(getattr(captcha_config, "session_refresh_scheduler_interval_minutes", 30) or 30)
            )
            config.set_session_refresh_scheduler_batch_size(
                int(getattr(captcha_config, "session_refresh_scheduler_batch_size", 10) or 10)
            )
            config.set_session_refresh_scheduler_only_expiring_within_minutes(
                int(getattr(captcha_config, "session_refresh_scheduler_only_expiring_within_minutes", 60) or 60)
            )
            config.set_dedicated_extension_enabled(
                bool(getattr(captcha_config, "dedicated_extension_enabled", False))
            )
            config.set_dedicated_extension_captcha_timeout_seconds(
                int(getattr(captcha_config, "dedicated_extension_captcha_timeout_seconds", 25) or 25)
            )
            config.set_dedicated_extension_st_refresh_timeout_seconds(
                int(getattr(captcha_config, "dedicated_extension_st_refresh_timeout_seconds", 45) or 45)
            )
            config.set_extension_fallback_to_managed_on_dedicated_failure(
                bool(getattr(captcha_config, "extension_fallback_to_managed_on_dedicated_failure", False))
            )

    # Cache config operations
    async def get_cache_config(self) -> CacheConfig:
        """Get cache configuration"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM cache_config WHERE id = 1")
            row = await cursor.fetchone()
            if row:
                return CacheConfig(**dict(row))
            # Return default if not found
            return CacheConfig(cache_enabled=False, cache_timeout=7200)

    async def update_cache_config(self, enabled: bool = None, timeout: int = None, base_url: Optional[str] = None):
        """Update cache configuration"""
        async with self._connect(write=True) as db:
            db.row_factory = aiosqlite.Row
            # Get current values
            cursor = await db.execute("SELECT * FROM cache_config WHERE id = 1")
            row = await cursor.fetchone()

            if row:
                current = dict(row)
                # Use new values if provided, otherwise keep existing
                new_enabled = enabled if enabled is not None else current.get("cache_enabled", False)
                new_timeout = timeout if timeout is not None else current.get("cache_timeout", 7200)
                new_base_url = base_url if base_url is not None else current.get("cache_base_url")

                # If base_url is explicitly set to empty string, treat as None
                if base_url == "":
                    new_base_url = None

                await db.execute("""
                    UPDATE cache_config
                    SET cache_enabled = ?, cache_timeout = ?, cache_base_url = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """, (new_enabled, new_timeout, new_base_url))
            else:
                # Insert default row if not exists
                new_enabled = enabled if enabled is not None else False
                new_timeout = timeout if timeout is not None else 7200
                new_base_url = base_url if base_url is not None else None

                await db.execute("""
                    INSERT INTO cache_config (id, cache_enabled, cache_timeout, cache_base_url)
                    VALUES (1, ?, ?, ?)
                """, (new_enabled, new_timeout, new_base_url))

            await db.commit()

    # Debug config operations
    async def get_debug_config(self) -> 'DebugConfig':
        """Get debug configuration"""
        from .models import DebugConfig
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM debug_config WHERE id = 1")
            row = await cursor.fetchone()
            if row:
                return DebugConfig(**dict(row))
            # Return default if not found
            return DebugConfig(enabled=False, log_requests=True, log_responses=True, mask_token=True)

    async def update_debug_config(
        self,
        enabled: bool = None,
        log_requests: bool = None,
        log_responses: bool = None,
        mask_token: bool = None
    ):
        """Update debug configuration"""
        async with self._connect(write=True) as db:
            db.row_factory = aiosqlite.Row
            # Get current values
            cursor = await db.execute("SELECT * FROM debug_config WHERE id = 1")
            row = await cursor.fetchone()

            if row:
                current = dict(row)
                # Use new values if provided, otherwise keep existing
                new_enabled = enabled if enabled is not None else current.get("enabled", False)
                new_log_requests = log_requests if log_requests is not None else current.get("log_requests", True)
                new_log_responses = log_responses if log_responses is not None else current.get("log_responses", True)
                new_mask_token = mask_token if mask_token is not None else current.get("mask_token", True)

                await db.execute("""
                    UPDATE debug_config
                    SET enabled = ?, log_requests = ?, log_responses = ?, mask_token = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """, (new_enabled, new_log_requests, new_log_responses, new_mask_token))
            else:
                # Insert default row if not exists
                new_enabled = enabled if enabled is not None else False
                new_log_requests = log_requests if log_requests is not None else True
                new_log_responses = log_responses if log_responses is not None else True
                new_mask_token = mask_token if mask_token is not None else True

                await db.execute("""
                    INSERT INTO debug_config (id, enabled, log_requests, log_responses, mask_token)
                    VALUES (1, ?, ?, ?, ?)
                """, (new_enabled, new_log_requests, new_log_responses, new_mask_token))

            await db.commit()

    # Captcha config operations
    async def get_captcha_config(self) -> CaptchaConfig:
        """Get captcha configuration"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM captcha_config WHERE id = 1")
            row = await cursor.fetchone()
            if row:
                return CaptchaConfig(**dict(row))
            return CaptchaConfig()

    async def update_captcha_config(
        self,
        captcha_method: str = None,
        yescaptcha_api_key: str = None,
        yescaptcha_base_url: str = None,
        capmonster_api_key: str = None,
        capmonster_base_url: str = None,
        ezcaptcha_api_key: str = None,
        ezcaptcha_base_url: str = None,
        capsolver_api_key: str = None,
        capsolver_base_url: str = None,
        remote_browser_base_url: str = None,
        remote_browser_api_key: str = None,
        remote_browser_timeout: int = None,
        browser_fallback_to_remote_browser: bool = None,
        browser_proxy_enabled: bool = None,
        browser_proxy_url: str = None,
        browser_count: int = None,
        personal_project_pool_size: int = None,
        personal_max_resident_tabs: int = None,
        personal_idle_tab_ttl_seconds: int = None,
        browser_captcha_page_url: str = None,
        session_refresh_enabled: bool = None,
        session_refresh_browser_first: bool = None,
        session_refresh_inject_st_cookie: bool = None,
        session_refresh_warmup_urls: str = None,
        session_refresh_wait_seconds_per_url: int = None,
        session_refresh_overall_timeout_seconds: int = None,
        session_refresh_update_st_from_cookie: bool = None,
        session_refresh_fail_if_st_refresh_fails: bool = None,
        session_refresh_local_only: bool = None,
        session_refresh_scheduler_enabled: bool = None,
        session_refresh_scheduler_interval_minutes: int = None,
        session_refresh_scheduler_batch_size: int = None,
        session_refresh_scheduler_only_expiring_within_minutes: int = None,
        extension_queue_wait_timeout_seconds: int = None,
        dedicated_extension_enabled: bool = None,
        dedicated_extension_captcha_timeout_seconds: int = None,
        dedicated_extension_st_refresh_timeout_seconds: int = None,
        extension_fallback_to_managed_on_dedicated_failure: bool = None,
    ):
        """Update captcha configuration"""
        async with self._connect(write=True) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM captcha_config WHERE id = 1")
            row = await cursor.fetchone()

            if row:
                current = dict(row)
                new_method = captcha_method if captcha_method is not None else current.get("captcha_method", "yescaptcha")
                new_yes_key = yescaptcha_api_key if yescaptcha_api_key is not None else current.get("yescaptcha_api_key", "")
                new_yes_url = yescaptcha_base_url if yescaptcha_base_url is not None else current.get("yescaptcha_base_url", "https://api.yescaptcha.com")
                new_cap_key = capmonster_api_key if capmonster_api_key is not None else current.get("capmonster_api_key", "")
                new_cap_url = capmonster_base_url if capmonster_base_url is not None else current.get("capmonster_base_url", "https://api.capmonster.cloud")
                new_ez_key = ezcaptcha_api_key if ezcaptcha_api_key is not None else current.get("ezcaptcha_api_key", "")
                new_ez_url = ezcaptcha_base_url if ezcaptcha_base_url is not None else current.get("ezcaptcha_base_url", "https://api.ez-captcha.com")
                new_cs_key = capsolver_api_key if capsolver_api_key is not None else current.get("capsolver_api_key", "")
                new_cs_url = capsolver_base_url if capsolver_base_url is not None else current.get("capsolver_base_url", "https://api.capsolver.com")
                new_remote_base_url = remote_browser_base_url if remote_browser_base_url is not None else current.get("remote_browser_base_url", "")
                new_remote_api_key = remote_browser_api_key if remote_browser_api_key is not None else current.get("remote_browser_api_key", "")
                new_remote_timeout = remote_browser_timeout if remote_browser_timeout is not None else current.get("remote_browser_timeout", 60)
                new_browser_fallback = (
                    browser_fallback_to_remote_browser
                    if browser_fallback_to_remote_browser is not None
                    else current.get("browser_fallback_to_remote_browser", True)
                )
                new_proxy_enabled = browser_proxy_enabled if browser_proxy_enabled is not None else current.get("browser_proxy_enabled", False)
                new_proxy_url = browser_proxy_url if browser_proxy_url is not None else current.get("browser_proxy_url")
                new_browser_count = browser_count if browser_count is not None else current.get("browser_count", 1)
                new_personal_project_pool_size = personal_project_pool_size if personal_project_pool_size is not None else current.get("personal_project_pool_size", 4)
                new_personal_max_tabs = personal_max_resident_tabs if personal_max_resident_tabs is not None else current.get("personal_max_resident_tabs", 5)
                new_personal_idle_ttl = personal_idle_tab_ttl_seconds if personal_idle_tab_ttl_seconds is not None else current.get("personal_idle_tab_ttl_seconds", 600)
                default_page_url = "https://labs.google/fx/api/auth/providers"
                new_browser_captcha_page_url = (
                    browser_captcha_page_url
                    if browser_captcha_page_url is not None
                    else current.get("browser_captcha_page_url", default_page_url)
                )
                new_browser_captcha_page_url = (new_browser_captcha_page_url or default_page_url).strip() or default_page_url
                new_session_refresh_enabled = (
                    session_refresh_enabled
                    if session_refresh_enabled is not None
                    else current.get("session_refresh_enabled", True)
                )
                new_session_refresh_browser_first = (
                    session_refresh_browser_first
                    if session_refresh_browser_first is not None
                    else current.get("session_refresh_browser_first", True)
                )
                new_session_refresh_inject_st_cookie = (
                    session_refresh_inject_st_cookie
                    if session_refresh_inject_st_cookie is not None
                    else current.get("session_refresh_inject_st_cookie", True)
                )
                new_session_refresh_warmup_urls = (
                    session_refresh_warmup_urls
                    if session_refresh_warmup_urls is not None
                    else current.get(
                        "session_refresh_warmup_urls",
                        "https://labs.google/fx/tools/flow,https://labs.google/fx",
                    )
                )
                new_session_refresh_wait_seconds_per_url = (
                    session_refresh_wait_seconds_per_url
                    if session_refresh_wait_seconds_per_url is not None
                    else current.get("session_refresh_wait_seconds_per_url", 60)
                )
                new_session_refresh_overall_timeout_seconds = (
                    session_refresh_overall_timeout_seconds
                    if session_refresh_overall_timeout_seconds is not None
                    else current.get("session_refresh_overall_timeout_seconds", 180)
                )
                new_session_refresh_update_st_from_cookie = (
                    session_refresh_update_st_from_cookie
                    if session_refresh_update_st_from_cookie is not None
                    else current.get("session_refresh_update_st_from_cookie", True)
                )
                new_session_refresh_fail_if_st_refresh_fails = (
                    session_refresh_fail_if_st_refresh_fails
                    if session_refresh_fail_if_st_refresh_fails is not None
                    else current.get("session_refresh_fail_if_st_refresh_fails", True)
                )
                new_session_refresh_local_only = (
                    session_refresh_local_only
                    if session_refresh_local_only is not None
                    else current.get("session_refresh_local_only", True)
                )
                new_session_refresh_scheduler_enabled = (
                    session_refresh_scheduler_enabled
                    if session_refresh_scheduler_enabled is not None
                    else current.get("session_refresh_scheduler_enabled", False)
                )
                new_session_refresh_scheduler_interval_minutes = (
                    session_refresh_scheduler_interval_minutes
                    if session_refresh_scheduler_interval_minutes is not None
                    else current.get("session_refresh_scheduler_interval_minutes", 30)
                )
                new_session_refresh_scheduler_batch_size = (
                    session_refresh_scheduler_batch_size
                    if session_refresh_scheduler_batch_size is not None
                    else current.get("session_refresh_scheduler_batch_size", 10)
                )
                new_session_refresh_scheduler_only_expiring_within_minutes = (
                    session_refresh_scheduler_only_expiring_within_minutes
                    if session_refresh_scheduler_only_expiring_within_minutes is not None
                    else current.get("session_refresh_scheduler_only_expiring_within_minutes", 60)
                )
                new_extension_queue_wait_timeout = (
                    extension_queue_wait_timeout_seconds
                    if extension_queue_wait_timeout_seconds is not None
                    else current.get("extension_queue_wait_timeout_seconds", 20)
                )
                new_dedicated_extension_enabled = (
                    dedicated_extension_enabled
                    if dedicated_extension_enabled is not None
                    else current.get("dedicated_extension_enabled", False)
                )
                new_dedicated_extension_captcha_timeout = (
                    dedicated_extension_captcha_timeout_seconds
                    if dedicated_extension_captcha_timeout_seconds is not None
                    else current.get("dedicated_extension_captcha_timeout_seconds", 25)
                )
                new_dedicated_extension_st_refresh_timeout = (
                    dedicated_extension_st_refresh_timeout_seconds
                    if dedicated_extension_st_refresh_timeout_seconds is not None
                    else current.get("dedicated_extension_st_refresh_timeout_seconds", 45)
                )
                new_remote_timeout = max(5, int(new_remote_timeout)) if new_remote_timeout is not None else 60
                new_personal_project_pool_size = max(1, min(50, int(new_personal_project_pool_size)))
                new_personal_max_tabs = max(1, min(50, int(new_personal_max_tabs)))  # 限制1-50
                new_personal_idle_ttl = max(60, int(new_personal_idle_ttl))  # 最少60秒
                new_session_refresh_wait_seconds_per_url = max(0, min(600, int(new_session_refresh_wait_seconds_per_url)))
                new_session_refresh_overall_timeout_seconds = max(10, min(1800, int(new_session_refresh_overall_timeout_seconds)))
                new_session_refresh_scheduler_interval_minutes = max(1, min(1440, int(new_session_refresh_scheduler_interval_minutes)))
                new_session_refresh_scheduler_batch_size = max(1, min(200, int(new_session_refresh_scheduler_batch_size)))
                new_session_refresh_scheduler_only_expiring_within_minutes = max(
                    1, min(10080, int(new_session_refresh_scheduler_only_expiring_within_minutes))
                )
                new_extension_queue_wait_timeout = max(1, min(120, int(new_extension_queue_wait_timeout)))
                new_dedicated_extension_captcha_timeout = max(
                    5, min(180, int(new_dedicated_extension_captcha_timeout))
                )
                new_dedicated_extension_st_refresh_timeout = max(
                    10, min(300, int(new_dedicated_extension_st_refresh_timeout))
                )
                new_extension_fallback_to_managed_on_dedicated_failure = (
                    bool(extension_fallback_to_managed_on_dedicated_failure)
                    if extension_fallback_to_managed_on_dedicated_failure is not None
                    else bool(
                        current.get("extension_fallback_to_managed_on_dedicated_failure", False)
                    )
                )
                new_session_refresh_warmup_urls = (
                    str(new_session_refresh_warmup_urls or "").strip()
                    or "https://labs.google/fx/tools/flow,https://labs.google/fx"
                )

                await db.execute("""
                    UPDATE captcha_config
                    SET captcha_method = ?, yescaptcha_api_key = ?, yescaptcha_base_url = ?,
                        capmonster_api_key = ?, capmonster_base_url = ?,
                        ezcaptcha_api_key = ?, ezcaptcha_base_url = ?,
                        capsolver_api_key = ?, capsolver_base_url = ?,
                        remote_browser_base_url = ?, remote_browser_api_key = ?, remote_browser_timeout = ?,
                        browser_fallback_to_remote_browser = ?,
                        browser_proxy_enabled = ?, browser_proxy_url = ?, browser_count = ?,
                        personal_project_pool_size = ?,
                        personal_max_resident_tabs = ?, personal_idle_tab_ttl_seconds = ?,
                        browser_captcha_page_url = ?,
                        session_refresh_enabled = ?, session_refresh_browser_first = ?,
                        session_refresh_inject_st_cookie = ?, session_refresh_warmup_urls = ?,
                        session_refresh_wait_seconds_per_url = ?, session_refresh_overall_timeout_seconds = ?,
                        session_refresh_update_st_from_cookie = ?, session_refresh_fail_if_st_refresh_fails = ?,
                        session_refresh_local_only = ?, session_refresh_scheduler_enabled = ?,
                        session_refresh_scheduler_interval_minutes = ?, session_refresh_scheduler_batch_size = ?,
                        session_refresh_scheduler_only_expiring_within_minutes = ?,
                        extension_queue_wait_timeout_seconds = ?,
                        dedicated_extension_enabled = ?,
                        dedicated_extension_captcha_timeout_seconds = ?,
                        dedicated_extension_st_refresh_timeout_seconds = ?,
                        extension_fallback_to_managed_on_dedicated_failure = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """, (new_method, new_yes_key, new_yes_url, new_cap_key, new_cap_url,
                      new_ez_key, new_ez_url, new_cs_key, new_cs_url,
                      (new_remote_base_url or "").strip(), (new_remote_api_key or "").strip(), new_remote_timeout,
                      bool(new_browser_fallback),
                      new_proxy_enabled, new_proxy_url, new_browser_count, new_personal_project_pool_size,
                      new_personal_max_tabs, new_personal_idle_ttl, new_browser_captcha_page_url,
                      bool(new_session_refresh_enabled), bool(new_session_refresh_browser_first),
                      bool(new_session_refresh_inject_st_cookie), new_session_refresh_warmup_urls,
                      new_session_refresh_wait_seconds_per_url, new_session_refresh_overall_timeout_seconds,
                      bool(new_session_refresh_update_st_from_cookie),
                      bool(new_session_refresh_fail_if_st_refresh_fails),
                      bool(new_session_refresh_local_only), bool(new_session_refresh_scheduler_enabled),
                      new_session_refresh_scheduler_interval_minutes, new_session_refresh_scheduler_batch_size,
                      new_session_refresh_scheduler_only_expiring_within_minutes,
                      new_extension_queue_wait_timeout,
                      bool(new_dedicated_extension_enabled),
                      new_dedicated_extension_captcha_timeout,
                      new_dedicated_extension_st_refresh_timeout,
                      bool(new_extension_fallback_to_managed_on_dedicated_failure)))
            else:
                new_method = captcha_method if captcha_method is not None else "yescaptcha"
                new_yes_key = yescaptcha_api_key if yescaptcha_api_key is not None else ""
                new_yes_url = yescaptcha_base_url if yescaptcha_base_url is not None else "https://api.yescaptcha.com"
                new_cap_key = capmonster_api_key if capmonster_api_key is not None else ""
                new_cap_url = capmonster_base_url if capmonster_base_url is not None else "https://api.capmonster.cloud"
                new_ez_key = ezcaptcha_api_key if ezcaptcha_api_key is not None else ""
                new_ez_url = ezcaptcha_base_url if ezcaptcha_base_url is not None else "https://api.ez-captcha.com"
                new_cs_key = capsolver_api_key if capsolver_api_key is not None else ""
                new_cs_url = capsolver_base_url if capsolver_base_url is not None else "https://api.capsolver.com"
                new_remote_base_url = remote_browser_base_url if remote_browser_base_url is not None else ""
                new_remote_api_key = remote_browser_api_key if remote_browser_api_key is not None else ""
                new_remote_timeout = remote_browser_timeout if remote_browser_timeout is not None else 60
                new_browser_fallback = (
                    bool(browser_fallback_to_remote_browser)
                    if browser_fallback_to_remote_browser is not None
                    else True
                )
                new_proxy_enabled = browser_proxy_enabled if browser_proxy_enabled is not None else False
                new_proxy_url = browser_proxy_url
                new_browser_count = browser_count if browser_count is not None else 1
                new_personal_project_pool_size = personal_project_pool_size if personal_project_pool_size is not None else 4
                new_personal_max_tabs = personal_max_resident_tabs if personal_max_resident_tabs is not None else 5
                new_personal_idle_ttl = personal_idle_tab_ttl_seconds if personal_idle_tab_ttl_seconds is not None else 600
                default_page_url = "https://labs.google/fx/api/auth/providers"
                new_browser_captcha_page_url = (
                    (browser_captcha_page_url or default_page_url).strip()
                    if browser_captcha_page_url is not None
                    else default_page_url
                )
                new_browser_captcha_page_url = new_browser_captcha_page_url or default_page_url
                new_remote_timeout = max(5, int(new_remote_timeout))
                new_personal_project_pool_size = max(1, min(50, int(new_personal_project_pool_size)))
                new_personal_max_tabs = max(1, min(50, int(new_personal_max_tabs)))
                new_personal_idle_ttl = max(60, int(new_personal_idle_ttl))
                new_session_refresh_enabled = bool(session_refresh_enabled) if session_refresh_enabled is not None else True
                new_session_refresh_browser_first = bool(session_refresh_browser_first) if session_refresh_browser_first is not None else True
                new_session_refresh_inject_st_cookie = bool(session_refresh_inject_st_cookie) if session_refresh_inject_st_cookie is not None else True
                new_session_refresh_warmup_urls = (
                    str(session_refresh_warmup_urls or "").strip()
                    if session_refresh_warmup_urls is not None
                    else "https://labs.google/fx/tools/flow,https://labs.google/fx"
                ) or "https://labs.google/fx/tools/flow,https://labs.google/fx"
                new_session_refresh_wait_seconds_per_url = max(
                    0, min(600, int(session_refresh_wait_seconds_per_url if session_refresh_wait_seconds_per_url is not None else 60))
                )
                new_session_refresh_overall_timeout_seconds = max(
                    10, min(1800, int(session_refresh_overall_timeout_seconds if session_refresh_overall_timeout_seconds is not None else 180))
                )
                new_session_refresh_update_st_from_cookie = bool(session_refresh_update_st_from_cookie) if session_refresh_update_st_from_cookie is not None else True
                new_session_refresh_fail_if_st_refresh_fails = bool(session_refresh_fail_if_st_refresh_fails) if session_refresh_fail_if_st_refresh_fails is not None else True
                new_session_refresh_local_only = bool(session_refresh_local_only) if session_refresh_local_only is not None else True
                new_session_refresh_scheduler_enabled = bool(session_refresh_scheduler_enabled) if session_refresh_scheduler_enabled is not None else False
                new_session_refresh_scheduler_interval_minutes = max(
                    1, min(1440, int(session_refresh_scheduler_interval_minutes if session_refresh_scheduler_interval_minutes is not None else 30))
                )
                new_session_refresh_scheduler_batch_size = max(
                    1, min(200, int(session_refresh_scheduler_batch_size if session_refresh_scheduler_batch_size is not None else 10))
                )
                new_session_refresh_scheduler_only_expiring_within_minutes = max(
                    1,
                    min(
                        10080,
                        int(
                            session_refresh_scheduler_only_expiring_within_minutes
                            if session_refresh_scheduler_only_expiring_within_minutes is not None
                            else 60
                        ),
                    ),
                )
                new_extension_queue_wait_timeout = max(
                    1,
                    min(
                        120,
                        int(extension_queue_wait_timeout_seconds if extension_queue_wait_timeout_seconds is not None else 20),
                    ),
                )
                new_dedicated_extension_enabled = (
                    bool(dedicated_extension_enabled) if dedicated_extension_enabled is not None else False
                )
                new_dedicated_extension_captcha_timeout = max(
                    5,
                    min(
                        180,
                        int(
                            dedicated_extension_captcha_timeout_seconds
                            if dedicated_extension_captcha_timeout_seconds is not None
                            else 25
                        ),
                    ),
                )
                new_dedicated_extension_st_refresh_timeout = max(
                    10,
                    min(
                        300,
                        int(
                            dedicated_extension_st_refresh_timeout_seconds
                            if dedicated_extension_st_refresh_timeout_seconds is not None
                            else 45
                        ),
                    ),
                )
                new_extension_fallback_to_managed_on_dedicated_failure = (
                    bool(extension_fallback_to_managed_on_dedicated_failure)
                    if extension_fallback_to_managed_on_dedicated_failure is not None
                    else False
                )

                await db.execute("""
                    INSERT INTO captcha_config (id, captcha_method, yescaptcha_api_key, yescaptcha_base_url,
                        capmonster_api_key, capmonster_base_url, ezcaptcha_api_key, ezcaptcha_base_url,
                        capsolver_api_key, capsolver_base_url,
                        remote_browser_base_url, remote_browser_api_key, remote_browser_timeout,
                        browser_fallback_to_remote_browser,
                        browser_proxy_enabled, browser_proxy_url, browser_count,
                        personal_project_pool_size,
                        personal_max_resident_tabs, personal_idle_tab_ttl_seconds,
                        browser_captcha_page_url,
                        session_refresh_enabled, session_refresh_browser_first,
                        session_refresh_inject_st_cookie, session_refresh_warmup_urls,
                        session_refresh_wait_seconds_per_url, session_refresh_overall_timeout_seconds,
                        session_refresh_update_st_from_cookie, session_refresh_fail_if_st_refresh_fails,
                        session_refresh_local_only, session_refresh_scheduler_enabled,
                        session_refresh_scheduler_interval_minutes, session_refresh_scheduler_batch_size,
                        session_refresh_scheduler_only_expiring_within_minutes,
                        extension_queue_wait_timeout_seconds,
                        dedicated_extension_enabled, dedicated_extension_captcha_timeout_seconds,
                        dedicated_extension_st_refresh_timeout_seconds,
                        extension_fallback_to_managed_on_dedicated_failure)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (new_method, new_yes_key, new_yes_url, new_cap_key, new_cap_url,
                      new_ez_key, new_ez_url, new_cs_key, new_cs_url,
                      (new_remote_base_url or "").strip(), (new_remote_api_key or "").strip(), new_remote_timeout,
                      new_browser_fallback,
                      new_proxy_enabled, new_proxy_url, new_browser_count, new_personal_project_pool_size,
                      new_personal_max_tabs, new_personal_idle_ttl, new_browser_captcha_page_url,
                      new_session_refresh_enabled, new_session_refresh_browser_first,
                      new_session_refresh_inject_st_cookie, new_session_refresh_warmup_urls,
                      new_session_refresh_wait_seconds_per_url, new_session_refresh_overall_timeout_seconds,
                      new_session_refresh_update_st_from_cookie, new_session_refresh_fail_if_st_refresh_fails,
                      new_session_refresh_local_only, new_session_refresh_scheduler_enabled,
                      new_session_refresh_scheduler_interval_minutes, new_session_refresh_scheduler_batch_size,
                      new_session_refresh_scheduler_only_expiring_within_minutes,
                      new_extension_queue_wait_timeout, new_dedicated_extension_enabled,
                      new_dedicated_extension_captcha_timeout, new_dedicated_extension_st_refresh_timeout,
                      bool(new_extension_fallback_to_managed_on_dedicated_failure)))

            await db.commit()

    # Plugin config operations
    async def get_plugin_config(self) -> PluginConfig:
        """Get plugin configuration"""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM plugin_config WHERE id = 1")
            row = await cursor.fetchone()
            if row:
                return PluginConfig(**dict(row))
            return PluginConfig()

    async def update_plugin_config(self, connection_token: str, auto_enable_on_update: bool = True):
        """Update plugin configuration"""
        async with self._connect(write=True) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM plugin_config WHERE id = 1")
            row = await cursor.fetchone()

            if row:
                await db.execute("""
                    UPDATE plugin_config
                    SET connection_token = ?, auto_enable_on_update = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = 1
                """, (connection_token, auto_enable_on_update))
            else:
                await db.execute("""
                    INSERT INTO plugin_config (id, connection_token, auto_enable_on_update)
                    VALUES (1, ?, ?)
                """, (connection_token, auto_enable_on_update))

            await db.commit()

    # API key manager operations
    async def _get_or_create_api_client(self, client_name: str) -> int:
        async with self._connect(write=True) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT id FROM api_clients WHERE name = ?", (client_name,))
            row = await cursor.fetchone()
            if row:
                return int(row["id"])
            cursor = await db.execute(
                "INSERT INTO api_clients (name, is_active) VALUES (?, 1)",
                (client_name,),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def create_client_api_key(
        self,
        *,
        client_name: str,
        label: str,
        key_prefix: str,
        key_plaintext: Optional[str],
        key_hash: str,
        scopes: str,
        account_ids: List[int],
        endpoint_limits: Dict[str, Dict[str, int]],
        expires_at: Optional[str],
    ) -> int:
        client_id = await self._get_or_create_api_client(client_name)
        async with self._connect(write=True) as db:
            cursor = await db.execute(
                """
                INSERT INTO api_keys (client_id, label, key_prefix, key_plaintext, key_hash, scopes, is_active, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (client_id, label, key_prefix, key_plaintext, key_hash, scopes, expires_at),
            )
            key_id = int(cursor.lastrowid)

            for account_id in sorted({int(x) for x in account_ids if int(x) > 0}):
                await db.execute(
                    "INSERT OR IGNORE INTO api_key_accounts (api_key_id, account_id) VALUES (?, ?)",
                    (key_id, account_id),
                )

            for endpoint, values in (endpoint_limits or {}).items():
                rpm = int((values or {}).get("rpm") or 0)
                rph = int((values or {}).get("rph") or 0)
                burst = int((values or {}).get("burst") or 0)
                await db.execute(
                    """
                    INSERT OR REPLACE INTO api_key_rate_limits (api_key_id, endpoint, rpm, rph, burst, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (key_id, endpoint, rpm, rph, burst),
                )

            await db.commit()
            return key_id

    async def get_client_api_key_by_hash(self, key_hash: str) -> Optional[Dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT
                    k.*,
                    c.name AS client_name,
                    strftime('%s', k.expires_at) AS expires_unix
                FROM api_keys k
                JOIN api_clients c ON c.id = k.client_id
                WHERE k.key_hash = ?
                LIMIT 1
                """,
                (key_hash,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_api_key_account_ids(self, key_id: int, existing_only: bool = False) -> List[int]:
        async with self._connect() as db:
            if existing_only:
                cursor = await db.execute(
                    """
                    SELECT aka.account_id
                    FROM api_key_accounts aka
                    JOIN tokens t ON t.id = aka.account_id
                    WHERE aka.api_key_id = ?
                    ORDER BY aka.account_id ASC
                    """,
                    (key_id,),
                )
            else:
                cursor = await db.execute(
                    "SELECT account_id FROM api_key_accounts WHERE api_key_id = ? ORDER BY account_id ASC",
                    (key_id,),
                )
            rows = await cursor.fetchall()
            return [int(row[0]) for row in rows]

    async def prune_stale_api_key_accounts(self, key_id: int) -> int:
        """Remove api_key_accounts rows whose account_id no longer exists in tokens."""
        async with self._connect(write=True) as db:
            cursor = await db.execute(
                """
                DELETE FROM api_key_accounts
                WHERE api_key_id = ?
                  AND account_id NOT IN (SELECT id FROM tokens)
                """,
                (key_id,),
            )
            await db.commit()
            return int(cursor.rowcount or 0)

    async def get_api_key_rate_limits(self, key_id: int, endpoint: str) -> Dict[str, Any]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT rpm, rph, burst
                FROM api_key_rate_limits
                WHERE api_key_id = ? AND endpoint IN (?, '*')
                ORDER BY CASE WHEN endpoint = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (key_id, endpoint, endpoint),
            )
            row = await cursor.fetchone()
            return dict(row) if row else {}

    async def touch_api_key_usage(self, key_id: int):
        async with self._connect(write=True) as db:
            await db.execute(
                "UPDATE api_keys SET last_used_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (key_id,),
            )
            await db.commit()

    async def insert_api_key_audit_log(
        self,
        *,
        api_key_id: Optional[int],
        endpoint: str,
        account_id: Optional[int],
        status_code: int,
        detail: str,
        ip: str,
        user_agent: str,
    ):
        async with self._connect(write=True) as db:
            await db.execute(
                """
                INSERT INTO api_key_audit_logs (api_key_id, endpoint, account_id, status_code, detail, ip, user_agent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (api_key_id, endpoint, account_id, status_code, detail[:300], ip[:120], user_agent[:200]),
            )
            await db.commit()

    async def list_api_keys(self) -> List[Dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT
                    k.id,
                    c.name AS client_name,
                    k.label,
                    k.key_prefix,
                    CASE WHEN k.key_plaintext IS NOT NULL AND length(trim(k.key_plaintext)) > 0 THEN 1 ELSE 0 END AS can_reveal_plaintext,
                    k.scopes,
                    k.is_active,
                    k.expires_at,
                    k.last_used_at,
                    k.created_at
                FROM api_keys k
                JOIN api_clients c ON c.id = k.client_id
                ORDER BY k.created_at DESC
                """
            )
            rows = [dict(row) for row in await cursor.fetchall()]

            for row in rows:
                row["account_ids"] = await self.get_api_key_account_ids(
                    int(row["id"]),
                    existing_only=True,
                )
            return rows

    async def list_api_key_rate_limits(self, key_id: int) -> List[Dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT endpoint, rpm, rph, burst
                FROM api_key_rate_limits
                WHERE api_key_id = ?
                ORDER BY endpoint ASC
                """,
                (key_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def update_api_key(
        self,
        key_id: int,
        *,
        is_active: Optional[bool] = None,
        client_name: Optional[str] = None,
        label: Optional[str] = None,
        scopes: Optional[str] = None,
        expires_at: Optional[str] = None,
        account_ids: Optional[List[int]] = None,
        endpoint_limits: Optional[Dict[str, Dict[str, int]]] = None,
    ):
        # Resolve client outside the write transaction: _get_or_create_api_client also
        # takes _write_lock; nesting here deadlocks asyncio.Lock until Cloudflare 524.
        resolved_client_id: Optional[int] = None
        if client_name is not None:
            resolved_client_id = await self._get_or_create_api_client(client_name.strip())

        async with self._connect(write=True) as db:
            if resolved_client_id is not None:
                await db.execute(
                    "UPDATE api_keys SET client_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (resolved_client_id, key_id),
                )
            if label is not None:
                await db.execute(
                    "UPDATE api_keys SET label = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (label, key_id),
                )
            if is_active is not None:
                await db.execute(
                    "UPDATE api_keys SET is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (1 if is_active else 0, key_id),
                )
            if scopes is not None:
                await db.execute(
                    "UPDATE api_keys SET scopes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (scopes, key_id),
                )
            if expires_at is not None:
                await db.execute(
                    "UPDATE api_keys SET expires_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (expires_at, key_id),
                )
            if account_ids is not None:
                await db.execute("DELETE FROM api_key_accounts WHERE api_key_id = ?", (key_id,))
                for account_id in sorted({int(x) for x in account_ids if int(x) > 0}):
                    await db.execute(
                        "INSERT OR IGNORE INTO api_key_accounts (api_key_id, account_id) VALUES (?, ?)",
                        (key_id, account_id),
                    )
            if endpoint_limits is not None:
                await db.execute("DELETE FROM api_key_rate_limits WHERE api_key_id = ?", (key_id,))
                for endpoint, values in endpoint_limits.items():
                    await db.execute(
                        """
                        INSERT INTO api_key_rate_limits (api_key_id, endpoint, rpm, rph, burst)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            key_id,
                            endpoint,
                            int((values or {}).get("rpm") or 0),
                            int((values or {}).get("rph") or 0),
                            int((values or {}).get("burst") or 0),
                        ),
                    )
            await db.commit()

    async def get_api_key_detail(self, key_id: int, include_plaintext: bool = False) -> Optional[Dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            plaintext_col = "k.key_plaintext," if include_plaintext else "NULL AS key_plaintext,"
            cursor = await db.execute(
                f"""
                SELECT
                    k.id,
                    c.name AS client_name,
                    k.label,
                    k.key_prefix,
                    {plaintext_col}
                    k.scopes,
                    k.is_active,
                    k.expires_at,
                    k.last_used_at,
                    k.created_at
                FROM api_keys k
                JOIN api_clients c ON c.id = k.client_id
                WHERE k.id = ?
                LIMIT 1
                """,
                (key_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["account_ids"] = await self.get_api_key_account_ids(
                key_id,
                existing_only=True,
            )
            data["endpoint_limits"] = await self.list_api_key_rate_limits(key_id)
            return data

    async def delete_api_key(self, key_id: int):
        async with self._connect(write=True) as db:
            await db.execute("DELETE FROM api_key_accounts WHERE api_key_id = ?", (key_id,))
            await db.execute("DELETE FROM api_key_rate_limits WHERE api_key_id = ?", (key_id,))
            await db.execute("DELETE FROM api_key_audit_logs WHERE api_key_id = ?", (key_id,))
            await db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
            await db.commit()

    async def count_api_key_audit_logs(self, key_id: Optional[int] = None) -> int:
        async with self._connect() as db:
            if key_id is not None:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM api_key_audit_logs WHERE api_key_id = ?",
                    (key_id,),
                )
            else:
                cursor = await db.execute("SELECT COUNT(*) FROM api_key_audit_logs")
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    async def list_api_key_audit_logs(
        self,
        limit: int = 200,
        offset: int = 0,
        key_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 200), 500))
        safe_offset = max(0, int(offset or 0))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            if key_id is not None:
                cursor = await db.execute(
                    """
                    SELECT
                        l.id,
                        l.api_key_id,
                        k.key_prefix,
                        k.label,
                        l.endpoint,
                        l.account_id,
                        l.status_code,
                        l.detail,
                        l.ip,
                        l.user_agent,
                        l.created_at
                    FROM api_key_audit_logs l
                    LEFT JOIN api_keys k ON k.id = l.api_key_id
                    WHERE l.api_key_id = ?
                    ORDER BY l.created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (key_id, safe_limit, safe_offset),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT
                        l.id,
                        l.api_key_id,
                        k.key_prefix,
                        k.label,
                        l.endpoint,
                        l.account_id,
                        l.status_code,
                        l.detail,
                        l.ip,
                        l.user_agent,
                        l.created_at
                    FROM api_key_audit_logs l
                    LEFT JOIN api_keys k ON k.id = l.api_key_id
                    ORDER BY l.created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (safe_limit, safe_offset),
                )
            return [dict(row) for row in await cursor.fetchall()]

    async def get_extension_worker_binding_for_route_key(self, route_key: str) -> Optional[Dict[str, Any]]:
        normalized = (route_key or "").strip()
        if not normalized:
            return None
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT id, route_key, api_key_id, created_at, updated_at
                FROM extension_worker_bindings
                WHERE route_key = ?
                """,
                (normalized,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def upsert_extension_worker_binding(self, route_key: str, api_key_id: int) -> None:
        normalized = (route_key or "").strip()
        if not normalized:
            raise ValueError("route_key is required")
        normalized_key_id = int(api_key_id)
        async with self._connect(write=True) as db:
            await db.execute(
                """
                INSERT INTO extension_worker_bindings (route_key, api_key_id, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(route_key) DO UPDATE SET
                    api_key_id = excluded.api_key_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (normalized, normalized_key_id),
            )
            await db.commit()

    async def delete_extension_worker_binding(self, route_key: str) -> None:
        normalized = (route_key or "").strip()
        if not normalized:
            return
        async with self._connect(write=True) as db:
            await db.execute(
                "DELETE FROM extension_worker_bindings WHERE route_key = ?",
                (normalized,),
            )
            await db.commit()

    async def list_extension_worker_bindings(self) -> List[Dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT
                    b.id,
                    b.route_key,
                    b.api_key_id,
                    b.created_at,
                    b.updated_at,
                    k.label AS api_key_label
                FROM extension_worker_bindings b
                LEFT JOIN api_keys k ON k.id = b.api_key_id
                ORDER BY b.updated_at DESC, b.route_key ASC
                """
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_dedicated_extension_worker_by_key_hash(self, worker_key_hash: str) -> Optional[Dict[str, Any]]:
        normalized = (worker_key_hash or "").strip()
        if not normalized:
            return None
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT *
                FROM dedicated_extension_workers
                WHERE worker_key_hash = ?
                """,
                (normalized,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_dedicated_extension_worker(self, worker_id: int) -> Optional[Dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM dedicated_extension_workers WHERE id = ?",
                (int(worker_id),),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def create_dedicated_extension_worker(
        self,
        *,
        worker_key_prefix: str,
        worker_key_hash: str,
        label: str = "",
        token_id: Optional[int] = None,
        route_key: Optional[str] = None,
    ) -> int:
        async with self._connect(write=True) as db:
            cursor = await db.execute(
                """
                INSERT INTO dedicated_extension_workers (
                    worker_key_prefix, worker_key_hash, label, token_id, route_key
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (worker_key_prefix or "").strip(),
                    (worker_key_hash or "").strip(),
                    (label or "").strip(),
                    int(token_id) if token_id is not None else None,
                    (route_key or "").strip() or None,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def update_dedicated_extension_worker(
        self,
        worker_id: int,
        *,
        label: Optional[str] = None,
        token_id: Optional[int] = None,
        route_key: Optional[str] = None,
        is_active: Optional[bool] = None,
        last_instance_id: Optional[str] = None,
        last_error: Optional[str] = None,
        mark_seen: bool = False,
        clear_token_binding: bool = False,
    ) -> None:
        updates: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
        values: list[Any] = []
        if label is not None:
            updates.append("label = ?")
            values.append((label or "").strip())
        if token_id is not None:
            updates.append("token_id = ?")
            values.append(int(token_id))
        elif clear_token_binding:
            updates.append("token_id = NULL")
        if route_key is not None:
            updates.append("route_key = ?")
            values.append((route_key or "").strip() or None)
        if is_active is not None:
            updates.append("is_active = ?")
            values.append(bool(is_active))
        if last_instance_id is not None:
            updates.append("last_instance_id = ?")
            values.append((last_instance_id or "").strip() or None)
        if last_error is not None:
            updates.append("last_error = ?")
            values.append((last_error or "").strip() or None)
        if mark_seen:
            updates.append("last_seen_at = CURRENT_TIMESTAMP")
        values.append(int(worker_id))
        async with self._connect(write=True) as db:
            await db.execute(
                f"UPDATE dedicated_extension_workers SET {', '.join(updates)} WHERE id = ?",
                tuple(values),
            )
            await db.commit()

    async def delete_dedicated_extension_worker(self, worker_id: int) -> None:
        async with self._connect(write=True) as db:
            await db.execute("DELETE FROM dedicated_extension_workers WHERE id = ?", (int(worker_id),))
            await db.commit()

    async def list_dedicated_extension_workers(self) -> List[Dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT
                    w.*,
                    t.email AS token_email,
                    t.remark AS token_remark
                FROM dedicated_extension_workers w
                LEFT JOIN tokens t ON t.id = w.token_id
                ORDER BY w.updated_at DESC, w.id DESC
                """
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
