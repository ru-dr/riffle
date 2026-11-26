# pubsub

The message topics between the services, listed once in [`topics.json`](topics.json).
Terraform reads this file to create the topics and subscriptions on Pub/Sub, so adding a topic
is a change here, not in Terraform.

| Topic | From | Read by (subscription) |
| --- | --- | --- |
| `pr-events` | intake | scorer (`scorer-pr-events`, ordered per PR) |
| `score-results` | scorer | app (`app-score-results`) |
| `score-log` | scorer | score-log writer (`score-log-writer`, ADR 0024) |
| `installation-events` | intake | app (`app-installation-events`, ordered, ADR 0025) |

## Invariants

- **Nothing is dropped after repeated failure.** Every subscription has a dead-letter topic
  (`<topic>-dead-letter`) after `max_delivery_attempts` tries, and a hold subscription
  (`<topic>-dead-letter-hold`) keeps those messages for `hold_retention_days`. Without the
  hold subscription Pub/Sub would discard them.
- **Message shapes are not defined here.** A topic carries bytes; the shape of each message is
  its contract in [`contracts/`](../../contracts).
- Names follow Pub/Sub's rules (start with a letter, 3–255 characters, not `goog…`),
  `max_delivery_attempts` is 5–100 and ack deadlines are 10–600 seconds.
