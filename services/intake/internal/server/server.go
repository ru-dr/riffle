// Package server is intake's HTTP front door: verify, dedupe, publish, answer. Nothing else:
// no database writes, no feature work, no GitHub calls (services/intake/README.md).
package server

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"sync/atomic"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/ru-dr/riffle/services/intake/internal/dedupe"
	"github.com/ru-dr/riffle/services/intake/internal/mapping"
	"github.com/ru-dr/riffle/services/intake/internal/webhook"
)

// MaxBody is GitHub's largest webhook payload; anything bigger was not sent by GitHub.
const MaxBody = 25 << 20

// Deduper is what the server needs from the dedupe store.
type Deduper interface {
	Claim(ctx context.Context, delivery string) (dedupe.Outcome, error)
	Done(ctx context.Context, delivery string) error
	Release(ctx context.Context, delivery string) error
}

// Publisher is what the server needs from Pub/Sub.
type Publisher interface {
	Publish(ctx context.Context, m mapping.Message) error
}

// Config holds what the server is built from.
type Config struct {
	Secret    []byte
	Dedupe    Deduper
	Publisher Publisher
	Logger    *slog.Logger
	// Deadline bounds the whole request, inside GitHub's 10 second limit.
	Deadline time.Duration
	Now      func() time.Time
	Registry prometheus.Registerer
}

// Server handles /webhook, /healthz and /readyz; metrics are served separately (Metrics).
type Server struct {
	cfg      Config
	stopping atomic.Bool
	requests *prometheus.CounterVec
	latency  *prometheus.HistogramVec
}

// New builds the server and registers its metrics.
func New(cfg Config) *Server {
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.Deadline == 0 {
		cfg.Deadline = 8 * time.Second
	}
	s := &Server{cfg: cfg}
	// Every riffle_ metric carries a tenant label (README, Observability); "unknown" before
	// the payload is verified and read.
	s.requests = prometheus.NewCounterVec(prometheus.CounterOpts{
		Name: "riffle_intake_events_total", Help: "Webhook deliveries by event and result.",
	}, []string{"tenant", "event", "result"})
	s.latency = prometheus.NewHistogramVec(prometheus.HistogramOpts{
		Name: "riffle_intake_latency_seconds", Help: "Time from request to answer; GitHub allows 10 s.",
		Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 8, 10},
	}, []string{"tenant", "result"})
	if cfg.Registry != nil {
		cfg.Registry.MustRegister(s.requests, s.latency)
	}
	return s
}

// Handler returns the public routes, the ones the load balancer reaches.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /webhook", s.webhook)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, _ *http.Request) {
		// Readiness does not depend on Redis: intake publishes without it (see Claim).
		if s.stopping.Load() {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
	return mux
}

// Metrics returns /metrics on its own handler, served on the port named "metrics": the
// network rules let Managed Prometheus reach that port only, and the load balancer never
// routes to it.
func Metrics(gatherer prometheus.Gatherer) http.Handler {
	mux := http.NewServeMux()
	mux.Handle("GET /metrics", promhttp.HandlerFor(gatherer, promhttp.HandlerOpts{}))
	return mux
}

// Stopping makes /readyz fail, so the load balancer sends no new requests during shutdown.
func (s *Server) Stopping() { s.stopping.Store(true) }

type answer struct {
	Status   string `json:"status"`
	Reason   string `json:"reason,omitempty"`
	Messages int    `json:"messages,omitempty"`
}

func (s *Server) webhook(w http.ResponseWriter, r *http.Request) {
	start := s.cfg.Now()
	ctx, cancel := context.WithTimeout(r.Context(), s.cfg.Deadline)
	defer cancel()

	event := r.Header.Get("X-GitHub-Event")
	delivery := r.Header.Get("X-GitHub-Delivery")
	log := s.cfg.Logger.With("delivery_id", delivery, "event", event)
	tenant := "unknown"
	reply := func(code int, a answer) {
		s.requests.WithLabelValues(tenant, event, a.Status).Inc()
		s.latency.WithLabelValues(tenant, a.Status).Observe(s.cfg.Now().Sub(start).Seconds())
		log.Info("webhook", "tenant_id", tenant, "result", a.Status, "reason", a.Reason, "messages", a.Messages,
			"status_code", code, "duration_ms", s.cfg.Now().Sub(start).Milliseconds())
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(code)
		_ = json.NewEncoder(w).Encode(a)
	}

	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, MaxBody))
	if err != nil {
		var tooBig *http.MaxBytesError
		if errors.As(err, &tooBig) {
			reply(http.StatusRequestEntityTooLarge, answer{Status: "rejected", Reason: "body_too_large"})
			return
		}
		reply(http.StatusBadRequest, answer{Status: "rejected", Reason: "unreadable_body"})
		return
	}
	if err := webhook.Verify(s.cfg.Secret, body, r.Header.Get("X-Hub-Signature-256")); err != nil {
		reply(http.StatusUnauthorized, answer{Status: "rejected", Reason: "bad_signature"})
		return
	}
	// Checked after the signature, so an unsigned caller learns nothing about what is valid.
	if event == "" || !mapping.DeliveryID.MatchString(delivery) {
		reply(http.StatusBadRequest, answer{Status: "rejected", Reason: "bad_headers"})
		return
	}
	if event == "ping" {
		reply(http.StatusOK, answer{Status: "pong"})
		return
	}

	result, err := mapping.Map(event, delivery, body, s.cfg.Now())
	if err != nil {
		log.Warn("malformed payload", "error", err)
		reply(http.StatusBadRequest, answer{Status: "rejected", Reason: "malformed_payload"})
		return
	}
	if result.TenantID != "" {
		tenant = result.TenantID
	}
	if result.Ignored != "" {
		reply(http.StatusAccepted, answer{Status: "ignored", Reason: result.Ignored})
		return
	}

	claimed := true
	switch outcome, err := s.cfg.Dedupe.Claim(ctx, delivery); {
	case err != nil:
		// Redis is down or slow: publish anyway. A duplicate is caught again by the scorer's
		// unique delivery_id; a dropped PR would be a broken promise.
		log.Warn("dedupe unavailable, publishing without it", "error", err)
		claimed = false
	case outcome == dedupe.Duplicate:
		reply(http.StatusOK, answer{Status: "duplicate"})
		return
	case outcome == dedupe.InFlight:
		reply(http.StatusAccepted, answer{Status: "in_flight"})
		return
	}

	for _, m := range result.Messages {
		if err := s.cfg.Publisher.Publish(ctx, m); err != nil {
			log.Error("publish failed", "error", err, "topic", m.Topic)
			if claimed {
				// A fresh context: ctx may be the one that ran out.
				releaseCtx, done := context.WithTimeout(context.WithoutCancel(ctx), time.Second)
				if err := s.cfg.Dedupe.Release(releaseCtx, delivery); err != nil {
					log.Warn("release failed; the claim expires on its own", "error", err)
				}
				done()
			}
			reply(http.StatusServiceUnavailable, answer{Status: "publish_failed"})
			return
		}
	}
	if claimed {
		if err := s.cfg.Dedupe.Done(ctx, delivery); err != nil {
			log.Warn("could not mark delivery done", "error", err)
		}
	}
	reply(http.StatusOK, answer{Status: "published", Messages: len(result.Messages)})
}
