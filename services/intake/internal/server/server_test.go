package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/redis/go-redis/v9"

	"github.com/ru-dr/riffle/services/intake/internal/dedupe"
	"github.com/ru-dr/riffle/services/intake/internal/mapping"
	"github.com/ru-dr/riffle/services/intake/internal/webhook"
)

var secret = []byte("test-webhook-secret")

// fakePublisher records messages and fails while fail is set.
type fakePublisher struct {
	mu   sync.Mutex
	got  []mapping.Message
	fail bool
}

func (f *fakePublisher) Publish(_ context.Context, m mapping.Message) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.fail {
		return errors.New("pubsub unavailable")
	}
	f.got = append(f.got, m)
	return nil
}

func (f *fakePublisher) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.got)
}

type rig struct {
	srv     *Server
	http    http.Handler
	metrics http.Handler
	pub     *fakePublisher
	redis   *miniredis.Miniredis
	store   *dedupe.Store
}

func newRig(t *testing.T) *rig {
	t.Helper()
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr(), MaxRetries: -1, DialTimeout: 200 * time.Millisecond})
	t.Cleanup(func() { rdb.Close() })
	store := dedupe.New(rdb, 5*time.Minute, 72*time.Hour)
	pub := &fakePublisher{}
	reg := prometheus.NewRegistry()
	s := New(Config{Secret: secret, Dedupe: store, Publisher: pub, Registry: reg,
		Logger: slog.New(slog.NewJSONHandler(io.Discard, nil))})
	return &rig{srv: s, http: s.Handler(), metrics: Metrics(reg), pub: pub, redis: mr, store: store}
}

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "fixtures", "webhooks", name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

type response struct {
	code int
	body answer
}

func (r *rig) send(t *testing.T, event, delivery string, body []byte, signature string) response {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/webhook", bytes.NewReader(body))
	req.Header.Set("X-GitHub-Event", event)
	req.Header.Set("X-GitHub-Delivery", delivery)
	req.Header.Set("X-Hub-Signature-256", signature)
	rec := httptest.NewRecorder()
	r.http.ServeHTTP(rec, req)
	var a answer
	_ = json.Unmarshal(rec.Body.Bytes(), &a)
	return response{rec.Code, a}
}

func (r *rig) signed(t *testing.T, event, delivery, file string) response {
	t.Helper()
	body := fixture(t, file)
	return r.send(t, event, delivery, body, webhook.Sign(secret, body))
}

func TestASignedPROpenedIsPublishedOnce(t *testing.T) {
	r := newRig(t)
	if got := r.signed(t, "pull_request", "d-1", "pull_request.opened.json"); got.code != 200 || got.body.Status != "published" {
		t.Fatalf("got %+v", got)
	}
	if r.pub.count() != 1 || r.pub.got[0].Topic != mapping.TopicPrEvents {
		t.Fatalf("published %+v", r.pub.got)
	}
}

func TestTheSameDeliveryTwiceIsPublishedOnce(t *testing.T) {
	r := newRig(t)
	r.signed(t, "pull_request", "d-1", "pull_request.opened.json")
	if got := r.signed(t, "pull_request", "d-1", "pull_request.opened.json"); got.code != 200 || got.body.Status != "duplicate" {
		t.Fatalf("second: %+v", got)
	}
	if r.pub.count() != 1 {
		t.Fatalf("published %d times", r.pub.count())
	}
}

func TestADeliveryBeingPublishedElsewhereIsNotPublishedAgain(t *testing.T) {
	r := newRig(t)
	if _, err := r.store.Claim(context.Background(), "d-1"); err != nil {
		t.Fatal(err)
	}
	if got := r.signed(t, "pull_request", "d-1", "pull_request.opened.json"); got.code != 202 || got.body.Status != "in_flight" {
		t.Fatalf("got %+v", got)
	}
	if r.pub.count() != 0 {
		t.Fatal("published while another request held the claim")
	}
}

func TestABadSignatureIsRejectedAndNothingIsPublished(t *testing.T) {
	r := newRig(t)
	body := fixture(t, "pull_request.opened.json")
	for name, sig := range map[string]string{
		"wrong secret": webhook.Sign([]byte("other"), body),
		"missing":      "",
	} {
		if got := r.send(t, "pull_request", "d-1", body, sig); got.code != 401 {
			t.Errorf("%s: got %+v", name, got)
		}
	}
	if r.pub.count() != 0 {
		t.Fatal("published an unsigned delivery")
	}
}

func TestBadHeadersOnASignedRequestAreRejected(t *testing.T) {
	r := newRig(t)
	if got := r.signed(t, "pull_request", "not a guid!", "pull_request.opened.json"); got.code != 400 {
		t.Fatalf("got %+v", got)
	}
	if got := r.signed(t, "", "d-1", "pull_request.opened.json"); got.code != 400 {
		t.Fatalf("got %+v", got)
	}
}

func TestPingAnswersPong(t *testing.T) {
	r := newRig(t)
	if got := r.signed(t, "ping", "d-1", "ping.json"); got.code != 200 || got.body.Status != "pong" {
		t.Fatalf("got %+v", got)
	}
}

func TestEventsNobodyNeedsAreAcceptedAndIgnored(t *testing.T) {
	r := newRig(t)
	got := r.signed(t, "pull_request", "d-1", "pull_request.closed.json")
	if got.code != 202 || got.body.Status != "ignored" || got.body.Reason != "action_not_scored" || r.pub.count() != 0 {
		t.Fatalf("got %+v", got)
	}
}

func TestACheckSuiteFansOutToEveryPR(t *testing.T) {
	r := newRig(t)
	if got := r.signed(t, "check_suite", "d-1", "check_suite.completed.json"); got.code != 200 || got.body.Messages != 2 {
		t.Fatalf("got %+v", got)
	}
}

func TestInstallationEventsGoToTheirOwnTopic(t *testing.T) {
	r := newRig(t)
	r.signed(t, "installation", "d-1", "installation.created.json")
	if r.pub.count() != 1 || r.pub.got[0].Topic != mapping.TopicInstallationEvents {
		t.Fatalf("published %+v", r.pub.got)
	}
}

func TestAMalformedSignedPayloadIsRejected(t *testing.T) {
	r := newRig(t)
	body := []byte(`{"action":"opened","installation":{"id":1},"repository":{"full_name":"a/b"},"pull_request":{"number":1,"head":{"sha":"nope"}}}`)
	if got := r.send(t, "pull_request", "d-1", body, webhook.Sign(secret, body)); got.code != 400 || got.body.Reason != "malformed_payload" {
		t.Fatalf("got %+v", got)
	}
}

func TestAFailedPublishAnswers503AndTheRedeliveryIsPublished(t *testing.T) {
	r := newRig(t)
	r.pub.fail = true
	if got := r.signed(t, "pull_request", "d-1", "pull_request.opened.json"); got.code != 503 {
		t.Fatalf("got %+v", got)
	}
	r.pub.fail = false
	if got := r.signed(t, "pull_request", "d-1", "pull_request.opened.json"); got.code != 200 || got.body.Status != "published" {
		t.Fatalf("the redelivery was dropped as a duplicate: %+v", got)
	}
}

func TestWithRedisDownTheEventIsStillPublished(t *testing.T) {
	r := newRig(t)
	r.redis.Close()
	start := time.Now()
	if got := r.signed(t, "pull_request", "d-1", "pull_request.opened.json"); got.code != 200 || got.body.Status != "published" {
		t.Fatalf("got %+v", got)
	}
	if took := time.Since(start); took > 3*time.Second {
		t.Fatalf("took %v; GitHub allows 10 s", took)
	}
	if r.pub.count() != 1 {
		t.Fatal("not published")
	}
}

func TestATooLargeBodyIsRejected(t *testing.T) {
	r := newRig(t)
	body := []byte(`{"x":"` + strings.Repeat("a", MaxBody) + `"}`)
	if got := r.send(t, "pull_request", "d-1", body, webhook.Sign(secret, body)); got.code != 413 {
		t.Fatalf("got %+v", got)
	}
}

func TestOnlyPostIsAllowedOnWebhook(t *testing.T) {
	r := newRig(t)
	rec := httptest.NewRecorder()
	r.http.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/webhook", nil))
	if rec.Code != http.StatusMethodNotAllowed {
		t.Fatalf("got %d", rec.Code)
	}
}

func TestMetricsCarryTheTenantAndAreNotOnThePublicPort(t *testing.T) {
	r := newRig(t)
	r.signed(t, "pull_request", "d-1", "pull_request.opened.json")
	if got := testutil.ToFloat64(r.srv.requests.WithLabelValues("48213", "pull_request", "published")); got != 1 {
		t.Fatalf("riffle_intake_events_total = %v", got)
	}
	rec := httptest.NewRecorder()
	r.metrics.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if !strings.Contains(rec.Body.String(), `riffle_intake_latency_seconds_count{result="published",tenant="48213"} 1`) {
		t.Fatalf("latency metric missing:\n%s", rec.Body.String())
	}
	public := httptest.NewRecorder()
	r.http.ServeHTTP(public, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if public.Code != http.StatusNotFound {
		t.Fatalf("/metrics answered %d on the public port", public.Code)
	}
}

func TestReadyzFailsOnceStopping(t *testing.T) {
	r := newRig(t)
	for _, want := range []int{200, 503} {
		rec := httptest.NewRecorder()
		r.http.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/readyz", nil))
		if rec.Code != want {
			t.Fatalf("readyz = %d, want %d", rec.Code, want)
		}
		r.srv.Stopping()
	}
}
