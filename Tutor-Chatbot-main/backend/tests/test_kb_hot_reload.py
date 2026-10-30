"""知识库热重载（XB-19）与管理端点鉴权（XB-20）的单测。

覆盖三件事：
  1. `retriever.clear_bm25_cache()` 真的清掉 BM25 语料缓存
  2. `app.reload_knowledge_store()` 同时清「向量库句柄」与「BM25 缓存」两处状态，
     且加载失败时按既有策略降级（返回 reloaded=False）而不是抛异常
  3. `POST /admin/reload-kb` 的鉴权闸门（XB-20）：默认只放行本机来源，
     配置 `ADMIN_RELOAD_TOKEN` 后改为校验 `X-Admin-Token`

不真的加载向量库（那要跑 embedding），全部用 mock 隔离。
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.requests import Request

import app as app_module
from agent.rag import retriever


class TestClearBm25Cache(unittest.TestCase):
    def setUp(self):
        retriever._BM25_CACHE.clear()
        retriever._BM25_CACHE["probe"] = (Mock(), [Mock()])

    def tearDown(self):
        retriever._BM25_CACHE.clear()

    def test_clears_and_reports_count(self):
        self.assertEqual(retriever.clear_bm25_cache(), 1)
        self.assertEqual(retriever._BM25_CACHE, {})

    def test_clearing_empty_cache_is_safe(self):
        retriever._BM25_CACHE.clear()
        self.assertEqual(retriever.clear_bm25_cache(), 0)


class TestReloadKnowledgeStore(unittest.TestCase):
    def test_clears_both_vector_and_bm25_state(self):
        """只清 lru_cache 不清 BM25 会留下旧语料配新库，两者必须一起清。"""
        with patch.object(app_module, "get_knowledge_store") as fake_store, patch.object(
            app_module, "clear_bm25_cache", return_value=2
        ) as fake_clear:
            fake_store.cache_clear = Mock()
            fake_store.return_value = None  # 模拟库不可用
            result = app_module.reload_knowledge_store()

        fake_store.cache_clear.assert_called_once()
        fake_clear.assert_called_once()
        self.assertFalse(result["reloaded"])
        self.assertIsNone(result["chunks"])
        self.assertEqual(result["bm25_cleared"], 2)

    def test_reports_chunk_counts_before_and_after(self):
        old_store = Mock()
        old_store._collection.count.return_value = 235
        new_store = Mock()
        new_store._collection.count.return_value = 241

        with patch.object(app_module, "get_knowledge_store") as fake_store, patch.object(
            app_module, "clear_bm25_cache", return_value=1
        ):
            fake_store.cache_clear = Mock()
            # 第一次调用取重载前的切片数，第二次是重载后重新加载
            fake_store.side_effect = [old_store, new_store]
            result = app_module.reload_knowledge_store()

        self.assertTrue(result["reloaded"])
        self.assertEqual(result["chunks_before"], 235)
        self.assertEqual(result["chunks"], 241)
        # 增量后若两者相等，说明磁盘变更没进库，这个对比就是排查依据
        self.assertEqual(fake_store.call_count, 2)

    def test_failure_degrades_instead_of_raising(self):
        """知识库是增强项：加载失败必须降级，不能把 /chat 拖成 500。"""
        with patch.object(app_module, "get_knowledge_store") as fake_store, patch.object(
            app_module, "clear_bm25_cache", return_value=0
        ):
            fake_store.cache_clear = Mock()
            fake_store.side_effect = RuntimeError("collection 不存在")
            # 不抛异常即是本用例的断言
            result = app_module.reload_knowledge_store()

        self.assertFalse(result["reloaded"])
        self.assertIsNone(result["chunks"])
        self.assertIsNone(result["chunks_before"])


class TestAdminEndpointRegistered(unittest.TestCase):
    def test_reload_route_exists(self):
        """端点容易被误删，且删了不会有任何测试失败——静态 registries 检查兜住。"""
        paths = {getattr(r, "path", "") for r in app_module.app.routes}
        self.assertIn("/admin/reload-kb", paths)


class TestIsLoopbackClient(unittest.TestCase):
    """`::ffff:127.0.0.1` 这类 IPv4-mapped 地址最容易判错：
    `ipaddress.ip_address("::ffff:127.0.0.1").is_loopback` 是 False，
    不映射就会把本机请求挡在门外。"""

    def test_ipv4_loopback(self):
        self.assertTrue(app_module._is_loopback_client("127.0.0.1"))

    def test_ipv6_loopback(self):
        self.assertTrue(app_module._is_loopback_client("::1"))

    def test_ipv4_mapped_ipv6(self):
        self.assertTrue(app_module._is_loopback_client("::ffff:127.0.0.1"))

    def test_remote_ipv4(self):
        self.assertFalse(app_module._is_loopback_client("203.0.113.5"))

    def test_unparsable_host(self):
        """TestClient 默认给的 'testclient' 不是 IP，必须判为不可信而不是放行。"""
        self.assertFalse(app_module._is_loopback_client("testclient"))

    def test_empty_host(self):
        self.assertFalse(app_module._is_loopback_client(""))


def make_request(host: str = "127.0.0.1", headers: dict | None = None) -> Request:
    """直接造一个 Request，不走 HTTP。

    鉴权判据只用到 `client.host` 和 headers，没必要为每个用例起一次 TestClient
    （那会触发 lifespan，连一次数据库、刷一屏日志）。端到端是否真接上闸门，
    交给下面 `TestAdminReloadAuth` 的两个用例守住。
    """
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "path": "/admin/reload-kb",
            "raw_path": b"/admin/reload-kb",
            "query_string": b"",
            "scheme": "http",
            "root_path": "",
            "client": (host, 12345),
            "server": ("testserver", 80),
            "headers": [
                (k.lower().encode("latin-1"), v.encode("latin-1"))
                for k, v in (headers or {}).items()
            ],
        }
    )


class TestAdminGate(unittest.TestCase):
    """XB-20 闸门纯函数：默认只放行本机来源，配置令牌后改为校验令牌。"""

    def test_loopback_passes_by_default(self):
        """没配令牌时本机照常可用——现有 curl 用法不受影响。"""
        self.assertEqual(app_module._admin_gate(make_request("127.0.0.1")), ("", 0))

    def test_remote_rejected_when_no_token(self):
        reason, status = app_module._admin_gate(make_request("203.0.113.5"))
        self.assertEqual(status, 403)
        self.assertIn("本机", reason)

    def test_remote_rejected_even_from_private_lan(self):
        """内网地址也不放行：暴露到局域网同样算「非本机」。"""
        self.assertEqual(app_module._admin_gate(make_request("192.168.1.7"))[1], 403)

    def test_correct_token_passes_from_remote(self):
        """配了令牌就不再看来源，方便远程运维。"""
        with patch.dict(os.environ, {app_module.ADMIN_TOKEN_ENV: "s3cr3t"}):
            self.assertEqual(
                app_module._admin_gate(
                    make_request("203.0.113.5", {"X-Admin-Token": "s3cr3t"})
                ),
                ("", 0),
            )

    def test_loopback_also_needs_token_once_configured(self):
        """配了令牌后本机也要带——行为一致才好排查，不能本机悄悄放行。"""
        with patch.dict(os.environ, {app_module.ADMIN_TOKEN_ENV: "s3cr3t"}):
            self.assertEqual(app_module._admin_gate(make_request("127.0.0.1"))[1], 401)
            self.assertEqual(
                app_module._admin_gate(
                    make_request("127.0.0.1", {"X-Admin-Token": "s3cr3t"})
                ),
                ("", 0),
            )

    def test_wrong_token_rejected(self):
        with patch.dict(os.environ, {app_module.ADMIN_TOKEN_ENV: "s3cr3t"}):
            reason, status = app_module._admin_gate(
                make_request("127.0.0.1", {"X-Admin-Token": "guess"})
            )
        self.assertEqual(status, 401)
        self.assertNotIn("s3cr3t", reason)

    def test_header_name_is_case_insensitive(self):
        """HTTP 头名大小写不敏感，按小写取才能匹配 `x-admin-token`。"""
        with patch.dict(os.environ, {app_module.ADMIN_TOKEN_ENV: "s3cr3t"}):
            self.assertEqual(
                app_module._admin_gate(
                    make_request("127.0.0.1", {"x-admin-token": "s3cr3t"})
                ),
                ("", 0),
            )

    def test_blank_token_env_falls_back_to_loopback_rule(self):
        """配了空串等于没配——不能因为 `ADMIN_RELOAD_TOKEN=` 就变成放行一切。"""
        with patch.dict(os.environ, {app_module.ADMIN_TOKEN_ENV: "   "}):
            self.assertEqual(
                app_module._admin_gate(make_request("203.0.113.5"))[1], 403
            )


class TestAdminReloadAuth(unittest.TestCase):
    """端到端：端点确实接上了闸门（只验证路由与状态码，不重复闸门矩阵）。"""

    def setUp(self):
        # 限流与鉴权是两件事，测试里关掉限流免得互相干扰
        self._limiter_enabled = getattr(app_module.limiter, "enabled", True)
        app_module.limiter.enabled = False

    def tearDown(self):
        app_module.limiter.enabled = self._limiter_enabled

    def _post(self, client_host="127.0.0.1", headers=None):
        from fastapi.testclient import TestClient

        payload = {
            "reloaded": True,
            "chunks": 248,
            "chunks_before": 248,
            "bm25_cleared": 1,
        }
        with patch.object(app_module, "reload_knowledge_store", return_value=payload):
            with TestClient(app_module.app, client=(client_host, 12345)) as client:
                return client.post("/admin/reload-kb", headers=headers or {})

    def test_loopback_request_gets_200(self):
        self.assertEqual(self._post("127.0.0.1").status_code, 200)

    def test_remote_request_gets_403(self):
        resp = self._post("203.0.113.5")
        self.assertEqual(resp.status_code, 403)

    def test_rejection_is_logged_without_token(self):
        """被拒绝要留痕（含来源 IP），但日志里不能出现令牌本身。"""
        with patch.dict(os.environ, {app_module.ADMIN_TOKEN_ENV: "s3cr3t-abc"}):
            with self.assertLogs(app_module.logger, level="WARNING") as captured:
                resp = self._post("127.0.0.1", headers={"X-Admin-Token": "wrong"})
        joined = "\n".join(captured.output)
        self.assertEqual(resp.status_code, 401)
        self.assertIn("管理端点拒绝", joined)
        self.assertNotIn("s3cr3t", joined)
        self.assertNotIn("s3cr3t", str(resp.json()))


if __name__ == "__main__":
    unittest.main()
