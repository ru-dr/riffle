// Package mapping turns a verified GitHub delivery into the messages intake publishes:
// PrEvent on pr-events (contracts/pr_event.schema.json) and InstallationEvent on
// installation-events (contracts/installation_event.schema.json). It reads only identifiers
// from the payload and never calls GitHub: intake's job ends at publish.
package mapping

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"time"
)

// Topics by their name in infra/pubsub/topics.json. The publisher maps them to the names in
// GCP (riffle-<environment>-<name>).
const (
	TopicPrEvents           = "pr-events"
	TopicInstallationEvents = "installation-events"
)

// Message is one thing to publish.
type Message struct {
	Topic       string
	OrderingKey string
	DeliveryID  string // the message's own delivery id (suffixed when one delivery fans out)
	TenantID    string
	Schema      string // contract name and version, sent as a message attribute
	Data        []byte
}

// Result is what a delivery maps to. Ignored is set, and Messages empty, when the delivery
// is valid but nothing downstream needs it (a closed PR, a check suite with no PR, ...).
type Result struct {
	Event    string
	Action   string
	TenantID string
	Messages []Message
	Ignored  string
}

// ErrMalformed means the payload lacks something every event of its kind has. GitHub does
// not send these, so the caller answers 400 and logs it.
var ErrMalformed = errors.New("malformed payload")

// PrEvent is contracts/pr_event.schema.json. Field order follows the schema.
type PrEvent struct {
	DeliveryID string `json:"delivery_id"`
	TenantID   string `json:"tenant_id"`
	Repo       string `json:"repo"`
	PRNumber   int    `json:"pr_number"`
	Action     string `json:"action"`
	HeadSHA    string `json:"head_sha"`
	ReceivedAt string `json:"received_at"`
}

// InstallationEvent is contracts/installation_event.schema.json.
type InstallationEvent struct {
	DeliveryID          string       `json:"delivery_id"`
	TenantID            string       `json:"tenant_id"`
	Event               string       `json:"event"`
	Action              string       `json:"action"`
	AccountLogin        string       `json:"account_login"`
	AccountType         string       `json:"account_type"`
	RepositoriesAdded   []Repository `json:"repositories_added"`
	RepositoriesRemoved []Repository `json:"repositories_removed"`
	ReceivedAt          string       `json:"received_at"`
}

// Repository is one entry of an installation event.
type Repository struct {
	ID       int64  `json:"id"`
	FullName string `json:"full_name"`
}

// The pull_request actions that can change a PR's risk; every rescore.on trigger.
var prActions = map[string]bool{
	"opened": true, "synchronize": true, "reopened": true,
	"ready_for_review": true, "edited": true, "labeled": true,
}

var installationActions = map[string]bool{
	"created": true, "deleted": true, "suspend": true, "unsuspend": true, "new_permissions_accepted": true,
}

var (
	repoName = regexp.MustCompile(`^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$`)
	sha1Hex  = regexp.MustCompile(`^[0-9a-f]{40}$`)
	// DeliveryID is the shape of GitHub's X-GitHub-Delivery header (a GUID).
	DeliveryID = regexp.MustCompile(`^[A-Za-z0-9-]{1,64}$`)
)

// The parts of GitHub's payloads intake reads. Everything else is ignored.
type payload struct {
	Action       string `json:"action"`
	Installation *struct {
		ID      int64 `json:"id"`
		Account struct {
			Login string `json:"login"`
			Type  string `json:"type"`
		} `json:"account"`
	} `json:"installation"`
	Repository struct {
		FullName string `json:"full_name"`
	} `json:"repository"`
	PullRequest struct {
		Number int `json:"number"`
		Head   struct {
			SHA string `json:"sha"`
		} `json:"head"`
	} `json:"pull_request"`
	CheckSuite struct {
		HeadSHA      string `json:"head_sha"`
		PullRequests []struct {
			Number int `json:"number"`
		} `json:"pull_requests"`
	} `json:"check_suite"`
	Repositories        []Repository `json:"repositories"`
	RepositoriesAdded   []Repository `json:"repositories_added"`
	RepositoriesRemoved []Repository `json:"repositories_removed"`
}

// Map maps one delivery. event is the X-GitHub-Event header, delivery the X-GitHub-Delivery
// header (already checked against DeliveryID), now the time it was verified.
func Map(event, delivery string, body []byte, now time.Time) (Result, error) {
	var p payload
	if err := json.Unmarshal(body, &p); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrMalformed, err)
	}
	r := Result{Event: event, Action: p.Action}
	if p.Installation == nil || p.Installation.ID <= 0 {
		r.Ignored = "no_installation"
		return r, nil
	}
	r.TenantID = strconv.FormatInt(p.Installation.ID, 10)
	received := now.UTC().Format(time.RFC3339Nano)

	switch event {
	case "pull_request":
		if !prActions[p.Action] {
			r.Ignored = "action_not_scored"
			return r, nil
		}
		m, err := prMessage(PrEvent{DeliveryID: delivery, TenantID: r.TenantID, Repo: p.Repository.FullName,
			PRNumber: p.PullRequest.Number, Action: p.Action, HeadSHA: p.PullRequest.Head.SHA, ReceivedAt: received})
		if err != nil {
			return Result{}, err
		}
		r.Messages = []Message{m}

	case "check_suite":
		if p.Action != "completed" {
			r.Ignored = "action_not_scored"
			return r, nil
		}
		// A fork's PRs are not listed in its check suite, and finding them would need a
		// GitHub call, which intake never makes. Those PRs are rescored on their next push.
		if len(p.CheckSuite.PullRequests) == 0 {
			r.Ignored = "no_pull_requests"
			return r, nil
		}
		fanOut := len(p.CheckSuite.PullRequests) > 1
		for _, pr := range p.CheckSuite.PullRequests {
			id := delivery
			if fanOut {
				id = delivery + ":" + strconv.Itoa(pr.Number)
			}
			m, err := prMessage(PrEvent{DeliveryID: id, TenantID: r.TenantID, Repo: p.Repository.FullName,
				PRNumber: pr.Number, Action: "ci_completed", HeadSHA: p.CheckSuite.HeadSHA, ReceivedAt: received})
			if err != nil {
				return Result{}, err
			}
			r.Messages = append(r.Messages, m)
		}

	case "installation", "installation_repositories":
		if event == "installation" && !installationActions[p.Action] ||
			event == "installation_repositories" && p.Action != "added" && p.Action != "removed" {
			r.Ignored = "action_not_handled"
			return r, nil
		}
		ev := InstallationEvent{DeliveryID: delivery, TenantID: r.TenantID, Event: event, Action: p.Action,
			AccountLogin: p.Installation.Account.Login, AccountType: p.Installation.Account.Type,
			RepositoriesAdded: nonNil(p.RepositoriesAdded), RepositoriesRemoved: nonNil(p.RepositoriesRemoved), ReceivedAt: received}
		if event == "installation" && p.Action == "created" {
			ev.RepositoriesAdded = nonNil(p.Repositories)
		}
		if ev.AccountLogin == "" || (ev.AccountType != "Organization" && ev.AccountType != "User") {
			return Result{}, fmt.Errorf("%w: installation account missing", ErrMalformed)
		}
		for _, repo := range append(append([]Repository{}, ev.RepositoriesAdded...), ev.RepositoriesRemoved...) {
			if repo.ID <= 0 || !repoName.MatchString(repo.FullName) {
				return Result{}, fmt.Errorf("%w: repository %q", ErrMalformed, repo.FullName)
			}
		}
		data, err := json.Marshal(ev)
		if err != nil {
			return Result{}, err
		}
		r.Messages = []Message{{Topic: TopicInstallationEvents, OrderingKey: r.TenantID, DeliveryID: delivery,
			TenantID: r.TenantID, Schema: "installation_event/1.0", Data: data}}

	default:
		r.Ignored = "event_not_handled"
	}
	return r, nil
}

func prMessage(ev PrEvent) (Message, error) {
	if !repoName.MatchString(ev.Repo) || ev.PRNumber < 1 || !sha1Hex.MatchString(ev.HeadSHA) {
		return Message{}, fmt.Errorf("%w: repo %q, pr %d, head %q", ErrMalformed, ev.Repo, ev.PRNumber, ev.HeadSHA)
	}
	data, err := json.Marshal(ev)
	if err != nil {
		return Message{}, err
	}
	return Message{Topic: TopicPrEvents, OrderingKey: fmt.Sprintf("%s/%s#%d", ev.TenantID, ev.Repo, ev.PRNumber),
		DeliveryID: ev.DeliveryID, TenantID: ev.TenantID, Schema: "pr_event/1.0", Data: data}, nil
}

// nonNil keeps an empty list as [] in JSON: the contract requires the key.
func nonNil(r []Repository) []Repository {
	if r == nil {
		return []Repository{}
	}
	return r
}
