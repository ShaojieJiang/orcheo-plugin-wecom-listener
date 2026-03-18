"""Integration tests for the WeCom listener plugin."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

from orcheo.listeners.compiler import compile_listener_subscriptions
from orcheo.listeners.models import ListenerSubscription
from orcheo.listeners.registry import listener_registry
from orcheo.plugins import load_enabled_plugins, reset_plugin_loader_for_tests
from orcheo.plugins.manager import PluginManager


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WECOM_PLUGIN_SRC = PACKAGE_ROOT / "src"


def _set_plugin_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    cache_dir = tmp_path / "cache"
    config_dir = tmp_path / "config"
    plugin_dir.mkdir()
    cache_dir.mkdir()
    config_dir.mkdir()
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(plugin_dir))
    monkeypatch.setenv("ORCHEO_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("ORCHEO_CONFIG_DIR", str(config_dir))


def _load_plugins() -> None:
    reset_plugin_loader_for_tests()
    load_enabled_plugins(force=True)


class RecordingListenerRepository:
    """Repository stub that records dispatched listener payloads."""

    def __init__(self) -> None:
        self.events: list[tuple[object, object]] = []

    async def dispatch_listener_event(
        self, subscription_id: object, payload: object
    ) -> object:
        self.events.append((subscription_id, payload))
        return {"subscription_id": str(subscription_id)}


def _load_wecom_plugin_module() -> ModuleType:
    module_name = "test_orcheo_plugin_wecom_listener"
    module_path = WECOM_PLUGIN_SRC / "orcheo_plugin_wecom_listener" / "__init__.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


@pytest.mark.asyncio()
async def test_wecom_plugin_dispatches_normalized_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The WeCom validation adapter should dispatch the shared payload contract."""
    _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    manager.install(str(PACKAGE_ROOT))

    _load_plugins()

    subscriptions = compile_listener_subscriptions(
        uuid4(),
        uuid4(),
        {
            "index": {
                "listeners": [
                    {
                        "node_name": "wecom_listener",
                        "platform": "wecom",
                        "bot_id": "aib-test-bot",
                        "bot_secret": "test-secret",
                        "test_events": [
                            {
                                "text": "hello from wecom",
                                "to_user": "user-123",
                            }
                        ],
                    }
                ]
            }
        },
    )
    subscription = subscriptions[0]
    repository = RecordingListenerRepository()
    adapter = listener_registry.build_adapter(
        "wecom",
        repository=repository,
        subscription=subscription,
        runtime_id="wecom-runtime",
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(adapter.run(stop_event))
    await asyncio.sleep(0)
    stop_event.set()
    await task

    assert len(repository.events) == 1
    _subscription_id, payload = repository.events[0]
    assert payload.platform == "wecom"
    assert payload.message.text == "hello from wecom"
    assert payload.reply_target["to_user"] == "user-123"
    assert "corp_id" not in payload.reply_target
    assert adapter.health().status == "stopped"

    uninstall_impact = manager.uninstall("orcheo-plugin-wecom-listener")
    assert uninstall_impact.restart_required is True
    assert manager.list_plugins() == []


@pytest.mark.asyncio()
async def test_wecom_plugin_uses_websocket_mode_without_fixture_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The adapter should switch to WebSocket long-connection mode by default."""
    _set_plugin_env(monkeypatch, tmp_path)
    manager = PluginManager()
    manager.install(str(PACKAGE_ROOT))

    _load_plugins()

    subscriptions = compile_listener_subscriptions(
        uuid4(),
        uuid4(),
        {
            "index": {
                "listeners": [
                    {
                        "node_name": "wecom_listener",
                        "platform": "wecom",
                        "bot_id": "aib-test-bot",
                        "bot_secret": "test-secret",
                    }
                ]
            }
        },
    )
    repository = RecordingListenerRepository()
    adapter = listener_registry.build_adapter(
        "wecom",
        repository=repository,
        subscription=subscriptions[0],
        runtime_id="wecom-runtime",
    )

    entered_ws_mode = asyncio.Event()

    async def fake_ws_mode(self, stop_event: asyncio.Event) -> None:
        self._status = "healthy"
        self._detail = "using websocket mode"
        entered_ws_mode.set()
        await stop_event.wait()
        self._status = "stopped"

    monkeypatch.setattr(
        type(adapter),
        "_run_websocket_connection",
        fake_ws_mode,
    )

    stop_event = asyncio.Event()
    task = asyncio.create_task(adapter.run(stop_event))
    await asyncio.wait_for(entered_ws_mode.wait(), timeout=1)
    stop_event.set()
    await task

    assert repository.events == []
    assert adapter.health().status == "stopped"


@pytest.mark.asyncio()
async def test_wecom_plugin_websocket_mode_blocks_when_config_missing() -> None:
    """The adapter should report blocked when bot_id or bot_secret is missing."""
    wecom_plugin = _load_wecom_plugin_module()
    subscription = ListenerSubscription(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        node_name="wecom_listener",
        platform="wecom",
        bot_identity_key="wecom:primary",
        config={
            "bot_id": "",
            "bot_secret": "test-secret",
        },
    )
    repository = RecordingListenerRepository()
    adapter = wecom_plugin.WeComListenerAdapter(
        repository=repository,
        subscription=subscription,
        runtime_id="wecom-runtime",
    )

    stop_event = asyncio.Event()
    task = asyncio.create_task(adapter.run(stop_event))
    await asyncio.sleep(0)
    health = adapter.health()
    assert health.status == "error"
    assert health.detail is not None
    assert "bot_id" in health.detail
    assert "blocked:" in health.detail
    assert repository.events == []

    stop_event.set()
    await task
    assert adapter.health().status == "stopped"


def test_wecom_ws_event_normalization() -> None:
    """WebSocket frames should normalize into the shared listener payload."""
    wecom_plugin = _load_wecom_plugin_module()

    subscription = ListenerSubscription(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        node_name="wecom_listener",
        platform="wecom",
        bot_identity_key="wecom:primary",
        config={
            "bot_id": "aib-test-bot",
            "bot_secret": "test-secret",
        },
    )

    frame = {
        "msgtype": "text",
        "body": {
            "from": {"user_id": "user-789"},
            "chat_id": "chat-abc",
            "msg_id": "msg-001",
            "text": {"content": "hello from websocket"},
        },
    }
    payload = wecom_plugin.normalize_wecom_ws_event(subscription, frame)
    assert payload is not None
    assert payload.platform == "wecom"
    assert payload.event_type == "text"
    assert payload.message.text == "hello from websocket"
    assert payload.message.user_id == "user-789"
    assert payload.message.message_id == "msg-001"
    assert payload.message.chat_id == "chat-abc"
    assert payload.message.chat_type == "group"
    assert "corp_id" not in payload.reply_target
    assert payload.reply_target["chat_id"] == "chat-abc"
    assert payload.reply_target["to_user"] is None
    assert payload.metadata["transport"] == "websocket"


def test_wecom_ws_event_normalization_private_message() -> None:
    """Private messages should set to_user instead of chat_id in reply_target."""
    wecom_plugin = _load_wecom_plugin_module()

    subscription = ListenerSubscription(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        node_name="wecom_listener",
        platform="wecom",
        bot_identity_key="wecom:primary",
        config={},
    )

    frame = {
        "msgtype": "text",
        "body": {
            "from": {"user_id": "user-789"},
            "msg_id": "msg-002",
            "text": {"content": "private hello"},
        },
    }
    payload = wecom_plugin.normalize_wecom_ws_event(subscription, frame)
    assert payload is not None
    assert payload.message.chat_type == "private"
    assert payload.reply_target["to_user"] == "user-789"
    assert payload.reply_target["chat_id"] is None


def test_wecom_ws_event_normalization_image_and_file() -> None:
    """Image and file frames should produce text previews."""
    wecom_plugin = _load_wecom_plugin_module()

    subscription = ListenerSubscription(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        node_name="wecom_listener",
        platform="wecom",
        bot_identity_key="wecom:primary",
        config={},
    )

    image_frame = {
        "msgtype": "image",
        "body": {
            "from": {"user_id": "user-1"},
            "image": {"url": "https://example.com/img.jpg"},
        },
    }
    payload = wecom_plugin.normalize_wecom_ws_event(subscription, image_frame)
    assert payload is not None
    assert payload.message.text == "[Image]"

    file_frame = {
        "msgtype": "file",
        "body": {
            "from": {"user_id": "user-1"},
            "file": {"file_name": "report.pdf"},
        },
    }
    payload = wecom_plugin.normalize_wecom_ws_event(subscription, file_frame)
    assert payload is not None
    assert payload.message.text == "[File] report.pdf"


def _prepare_wecom_websocket_mock(
    *,
    monkeypatch: pytest.MonkeyPatch,
    wecom_plugin: ModuleType,
    connected_loops: list[asyncio.AbstractEventLoop],
    first_loop_ref: list[asyncio.AbstractEventLoop | None],
) -> None:
    class FakeWSClient:
        def __init__(
            self, *, bot_id: str, secret: str, max_reconnect_attempts: int = 0
        ) -> None:
            self.bot_id = bot_id
            self.secret = secret
            self._handlers: dict[str, object] = {}

        def on(self, event_type: str, handler: object) -> None:
            self._handlers[event_type] = handler

        async def connect(self) -> None:
            running_loop = asyncio.get_running_loop()
            connected_loops.append(running_loop)
            if first_loop_ref[0] is None:
                first_loop_ref[0] = running_loop
            elif running_loop is not first_loop_ref[0]:
                raise RuntimeError("WeCom client was bound to a different loop.")
            handler = self._handlers.get("message.text")
            if handler is not None:
                running_loop.call_soon(
                    handler,
                    {
                        "msgtype": "text",
                        "body": {
                            "from": {"user_id": f"user-{self.bot_id}"},
                            "msg_id": f"msg-{self.bot_id}",
                            "text": {"content": f"hello from {self.bot_id}"},
                        },
                    },
                )

        async def disconnect(self) -> None:
            return None

    sdk_module = ModuleType("wecom_aibot_sdk")
    sdk_module.WSClient = FakeWSClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "wecom_aibot_sdk", sdk_module)
    monkeypatch.setattr(
        wecom_plugin,
        "get_wecom_long_connection_block_reason",
        lambda _config: None,
    )


@pytest.mark.asyncio()
async def test_wecom_plugin_websocket_mode_shares_one_sdk_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple WebSocket-mode adapters should reuse one SDK loop."""
    wecom_plugin = _load_wecom_plugin_module()
    connected_loops: list[asyncio.AbstractEventLoop] = []
    first_loop_ref: list[asyncio.AbstractEventLoop | None] = [None]

    _prepare_wecom_websocket_mock(
        monkeypatch=monkeypatch,
        wecom_plugin=wecom_plugin,
        connected_loops=connected_loops,
        first_loop_ref=first_loop_ref,
    )

    subscriptions = [
        ListenerSubscription(
            workflow_id=uuid4(),
            workflow_version_id=uuid4(),
            node_name=f"wecom_listener_{index}",
            platform="wecom",
            bot_identity_key=f"wecom:{index}",
            config={
                "bot_id": f"bot-{index}",
                "bot_secret": f"secret-{index}",
            },
        )
        for index in range(2)
    ]
    repositories = [RecordingListenerRepository(), RecordingListenerRepository()]
    adapters = [
        wecom_plugin.WeComListenerAdapter(
            repository=repository,
            subscription=subscription,
            runtime_id=f"runtime-{index}",
        )
        for index, (repository, subscription) in enumerate(
            zip(repositories, subscriptions, strict=True)
        )
    ]
    stop_events = [asyncio.Event(), asyncio.Event()]
    tasks: list[asyncio.Task[None]] = []

    try:
        tasks.append(asyncio.create_task(adapters[0].run(stop_events[0])))
        for _ in range(50):
            if repositories[0].events:
                break
            await asyncio.sleep(0.05)
        assert repositories[0].events

        tasks.append(asyncio.create_task(adapters[1].run(stop_events[1])))
        for _ in range(50):
            if repositories[1].events:
                break
            await asyncio.sleep(0.05)
        assert repositories[1].events
    finally:
        for stop_event in stop_events:
            stop_event.set()
        await asyncio.gather(*tasks)
        wecom_plugin._SHARED_WECOM_SDK_LOOP.shutdown()

    assert len(connected_loops) == 2
    assert len(set(connected_loops)) == 1
    assert repositories[0].events[0][1].message.text == "hello from bot-0"
    assert repositories[1].events[0][1].message.text == "hello from bot-1"
    assert adapters[0].health().status == "stopped"
    assert adapters[1].health().status == "stopped"


@pytest.mark.asyncio()
async def test_wecom_ws_reply_node_sends_via_client() -> None:
    """WeComWsReplyNode should call client.reply() with the correct body."""
    wecom_plugin = _load_wecom_plugin_module()

    reply_calls: list[tuple[object, object]] = []

    class FakeClient:
        async def reply(self, frame: object, body: object) -> None:
            reply_calls.append((frame, body))

    loop = asyncio.get_running_loop()
    sub_id = str(uuid4())
    wecom_plugin.register_wecom_client(sub_id, FakeClient(), loop)

    try:
        node = wecom_plugin.WeComWsReplyNode(
            name="test_reply",
            message="Hello from agent",
            raw_event={"headers": {"req_id": "req-001"}},
            subscription_id=sub_id,
        )
        result = await node.run({}, {})
        assert result == {"sent": True}
        assert len(reply_calls) == 1
        frame_arg, body_arg = reply_calls[0]
        assert frame_arg == {"headers": {"req_id": "req-001"}}
        assert body_arg["msgtype"] == "stream"
        assert body_arg["stream"]["content"] == "Hello from agent"
        assert body_arg["stream"]["finish"] is True
        assert str(body_arg["stream"]["id"]).startswith("orcheo-")
    finally:
        wecom_plugin.deregister_wecom_client(sub_id)


@pytest.mark.asyncio()
async def test_wecom_ws_reply_node_relays_via_backend_when_no_local_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WeComWsReplyNode should HTTP-relay through the backend when no local client."""
    wecom_plugin = _load_wecom_plugin_module()

    monkeypatch.setenv("ORCHEO_BACKEND_INTERNAL_URL", "http://test-backend:9999")

    import httpx

    captured_requests: list[httpx.Request] = []

    async def mock_send(
        self: object, request: httpx.Request, **kwargs: object
    ) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"sent": True})

    monkeypatch.setattr(httpx.AsyncClient, "send", mock_send)

    node = wecom_plugin.WeComWsReplyNode(
        name="test_reply",
        message="Hello via relay",
        raw_event={"headers": {"req_id": "req-002"}},
        subscription_id="nonexistent-sub-id",
    )
    result = await node.run({}, {})
    assert result == {"sent": True}
    assert len(captured_requests) == 1
    assert "/api/internal/listeners/wecom/reply" in str(captured_requests[0].url)
    assert b"Hello via relay" in captured_requests[0].content
