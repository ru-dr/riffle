package publish

import (
	"context"
	"testing"
	"time"

	"cloud.google.com/go/pubsub/v2"
	"cloud.google.com/go/pubsub/v2/apiv1/pubsubpb"
	"cloud.google.com/go/pubsub/v2/pstest"
	"google.golang.org/api/option"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	"github.com/ru-dr/riffle/services/intake/internal/mapping"
)

const project = "test"

// The names in GCP carry the environment prefix; messages use the topics.json names.
var topics = map[string]string{
	mapping.TopicPrEvents:           "riffle-test-pr-events",
	mapping.TopicInstallationEvents: "riffle-test-installation-events",
}

// fakeClient is a Pub/Sub client on the in-memory pstest server, with intake's topics made.
func fakeClient(t *testing.T, opts ...pstest.ServerReactorOption) (*pubsub.Client, *pstest.Server) {
	t.Helper()
	srv := pstest.NewServer(opts...)
	t.Cleanup(func() { srv.Close() })
	conn, err := grpc.NewClient(srv.Addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	client, err := pubsub.NewClient(context.Background(), project, option.WithGRPCConn(conn))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { client.Close() })
	for _, actual := range topics {
		if _, err := client.TopicAdminClient.CreateTopic(context.Background(),
			&pubsubpb.Topic{Name: "projects/" + project + "/topics/" + actual}); err != nil {
			t.Fatal(err)
		}
	}
	return client, srv
}

func message(delivery string) mapping.Message {
	return mapping.Message{Topic: mapping.TopicPrEvents, OrderingKey: "1/a/b#1", DeliveryID: delivery,
		TenantID: "1", Schema: "pr_event/1.0", Data: []byte(`{"delivery_id":"` + delivery + `"}`)}
}

func TestPublishGoesToThePrefixedTopicWithItsKeyAndAttributes(t *testing.T) {
	client, srv := fakeClient(t)
	ps := New(client, topics)
	defer ps.Stop()
	if err := ps.Publish(context.Background(), message("d1")); err != nil {
		t.Fatal(err)
	}
	got := srv.Messages()
	if len(got) != 1 {
		t.Fatalf("want 1 message, got %d", len(got))
	}
	m := got[0]
	if m.Topic != "projects/test/topics/riffle-test-pr-events" {
		t.Fatalf("published to %q, not the prefixed topic", m.Topic)
	}
	if m.OrderingKey != "1/a/b#1" || m.Attributes["delivery_id"] != "d1" || m.Attributes["tenant_id"] != "1" ||
		m.Attributes["schema"] != "pr_event/1.0" || string(m.Data) != `{"delivery_id":"d1"}` {
		t.Fatalf("got %+v", m)
	}
}

func TestAnUnknownTopicIsAnError(t *testing.T) {
	client, _ := fakeClient(t)
	ps := New(client, topics)
	defer ps.Stop()
	m := message("d1")
	m.Topic = "nope"
	if err := ps.Publish(context.Background(), m); err == nil {
		t.Fatal("want an error")
	}
}

// failOnce makes the fake server refuse the first Publish call only.
type failOnce struct{ done bool }

func (f *failOnce) React(any) (bool, any, error) {
	if f.done {
		return false, nil, nil
	}
	f.done = true
	return true, nil, status.Error(codes.PermissionDenied, "injected")
}

func TestAfterAFailureTheSameOrderingKeyCanPublishAgain(t *testing.T) {
	// Without ResumePublish, the client refuses every later message with that key.
	client, srv := fakeClient(t, pstest.ServerReactorOption{FuncName: "Publish", Reactor: &failOnce{}})
	ps := New(client, topics)
	defer ps.Stop()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := ps.Publish(ctx, message("d1")); err == nil {
		t.Fatal("want the injected failure")
	}
	if err := ps.Publish(ctx, message("d1")); err != nil {
		t.Fatalf("the ordering key stayed paused after a failure: %v", err)
	}
	if n := len(srv.Messages()); n != 1 {
		t.Fatalf("want 1 stored message, got %d", n)
	}
}
