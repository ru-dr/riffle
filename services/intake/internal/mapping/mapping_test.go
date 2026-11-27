package mapping

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

// The repo root, from services/intake/internal/mapping.
var root = filepath.Join("..", "..", "..", "..")

var now = time.Date(2026, 11, 26, 10, 0, 0, 0, time.UTC)

func fixture(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(root, "fixtures", "webhooks", name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// contract compiles contracts/<name>.schema.json with format checks on, so a message that
// drifts from the contract fails here, not in the scorer.
func contract(t *testing.T, name string) *jsonschema.Schema {
	t.Helper()
	path := filepath.Join(root, "contracts", name+".schema.json")
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	doc, err := jsonschema.UnmarshalJSON(f)
	if err != nil {
		t.Fatal(err)
	}
	c := jsonschema.NewCompiler()
	c.AssertFormat()
	if err := c.AddResource(path, doc); err != nil {
		t.Fatal(err)
	}
	s, err := c.Compile(path)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func validate(t *testing.T, m Message) map[string]any {
	t.Helper()
	name := strings.SplitN(m.Schema, "/", 2)[0]
	inst, err := jsonschema.UnmarshalJSON(bytes.NewReader(m.Data))
	if err != nil {
		t.Fatal(err)
	}
	if err := contract(t, name).Validate(inst); err != nil {
		t.Fatalf("%s breaks contracts/%s.schema.json:\n%v", m.Data, name, err)
	}
	var out map[string]any
	_ = json.Unmarshal(m.Data, &out)
	return out
}

func TestPullRequestOpenedBecomesOnePrEvent(t *testing.T) {
	r, err := Map("pull_request", "d-1", fixture(t, "pull_request.opened.json"), now)
	if err != nil || r.Ignored != "" || len(r.Messages) != 1 {
		t.Fatalf("got %+v, %v", r, err)
	}
	m := r.Messages[0]
	if m.Topic != TopicPrEvents || m.OrderingKey != "48213/acme-corp/payments#42" || m.Schema != "pr_event/1.0" {
		t.Fatalf("routing: %+v", m)
	}
	got := validate(t, m)
	want := map[string]any{"delivery_id": "d-1", "tenant_id": "48213", "repo": "acme-corp/payments", "pr_number": 42.0,
		"action": "opened", "head_sha": "a3f1c9e2" + strings.Repeat("0", 32), "received_at": "2026-11-26T10:00:00Z"}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("%s = %v, want %v", k, got[k], v)
		}
	}
}

func TestSynchronizeCarriesTheNewHead(t *testing.T) {
	r, err := Map("pull_request", "d-2", fixture(t, "pull_request.synchronize.json"), now)
	if err != nil || len(r.Messages) != 1 {
		t.Fatalf("got %+v, %v", r, err)
	}
	if got := validate(t, r.Messages[0]); got["head_sha"] != "b7d2e4f1"+strings.Repeat("0", 32) || got["action"] != "synchronize" {
		t.Fatalf("got %v", got)
	}
}

func TestACheckSuiteWithTwoPRsFansOutWithSuffixedDeliveryIDs(t *testing.T) {
	r, err := Map("check_suite", "d-3", fixture(t, "check_suite.completed.json"), now)
	if err != nil || len(r.Messages) != 2 {
		t.Fatalf("got %+v, %v", r, err)
	}
	for i, pr := range []string{"42", "57"} {
		got := validate(t, r.Messages[i])
		if got["delivery_id"] != "d-3:"+pr || got["action"] != "ci_completed" {
			t.Errorf("message %d: %v", i, got)
		}
		if r.Messages[i].OrderingKey != "48213/acme-corp/payments#"+pr {
			t.Errorf("message %d ordering key %q", i, r.Messages[i].OrderingKey)
		}
	}
}

func TestACheckSuiteWithOnePRKeepsTheDeliveryIDAsIs(t *testing.T) {
	var p map[string]any
	_ = json.Unmarshal(fixture(t, "check_suite.completed.json"), &p)
	suite := p["check_suite"].(map[string]any)
	suite["pull_requests"] = suite["pull_requests"].([]any)[:1]
	body, _ := json.Marshal(p)
	r, err := Map("check_suite", "d-4", body, now)
	if err != nil || len(r.Messages) != 1 || validate(t, r.Messages[0])["delivery_id"] != "d-4" {
		t.Fatalf("got %+v, %v", r, err)
	}
}

func TestIgnored(t *testing.T) {
	cases := []struct{ event, file, reason string }{
		{"pull_request", "pull_request.closed.json", "action_not_scored"},
		{"check_suite", "check_suite.completed.fork.json", "no_pull_requests"},
		{"ping", "ping.json", "no_installation"},
		{"issues", "pull_request.opened.json", "event_not_handled"},
	}
	for _, c := range cases {
		r, err := Map(c.event, "d", fixture(t, c.file), now)
		if err != nil || r.Ignored != c.reason || len(r.Messages) != 0 {
			t.Errorf("%s %s: got %+v, %v; want ignored %q", c.event, c.file, r, err, c.reason)
		}
	}
}

func TestInstallationCreatedListsItsRepositories(t *testing.T) {
	r, err := Map("installation", "d-5", fixture(t, "installation.created.json"), now)
	if err != nil || len(r.Messages) != 1 {
		t.Fatalf("got %+v, %v", r, err)
	}
	m := r.Messages[0]
	if m.Topic != TopicInstallationEvents || m.OrderingKey != "48213" || m.Schema != "installation_event/1.0" {
		t.Fatalf("routing: %+v", m)
	}
	got := validate(t, m)
	if got["action"] != "created" || got["account_login"] != "acme-corp" || len(got["repositories_added"].([]any)) != 2 ||
		len(got["repositories_removed"].([]any)) != 0 {
		t.Fatalf("got %v", got)
	}
}

func TestInstallationDeletedAndRepositoriesRemoved(t *testing.T) {
	for _, c := range []struct{ event, file, action string }{
		{"installation", "installation.deleted.json", "deleted"},
		{"installation_repositories", "installation_repositories.removed.json", "removed"},
	} {
		r, err := Map(c.event, "d-6", fixture(t, c.file), now)
		if err != nil || len(r.Messages) != 1 {
			t.Fatalf("%s: got %+v, %v", c.file, r, err)
		}
		if got := validate(t, r.Messages[0]); got["action"] != c.action {
			t.Errorf("%s: got %v", c.file, got)
		}
	}
}

func TestMalformedPayloadsAreRejectedNotPublished(t *testing.T) {
	for name, body := range map[string]string{
		"not json":     `{`,
		"bad head sha": `{"action":"opened","installation":{"id":1},"repository":{"full_name":"a/b"},"pull_request":{"number":1,"head":{"sha":"xyz"}}}`,
		"no pr number": `{"action":"opened","installation":{"id":1},"repository":{"full_name":"a/b"},"pull_request":{"head":{"sha":"` + strings.Repeat("a", 40) + `"}}}`,
		"no account":   `{"action":"created","installation":{"id":1}}`,
	} {
		event := "pull_request"
		if name == "no account" {
			event = "installation"
		}
		if _, err := Map(event, "d", []byte(body), now); !errors.Is(err, ErrMalformed) {
			t.Errorf("%s: want ErrMalformed, got %v", name, err)
		}
	}
}
