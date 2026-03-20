# orcheo-plugin-wecom-listener

An Orcheo plugin that connects WeCom (Enterprise WeChat) to Orcheo workflows
via a WebSocket long-connection. Incoming messages and events are normalised
into the shared `ListenerDispatchPayload` contract and dispatched to any
workflow that subscribes to the `wecom` listener platform.

## What this plugin provides

| Component | Name | Description |
|---|---|---|
| Node | `WeComListenerPluginNode` | Declares a WeCom listener subscription in a workflow |
| Node | `WeComWsReplyNode` | Sends a reply to a WeCom message via the active long-connection |
| Listener | `wecom` | Runtime adapter that manages the WebSocket long-connection |

## Requirements

- Python 3.12+
- Orcheo backend with the listener runtime running
- A WeCom AI Bot with a `bot_id` and `bot_secret`
  ([WeCom AI Bot documentation](https://work.weixin.qq.com/nl/index/openclaw))

## Installation

```bash
orcheo plugin install git+https://github.com/ShaojieJiang/orcheo-plugin-wecom-listener.git
```

After installation, restart the backend and worker processes:

```bash
# Docker Compose
docker compose restart backend worker
```

## Configuration

Add a `WeComListenerPluginNode` to your workflow and configure the credentials:

| Field | Description |
|---|---|
| `bot_id` | WeCom AI Bot ID |
| `bot_secret` | WeCom AI Bot Secret |

Credentials can be interpolated from the Orcheo credential vault using the
`[[credential_key]]` syntax, e.g. `bot_id = "[[wecom_bot_id]]"`.

See the [Lark App Setup guide](https://orcheo.readthedocs.io/integrations/lark_app_setup/)
for a comparable credential setup walkthrough.

## Canvas template

The Canvas template `template-wecom-lark-shared-listener` wires a WeCom
listener and a Lark listener into one shared downstream workflow, showing
how to normalize events from multiple platforms before processing.

Install both plugins before importing the template:

```bash
orcheo plugin install git+https://github.com/ShaojieJiang/orcheo-plugin-wecom-listener.git
orcheo plugin install git+https://github.com/ShaojieJiang/orcheo-plugin-lark-listener.git
```

## Replying to messages

Use `WeComWsReplyNode` in your workflow to send a reply back through the same
long-connection that received the event. In a multi-process deployment (backend
+ Celery worker), the node relays the reply through the backend's internal
HTTP endpoint automatically.

## Development

```bash
git clone https://github.com/ShaojieJiang/orcheo-plugin-wecom-listener
cd orcheo-plugin-wecom-listener
uv venv && uv pip install -e ".[dev]"
uv run pytest
```

## Further reading

- [Plugin Reference](https://orcheo.readthedocs.io/custom_nodes_and_tools/) — listener plugin contract
- [CLI Reference](https://orcheo.readthedocs.io/cli_reference/) — `orcheo plugin` commands
- [orcheo-plugin-lark-listener](https://github.com/ShaojieJiang/orcheo-plugin-lark-listener) — companion Lark plugin
- [orcheo-plugin-template](https://github.com/ShaojieJiang/orcheo-plugin-template) — starter template for building your own plugin
