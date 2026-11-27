// Package publish sends mapped messages to Pub/Sub (or the emulator, when
// PUBSUB_EMULATOR_HOST is set: the client library picks that up by itself).
package publish

import (
	"context"
	"fmt"
	"time"

	"cloud.google.com/go/pubsub/v2"

	"github.com/ru-dr/riffle/services/intake/internal/mapping"
)

// PubSub publishes to one publisher per topic, with message ordering on: events of one PR
// (or one installation) carry the same ordering key and must arrive in order.
type PubSub struct {
	publishers map[string]*pubsub.Publisher
}

// New opens a publisher per topic. topics maps the names in infra/pubsub/topics.json
// (mapping.TopicPrEvents, ...) to the topic names in GCP (riffle-staging-pr-events, ...),
// which come from the riffle-platform-settings ConfigMap.
func New(client *pubsub.Client, topics map[string]string) *PubSub {
	ps := &PubSub{publishers: map[string]*pubsub.Publisher{}}
	for logical, actual := range topics {
		p := client.Publisher(actual)
		p.EnableMessageOrdering = true
		// intake answers GitHub within seconds, so do not hold messages back to batch them.
		p.PublishSettings.DelayThreshold = 5 * time.Millisecond
		ps.publishers[logical] = p
	}
	return ps
}

// Publish sends one message and waits until Pub/Sub has stored it, or ctx ends.
//
// After a failure the client pauses every later message with the same ordering key, to keep
// the order; ResumePublish lifts that, so GitHub's redelivery can be published.
func (ps *PubSub) Publish(ctx context.Context, m mapping.Message) error {
	p, ok := ps.publishers[m.Topic]
	if !ok {
		return fmt.Errorf("no publisher for topic %q", m.Topic)
	}
	result := p.Publish(ctx, &pubsub.Message{
		Data:        m.Data,
		OrderingKey: m.OrderingKey,
		Attributes:  map[string]string{"delivery_id": m.DeliveryID, "tenant_id": m.TenantID, "schema": m.Schema},
	})
	if _, err := result.Get(ctx); err != nil {
		if m.OrderingKey != "" {
			p.ResumePublish(m.OrderingKey)
		}
		return fmt.Errorf("publish to %s: %w", m.Topic, err)
	}
	return nil
}

// Stop sends anything still buffered and closes every publisher.
func (ps *PubSub) Stop() {
	for _, p := range ps.publishers {
		p.Stop()
	}
}
