# 08 — Slack Client + Approval Gate

## What Was Built and Why

`errander/integrations/slack.py` wraps the Slack Web API for outbound-only communication. `errander/safety/approval.py` implements the human-in-the-loop approval gate: post a dry-run report to Slack, poll for emoji reactions, and block live execution until a human approves (or the timeout auto-rejects).

Key constraint: **the agent VM has no public IP and no inbound traffic**. There are no webhooks, no Slack event subscriptions, no nginx. The agent polls Slack every 30 seconds — Slack never calls back.

---

## Key Concepts

### 1. aiohttp Async Context Manager Pattern

`aiohttp` sessions use the async context manager protocol for HTTP requests, not plain awaitables:

```python
# WRONG — session.post() is not awaitable
resp = await session.post(url, json=payload)

# CORRECT — session.post() returns an async context manager
async with session.post(url, json=payload) as resp:
    data = await resp.json()
```

This is deliberate: `aiohttp` keeps the connection alive until the body is consumed, then releases it back to the connection pool. The `async with` block guarantees cleanup even on exceptions.

The implementation stores the context manager object, then enters it:

```python
ctx = session.get(url, params=payload) if http_method == "GET" else session.post(url, json=payload)
async with ctx as resp:
    ...
```

This makes GET/POST switchable with one assignment before the `async with`.

### 2. Rate Limiting: Retry-After

Slack returns HTTP 429 when rate limited and includes a `Retry-After` header (seconds to wait). The implementation does one automatic retry:

```python
for attempt in range(2):
    async with ctx as resp:
        if resp.status == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            if attempt == 0:
                await asyncio.sleep(retry_after)
                continue  # retry with attempt=1
            raise SlackError(f"rate limit exceeded after retry")
        ...
```

Critical: use `continue` to retry, not `break`. `break` exits the loop and hits whatever code follows — a silent bug that looks like retry logic but isn't.

### 3. Slack `ts` as a Message Handle

Every posted Slack message has a timestamp (`ts`) that uniquely identifies it within a channel. `ts` is returned by `chat.postMessage` and used as the handle for all subsequent operations on that message:

```python
ts = await client.post_message("Dry-run complete — review plan")
reactions = await client.get_reactions(ts)  # poll by ts
```

`ts` looks like `"1700000000.000001"` — a Unix timestamp with microsecond precision as a string.

### 4. Reaction Polling vs Webhooks

Two approaches to Slack approval:

| | Polling | Webhooks |
|---|---|---|
| Network | Outbound HTTPS only | Requires public IP + inbound |
| Setup | Zero infra | nginx + TLS + Slack config |
| Latency | ~30s (poll interval) | Near-instant |
| Reliability | Simple | More moving parts |

For a VM with no public IP, polling is the only viable option. 30s latency is fine for maintenance approval — nobody is waiting at their desk for sub-second approval response.

### 5. Reaction Priority: Reject Before Approve

If both ✅ and ❌ are present on the same message (e.g., conflicting team members), rejection wins:

```python
# Check reject first — explicit rejection takes priority
for reaction in reactions:
    if reaction["name"] == REJECT_REACTION and reaction.get("users"):
        return False, reaction["users"][0]

# Only check approve if no reject found
for reaction in reactions:
    if reaction["name"] == APPROVE_REACTION and reaction.get("users"):
        return True, reaction["users"][0]
```

Two separate loops, not one combined loop. A single loop with `if/elif` would also work, but two loops make the priority explicit.

### 6. Timeout = Auto-Reject

The polling loop checks a deadline timestamp:

```python
deadline = datetime.now(tz=timezone.utc).timestamp() + timeout_seconds

while datetime.now(tz=timezone.utc).timestamp() < deadline:
    reactions = await slack_client.get_reactions(message_ts)
    # check reactions...
    await asyncio.sleep(poll_interval_seconds)

# Fell through deadline — auto-reject
return False, None
```

`timeout_seconds=0` immediately falls through (the while condition is false before the first poll). Used in tests to trigger timeout without sleeping.

### 7. Transient Error Handling in Polling

A Slack API error during polling should not abort the approval wait — the next poll might succeed:

```python
try:
    reactions = await slack_client.get_reactions(message_ts)
except SlackError as exc:
    logger.warning("Failed to fetch reactions (poll %d): %s", polls, exc)
    await asyncio.sleep(poll_interval_seconds)
    continue  # skip to next poll
```

This is a deliberate resilience choice: transient network errors, Slack outages, or rate limits skip the current poll window but don't fail the batch. The timeout still enforces a hard deadline.

---

## Code Walkthrough

### SlackClient._call → _make_request

```python
async def _call(self, method, payload, http_method="POST"):
    url = f"{_SLACK_API}/{method}"
    async with aiohttp.ClientSession(headers=self._headers) as session:
        return await self._make_request(session, http_method, url, payload)
```

A new `ClientSession` is created per call. This is less efficient than a long-lived session but avoids lifecycle management complexity (when to create/close it). For a low-frequency operations agent, this is fine.

### approval.request_approval

```python
async def request_approval(slack_client, batch_id, report):
    text = (
        f":robot_face: *Errander-AI Dry-Run Complete* — batch `{batch_id}`\n\n"
        f"React with :white_check_mark: to *approve* live execution.\n"
        f"React with :x: to *reject*.\n\n"
        f"```\n{report[:2800]}\n```"  # Slack message limit ~4000 chars
    )
    ts = await slack_client.post_message(text)
    return ts
```

The report is truncated at 2800 characters. Slack's hard limit is 4000 characters per message; 2800 leaves room for the header/footer.

### approval.poll_approval

See Section 6 above for the loop structure. The function returns `(bool, str | None)`:
- `(True, "U_APPROVER_ID")` — approved
- `(False, "U_REJECTER_ID")` — rejected
- `(False, None)` — timed out

---

## Testing the aiohttp Mock Pattern

The hardest part of testing `SlackClient` is mocking `aiohttp.ClientSession` correctly. The session returns an async context manager from `session.post()`, not a plain response:

```python
def _ctx(resp: MagicMock) -> MagicMock:
    """Wrap a response mock as an async context manager."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm

# Usage in tests:
mock_session.post = MagicMock(return_value=_ctx(mock_resp))
```

This makes `async with session.post(url, json=payload) as resp:` work in tests — `session.post()` returns the `_ctx(mock_resp)` CM object, `__aenter__` gives back `mock_resp`, and `resp.json()` returns the configured data.

---

## Gotchas

### 1. `method` vs `http_method` parameter naming collision

`SlackClient._call(method, payload, http_method="POST")`:
- `method` = Slack API method name (e.g., `"reactions.get"`)
- `http_method` = HTTP verb (`"GET"` or `"POST"`)

It's easy to write `self._call("reactions.get", {...}, method="GET")` — which passes `"GET"` as the first positional arg (the Slack method name), not the HTTP method. Always use the keyword `http_method=`.

### 2. `continue` vs `break` in retry loops

Using `break` instead of `continue` in a rate-limit retry loop exits the loop silently, falling through to a post-loop `raise`. The second attempt never runs. Symptom: the test `test_retries_after_429` fails because `call_count` is 1 instead of 2. Always use `continue` to loop.

### 3. asyncio import location for patching

`asyncio.sleep` in the implementation must be patchable from tests as `errander.integrations.slack.asyncio.sleep`. This requires `import asyncio` at the module level, not inside the function. A function-level `import asyncio` creates a local reference that isn't patchable via the module path.

---

## Quiz Yourself

1. Why does `aiohttp` use `async with session.post()` instead of `await session.post()`?
2. What happens if you use `break` instead of `continue` in the rate-limit retry loop?
3. Why does rejection take priority over approval when both reactions are present?
4. What does `poll_approval` return if Slack is down for the entire timeout window?
5. Why is `timeout_seconds=0` used in the timeout test instead of mocking `datetime.now()`?
6. Why does `request_approval` truncate the report at 2800 characters, not 4000?
