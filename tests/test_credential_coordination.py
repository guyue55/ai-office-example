"""合成凭证隔离回归；文件存储仅为多进程测试，不是生产凭证后端。"""

import json
import multiprocessing
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import httpx
import pytest
from office_client.auth import ClientError, OAuth
from office_client.credential_lock import credential_guard


class Store:
    def __init__(self):
        self.value = {
            "access_token": "synthetic-old",
            "refresh_token": "synthetic-refresh",
            "expires_at": 1,
        }

    def load(self):
        return dict(self.value) if self.value else None

    def save(self, value):
        self.value = dict(value)

    def clear(self):
        self.value = None


def client(store, handler):
    oauth = OAuth("https://service.invalid", store)
    oauth._config = {"issuer": "https://identity.invalid", "client_id": "synthetic"}
    oauth.http.close()
    oauth.http = httpx.Client(transport=httpx.MockTransport(handler))
    return oauth


def success():
    return httpx.Response(
        200,
        json={
            "access_token": "synthetic-new",
            "refresh_token": "synthetic-rotated",
            "expires_in": 300,
            "refresh_expires_in": 600,
        },
    )


def test_shared_store_objects_refresh_once_and_new_login_wins():
    store, entered, resume, calls = Store(), Event(), Event(), []

    def handler(request):
        calls.append(1)
        entered.set()
        assert resume.wait(3)
        return success()

    one, two = client(store, handler), client(store, handler)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(one.access_token)
            assert entered.wait(3)
            second = pool.submit(two.access_token)
            resume.set()
            assert first.result(3) == second.result(3) == "synthetic-new"
        assert len(calls) == 1
        # 第二轮：旧刷新与新登录保存协调，最终保留用户新完成的登录。
        store.value["expires_at"] = 1
        entered.clear()
        resume.clear()
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(one.access_token)
            assert entered.wait(3)
            login = pool.submit(
                two._save,
                {
                    "access_token": "synthetic-login",
                    "refresh_token": "synthetic-login-refresh",
                    "expires_in": 300,
                },
            )
            resume.set()
            pending.result(3)
            login.result(3)
        assert store.value["access_token"] == "synthetic-login"
    finally:
        one.close()
        two.close()


def test_uncoordinated_new_generation_is_not_overwritten():
    store = Store()

    def handler(request):
        store.save({"access_token": "synthetic-new-login", "expires_at": time.time() + 300})
        return success()

    oauth = client(store, handler)
    with pytest.raises(ClientError, match="登录已变化"):
        oauth.access_token()
    assert store.value["access_token"] == "synthetic-new-login"
    oauth.close()


@pytest.mark.parametrize("outcome", ["lost", "malformed", "save_failed", "ambiguous_503"])
def test_uncertain_rotation_is_never_reused(outcome):
    store, calls = Store(), []
    original = store.save

    def save(value):
        if outcome == "save_failed" and value.get("access_token") == "synthetic-new":
            raise ClientError("合成存储失败")
        original(value)

    store.save = save

    def handler(request):
        calls.append(1)
        if outcome == "lost":
            raise httpx.ReadTimeout("合成响应丢失")
        if outcome == "ambiguous_503":
            return httpx.Response(503)
        if outcome == "malformed":
            return httpx.Response(200, json={"expires_in": 300})
        return success()

    oauth = client(store, handler)
    with pytest.raises(ClientError):
        oauth.access_token()
    with pytest.raises(ClientError):
        oauth.access_token()
    assert len(calls) == 1 and oauth.status()["refresh_state"] == "uncertain"
    oauth.close()


def test_storage_failure_before_request_never_sends():
    store = Store()
    store.save = Mock(side_effect=ClientError("合成写入失败"))
    handler = Mock(return_value=success())
    oauth = client(store, handler)
    with pytest.raises(ClientError):
        oauth.access_token()
    handler.assert_not_called()
    oauth.close()


def test_actual_refresh_expiry_and_status_are_local_only():
    oauth = client(Store(), lambda request: success())
    start = time.time()
    oauth.access_token()
    status = oauth.status()
    assert start + 599 <= status["refresh_expires_at"] <= time.time() + 601
    assert status["refresh_state"] == "available_locally" and not status["online_verified"]
    assert "synthetic" not in json.dumps(status)
    oauth.close()


class SyntheticFileStore:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.coordination_key = "synthetic:" + str(folder)

    def load(self):
        return json.loads((self.folder / "value.json").read_text())

    def save(self, value):
        (self.folder / "value.json").write_text(json.dumps(value))

    def clear(self):
        (self.folder / "value.json").write_text("null")


def process_reader(folder, ready, queue):
    import office_client.credential_lock as locks

    locks.lock_directory = lambda: Path(folder) / "locks"

    def handler(request):
        with (Path(folder) / "requests.log").open("a") as out:
            out.write("request\n")
        time.sleep(0.1)
        return success()

    oauth = client(SyntheticFileStore(folder), handler)
    ready.wait(5)
    try:
        queue.put(oauth.access_token() == "synthetic-new")
    finally:
        oauth.close()


def test_real_processes_share_only_one_rotation(tmp_path):
    SyntheticFileStore(tmp_path).save(Store().load())
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    queue = context.Queue()
    processes = [
        context.Process(target=process_reader, args=(str(tmp_path), ready, queue)) for _ in range(3)
    ]
    try:
        for process in processes:
            process.start()
        ready.set()
        assert all(queue.get(timeout=15) for _ in processes)
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert (tmp_path / "requests.log").read_text().splitlines() == ["request"]
        for path in (tmp_path / "locks").iterdir():
            assert path.read_bytes() == b"0"
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        queue.close()
        queue.join_thread()


def test_nested_guard_and_exception_release():
    store = Store()
    with pytest.raises(ValueError), credential_guard(store), credential_guard(store):
        raise ValueError()
    with credential_guard(store, timeout=0.1):
        pass
