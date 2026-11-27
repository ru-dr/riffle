package main

import (
	"strings"
	"testing"
)

// The names intake reads must be the ones riffle-platform-settings (Terraform) and the
// intake-secrets ExternalSecret (infra/k8s/platform/secrets) provide.
func setEnv(t *testing.T, extra map[string]string) {
	t.Helper()
	base := map[string]string{
		"WEBHOOK_SECRET":            "s",
		"PUBSUB_PROJECT_ID":         "riffle-infra-platform",
		"TOPIC_PR_EVENTS":           "riffle-staging-pr-events",
		"TOPIC_INSTALLATION_EVENTS": "riffle-staging-installation-events",
		"REDIS_HOST":                "10.40.0.5",
		"REDIS_PORT":                "6378",
		"REDIS_AUTH":                "auth",
		"REDIS_ADDR":                "",
		"REDIS_CA_CERT":             "",
	}
	for k, v := range extra {
		base[k] = v
	}
	for k, v := range base {
		t.Setenv(k, v)
	}
}

func TestLoadReadsThePlatformSettingNames(t *testing.T) {
	setEnv(t, nil)
	c, err := load()
	if err != nil {
		t.Fatal(err)
	}
	if c.redisAddr != "10.40.0.5:6378" || c.topics["pr-events"] != "riffle-staging-pr-events" ||
		c.topics["installation-events"] != "riffle-staging-installation-events" || c.redisAuth != "auth" {
		t.Fatalf("got %+v", c)
	}
}

func TestLoadRefusesAMissingTopicOrSecret(t *testing.T) {
	setEnv(t, map[string]string{"TOPIC_INSTALLATION_EVENTS": ""})
	if _, err := load(); err == nil || !strings.Contains(err.Error(), "TOPIC_INSTALLATION_EVENTS") {
		t.Fatalf("want an error naming TOPIC_INSTALLATION_EVENTS, got %v", err)
	}
	setEnv(t, map[string]string{"WEBHOOK_SECRET": ""})
	if _, err := load(); err == nil {
		t.Fatal("want an error without WEBHOOK_SECRET")
	}
}

func TestRedisUsesTLSOnlyWithACertificate(t *testing.T) {
	setEnv(t, nil)
	c, _ := load()
	o, err := redisOptions(c)
	if err != nil || o.TLSConfig != nil {
		t.Fatalf("no CA: want plain, got %v, %v", o.TLSConfig, err)
	}
	c.redisCA = "not a certificate"
	if _, err := redisOptions(c); err == nil {
		t.Fatal("a CA that is not PEM must be refused, not silently ignored")
	}
}
