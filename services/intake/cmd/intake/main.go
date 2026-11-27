// Command intake is Riffle's webhook front door: it verifies a GitHub delivery, drops
// duplicates, publishes it and answers within GitHub's 10 seconds.
//
//	intake               serve webhooks on INTAKE_ADDR (:8080) and metrics on METRICS_ADDR (:9090)
//	intake healthcheck   exit 0 if the local server answers /healthz (the image has no shell)
//
// In the cluster its settings come from the riffle-platform-settings ConfigMap and its
// secrets from the intake-secrets Secret (infra/k8s/README.md).
package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"cloud.google.com/go/pubsub/v2"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/redis/go-redis/v9"

	"github.com/ru-dr/riffle/services/intake/internal/dedupe"
	"github.com/ru-dr/riffle/services/intake/internal/mapping"
	"github.com/ru-dr/riffle/services/intake/internal/publish"
	"github.com/ru-dr/riffle/services/intake/internal/server"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheck())
	}
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil)).With("service", "intake")
	// go-redis logs on its own, in plain text; send it through the JSON logger so every line
	// Loki receives is JSON.
	redis.SetLogger(redisLog{log.With("component", "redis")})
	if err := run(log); err != nil {
		log.Error("intake stopped", "error", err)
		os.Exit(1)
	}
}

// config is read from the environment; services/intake/README.md lists every variable.
type config struct {
	addr, metricsAddr string
	secret            string
	project           string
	topics            map[string]string // topics.json name -> name in GCP
	redisAddr         string
	redisAuth         string
	redisCA           string
	deadline          time.Duration
	pendingTTL        time.Duration
	doneTTL           time.Duration
}

func load() (config, error) {
	c := config{
		addr:        env("INTAKE_ADDR", ":8080"),
		metricsAddr: env("METRICS_ADDR", ":9090"),
		secret:      os.Getenv("WEBHOOK_SECRET"),
		project:     os.Getenv("PUBSUB_PROJECT_ID"),
		topics: map[string]string{
			mapping.TopicPrEvents:           os.Getenv("TOPIC_PR_EVENTS"),
			mapping.TopicInstallationEvents: os.Getenv("TOPIC_INSTALLATION_EVENTS"),
		},
		redisAddr: os.Getenv("REDIS_ADDR"),
		redisAuth: os.Getenv("REDIS_AUTH"),
		redisCA:   os.Getenv("REDIS_CA_CERT"),
	}
	if c.redisAddr == "" {
		c.redisAddr = net.JoinHostPort(env("REDIS_HOST", "localhost"), env("REDIS_PORT", "6379"))
	}
	var err error
	if c.deadline, err = duration("INTAKE_DEADLINE", "8s"); err != nil {
		return c, err
	}
	if c.pendingTTL, err = duration("DEDUPE_PENDING_TTL", "5m"); err != nil {
		return c, err
	}
	if c.doneTTL, err = duration("DEDUPE_DONE_TTL", "72h"); err != nil {
		return c, err
	}
	switch {
	case c.secret == "":
		return c, errors.New("WEBHOOK_SECRET is not set")
	case c.project == "":
		return c, errors.New("PUBSUB_PROJECT_ID is not set")
	case c.deadline <= 0 || c.deadline >= 10*time.Second:
		return c, fmt.Errorf("INTAKE_DEADLINE %v must be above 0 and below GitHub's 10 s", c.deadline)
	}
	for logical, actual := range c.topics {
		if actual == "" {
			return c, fmt.Errorf("no GCP topic name for %s: set TOPIC_%s", logical, envName(logical))
		}
	}
	return c, nil
}

// redisOptions: Memorystore in GCP needs the AUTH string and TLS checked against its CA
// (REDIS_CA_CERT); without a CA, a plain connection (a local Redis).
func redisOptions(c config) (*redis.Options, error) {
	o := &redis.Options{Addr: c.redisAddr, Password: c.redisAuth,
		// A slow Redis must never eat GitHub's 10 s: fail fast and publish without dedupe.
		DialTimeout: 500 * time.Millisecond, ReadTimeout: 500 * time.Millisecond, WriteTimeout: 500 * time.Millisecond, MaxRetries: 1}
	if c.redisCA != "" {
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM([]byte(c.redisCA)) {
			return nil, errors.New("REDIS_CA_CERT holds no PEM certificate")
		}
		o.TLSConfig = &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}
	}
	return o, nil
}

func run(log *slog.Logger) error {
	cfg, err := load()
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Credentials: the pod's Google service account through Workload Identity; no key file.
	client, err := pubsub.NewClient(ctx, cfg.project)
	if err != nil {
		return fmt.Errorf("pubsub client: %w", err)
	}
	defer client.Close()
	publisher := publish.New(client, cfg.topics)
	defer publisher.Stop()

	opts, err := redisOptions(cfg)
	if err != nil {
		return err
	}
	rdb := redis.NewClient(opts)
	defer rdb.Close()
	store := dedupe.New(rdb, cfg.pendingTTL, cfg.doneTTL)
	if err := store.Ping(ctx); err != nil {
		log.Warn("redis not reachable at start; publishing without dedupe until it is", "error", err)
	}

	registry := prometheus.NewRegistry()
	registry.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	srv := server.New(server.Config{Secret: []byte(cfg.secret), Dedupe: store, Publisher: publisher,
		Logger: log, Deadline: cfg.deadline, Registry: registry})

	public := &http.Server{Addr: cfg.addr, Handler: srv.Handler(),
		ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 15 * time.Second}
	metrics := &http.Server{Addr: cfg.metricsAddr, Handler: server.Metrics(registry), ReadHeaderTimeout: 5 * time.Second}

	errs := make(chan error, 2)
	go func() { errs <- public.ListenAndServe() }()
	go func() { errs <- metrics.ListenAndServe() }()
	log.Info("intake listening", "addr", cfg.addr, "metrics_addr", cfg.metricsAddr, "project", cfg.project,
		"redis", cfg.redisAddr, "redis_tls", cfg.redisCA != "")

	select {
	case err := <-errs:
		return err
	case <-ctx.Done():
	}
	// Stop taking new work, finish what is in flight, then flush Pub/Sub (deferred above).
	log.Info("shutting down")
	srv.Stopping()
	shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = metrics.Shutdown(shutdown)
	return public.Shutdown(shutdown)
}

// redisLog adapts slog to go-redis's logger.
type redisLog struct{ log *slog.Logger }

func (l redisLog) Printf(ctx context.Context, format string, v ...interface{}) {
	l.log.WarnContext(ctx, fmt.Sprintf(format, v...))
}

func healthcheck() int {
	addr := env("INTAKE_ADDR", ":8080")
	if addr[0] == ':' {
		addr = "127.0.0.1" + addr
	}
	client := http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get("http://" + addr + "/healthz")
	if err != nil {
		return 1
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}

func env(name, fallback string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return fallback
}

// envName turns a topics.json name into its settings key: pr-events -> PR_EVENTS.
func envName(topic string) string {
	b := []byte(topic)
	for i, c := range b {
		switch {
		case c == '-':
			b[i] = '_'
		case c >= 'a' && c <= 'z':
			b[i] = c - 'a' + 'A'
		}
	}
	return string(b)
}

func duration(name, fallback string) (time.Duration, error) {
	v := env(name, fallback)
	d, err := time.ParseDuration(v)
	if err != nil {
		if secs, serr := strconv.Atoi(v); serr == nil {
			return time.Duration(secs) * time.Second, nil
		}
		return 0, fmt.Errorf("%s=%q is not a duration: %w", name, v, err)
	}
	return d, nil
}
