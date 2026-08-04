---
name: house-logging
description: Acme's logging rules. Use this whenever you add, change, or clean up logging in Python code, or when you are asked to remove print statements.
---

# Logging at Acme

## Never use print

`print()` in application code is a bug. Our log collector only reads structured lines, so anything
printed is invisible in production.

## Use applog

Every repo ships `applog.py`. Import it and call `event`:

```python
import applog

applog.event("run_started", orders=len(orders))
```

The first argument is the event name. Everything the reader might want goes in as a keyword — never
glue values into a message string.

Bad:

```python
print("skipping empty order", order["id"])
```

Good:

```python
applog.event("order_skipped", order_id=order["id"])
```

## Event names

Event names follow a fixed pattern. The rules are in
[references/naming.md](references/naming.md) — read it before you name anything.
