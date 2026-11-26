# pubsub

The message queue between the services (diagram: "Message queue"), built from
[`infra/pubsub/topics.json`](../../../pubsub/topics.json). Add a topic there, not here.

Per topic in the file:

| Resource | Name | Why |
| --- | --- | --- |
| Topic | `<prefix>-<topic>` | Where the producer publishes |
| Subscription(s) | `<prefix>-<subscription>` | Where the consumer reads; ack deadline, ordering and retry backoff from the file |
| Dead-letter topic | `<prefix>-<topic>-dead-letter` | Receives a message after `max_delivery_attempts` failed tries |
| Hold subscription | `<prefix>-<topic>-dead-letter-hold` | Keeps dead letters for `hold_retention_days`; without it they would be discarded |
| Two permissions for Pub/Sub's own service agent | — | Publish to the dead-letter topic and acknowledge on the original subscription; dead-lettering silently fails without them |

| Setting | Why |
| --- | --- |
| Messages stored only in the region | Nothing leaves the installation's region |
| Subscriptions never expire | By default Pub/Sub deletes a subscription unread for 31 days, which would drop a quiet staging environment's queues |
| Unacknowledged messages kept 7 days | Time to fix a broken consumer before anything is lost |
| `consumer` label on subscriptions | Shows which service reads each one |
| The file is checked when Terraform reads it | Names, limits (attempts 5–100, ack 10–600 s, backoff ≤ 600 s), unique names and one subscription per topic at least; a mistake fails at `plan` with a message naming `topics.json` |

| Output | Used by |
| --- | --- |
| `topics`, `subscriptions` | Permissions per service (phase 9) and service settings |
| `dead_letter_subscriptions` | Where to look when messages fail; alerts (phase 13) |
