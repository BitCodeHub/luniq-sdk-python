# luniq

Server-side SDK for [Luniq.AI](https://uselunaai.com). Track events, identify
users, and evaluate feature flags from any Python backend.

## Install

```bash
pip install luniq
```

## Usage

```python
import os
from luniq import Luniq

luniq = Luniq(
    api_key=os.environ["LUNIQ_API_KEY"],
    endpoint="https://your-luniq-host.com",  # optional; defaults to https://uselunaai.com
    environment="PRD",
)

# Track from a backend route (Flask example)
@app.post("/api/orders")
def create_order():
    order = db.orders.create(request.json)
    luniq.track(
        "order_placed",
        visitor_id=g.user.visitor_id,
        account_id=g.user.id,
        properties={"amount": order.total, "sku_count": len(order.items)},
    )
    return order

# Server-side feature flag evaluation
@app.get("/api/checkout")
def checkout():
    flags = luniq.flags(visitor_id=g.user.visitor_id, traits={"plan": g.user.plan})
    if flags.get("new_checkout_v2"):
        return redirect("/checkout/v2")
    return redirect("/checkout/v1")

# On graceful shutdown
import atexit
atexit.register(luniq.shutdown)
```

## API

### `Luniq(api_key, endpoint=..., **opts)`

- `api_key` (required) — workspace API key with `write` or `admin` scope
- `endpoint` — base URL of your Luniq deployment (default `https://uselunaai.com`)
- `environment` — `PRD` / `STG` / `DEV` (default `PRD`)
- `flush_interval_ms` — default `10000`
- `max_queue_size` — default `10000`
- `redact_pii` — default `True`; auto-redacts emails, phones, cards, SSNs
- `session` — optional `requests.Session` for connection reuse / testing

### `track(name, visitor_id, account_id=None, properties=None, timestamp=None)`

Buffers an event for batched async upload. Non-blocking.

### `identify(visitor_id, account_id=None, traits=None)`

Emits a `$identify` event with the supplied trait set.

### `flags(visitor_id, account_id=None, traits=None) -> dict`

Fetches the current flag evaluation from Luniq for this visitor and caches
the result for sync access via `flag()`. Returns `{}` on network or server error.

### `flag(visitor_id, key) -> Any`

Sync — returns the cached flag value. Call `flags()` once first to populate
the cache. Returns `False` if the visitor or key isn't cached.

### `flush()`

Force-drain the buffered queue. Auto-called by a daemon background thread
every `flush_interval_ms`. Safe to call manually before process exit.

### `shutdown()`

Stops the background flush thread and flushes one last time. Call this
before the process exits to avoid losing buffered events.

## License

Apache-2.0.
