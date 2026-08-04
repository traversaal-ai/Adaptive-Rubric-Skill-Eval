# Event names

The pattern is `<subject>_<past tense verb>`, all lowercase, words joined by underscores.

```
run_started
order_skipped
run_finished
```

Rules:

- Lowercase only. No camelCase, no dots, no dashes.
- At least two words. `error` is not an event name; `payment_failed` is.
- Past tense. The event already happened — `order_skipped`, not `skip_order` or `skipping_order`.
- The subject comes first, so events for one thing sort together in a log search.

Never reuse a name for two different things. If a run can finish two ways, that's `run_finished`
and `run_failed`, not one name with a status field.
