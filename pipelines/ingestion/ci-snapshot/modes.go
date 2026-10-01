package main

// The two extra modes, beside the default commit walk:
//
//   -mode prs   every pull request closed in the window: number, state,
//               dates, author, and its last commit's combined CI result -
//               the CI a PR had when it was merged or closed. GraphQL.
//   -mode runs  GitHub Actions runs triggered by pushes to the default
//               branch: workflow, conclusion, attempt, timings. REST, so it
//               spends the REST quota and runs alongside the GraphQL modes.

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// --- shared helpers -----------------------------------------------------------

// graphql posts a query and decodes data into out, with the same waiting and
// retry rules as the commit walk. errTooHeavy means "try a smaller page".
func (c *client) graphql(label, q string, vars map[string]any, out any) error {
	body, _ := json.Marshal(map[string]any{"query": q, "variables": vars})
	var lastErr error
	for attempt := 0; attempt < 6; attempt++ {
		c.waitForQuota()
		req, _ := http.NewRequest("POST", "https://api.github.com/graphql", bytes.NewReader(body))
		req.Header.Set("Authorization", "bearer "+c.cfg.token)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("User-Agent", "riffle-ci-snapshot")
		res, err := c.http.Do(req)
		if err != nil {
			lastErr = err
			time.Sleep(time.Duration(10*(attempt+1)) * time.Second)
			continue
		}
		raw, _ := io.ReadAll(res.Body)
		res.Body.Close()
		switch {
		case res.StatusCode == 502 || res.StatusCode == 504:
			return errTooHeavy
		case res.StatusCode == 403 || res.StatusCode == 429:
			wait := retryAfter(res, attempt)
			c.send(eventMsg(fmt.Sprintf("%s: secondary limit, waiting %s", label, wait.Round(time.Second))))
			time.Sleep(wait)
			continue
		case res.StatusCode >= 500:
			lastErr = fmt.Errorf("http %d", res.StatusCode)
			time.Sleep(time.Duration(15*(attempt+1)) * time.Second)
			continue
		case res.StatusCode != 200:
			return fmt.Errorf("http %d", res.StatusCode)
		}
		var env struct {
			Data   json.RawMessage
			Errors []struct{ Message, Type string }
		}
		if err := json.Unmarshal(raw, &env); err != nil {
			lastErr = err
			continue
		}
		if len(env.Errors) > 0 {
			msg := strings.ToLower(env.Errors[0].Type + " " + env.Errors[0].Message)
			if strings.Contains(msg, "rate") {
				c.send(eventMsg(label + ": rate limited, waiting 2m"))
				time.Sleep(2 * time.Minute)
				continue
			}
			if strings.Contains(msg, "timeout") || strings.Contains(msg, "went wrong") {
				return errTooHeavy
			}
			return errors.New(env.Errors[0].Message)
		}
		var rl struct {
			RateLimit struct {
				Remaining int
				ResetAt   time.Time
			}
		}
		json.Unmarshal(env.Data, &rl)
		c.mu.Lock()
		c.rem, c.reset = rl.RateLimit.Remaining, rl.RateLimit.ResetAt
		c.mu.Unlock()
		c.send(quotaMsg{remaining: rl.RateLimit.Remaining, reset: rl.RateLimit.ResetAt})
		return json.Unmarshal(env.Data, out)
	}
	return fmt.Errorf("gave up: %v", lastErr)
}

func retryAfter(res *http.Response, attempt int) time.Duration {
	if s := res.Header.Get("Retry-After"); s != "" {
		if secs, err := strconv.Atoi(s); err == nil {
			return time.Duration(secs+2) * time.Second
		}
	}
	if res.Header.Get("X-RateLimit-Remaining") == "0" {
		if r, err := strconv.ParseInt(res.Header.Get("X-RateLimit-Reset"), 10, 64); err == nil {
			return time.Until(time.Unix(r, 0)) + 5*time.Second
		}
	}
	return time.Minute * time.Duration(attempt+1)
}

// rest GETs a REST path, tracking the REST quota separately from GraphQL.
func (c *client) rest(label, path string, out any) error {
	var lastErr error
	for attempt := 0; attempt < 6; attempt++ {
		c.mu.Lock()
		rem, reset := c.restRem, c.restReset
		c.mu.Unlock()
		if rem >= 0 && rem < 50 && time.Until(reset) > 0 {
			c.send(pausedMsg{until: reset.Add(5 * time.Second)})
			time.Sleep(time.Until(reset) + 5*time.Second)
			c.send(pausedMsg{})
		}
		req, _ := http.NewRequest("GET", "https://api.github.com"+path, nil)
		req.Header.Set("Authorization", "Bearer "+c.cfg.token)
		req.Header.Set("Accept", "application/vnd.github+json")
		req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
		req.Header.Set("User-Agent", "riffle-ci-snapshot")
		res, err := c.http.Do(req)
		if err != nil {
			lastErr = err
			time.Sleep(time.Duration(10*(attempt+1)) * time.Second)
			continue
		}
		raw, _ := io.ReadAll(res.Body)
		res.Body.Close()
		if r, err := strconv.Atoi(res.Header.Get("X-RateLimit-Remaining")); err == nil {
			rs, _ := strconv.ParseInt(res.Header.Get("X-RateLimit-Reset"), 10, 64)
			c.mu.Lock()
			c.restRem, c.restReset = r, time.Unix(rs, 0)
			c.mu.Unlock()
			c.send(quotaMsg{remaining: r, reset: time.Unix(rs, 0)})
		}
		switch {
		case res.StatusCode == 200:
			return json.Unmarshal(raw, out)
		case res.StatusCode == 404:
			return errors.New("not found")
		case res.StatusCode == 403 || res.StatusCode == 429:
			wait := retryAfter(res, attempt)
			c.send(eventMsg(fmt.Sprintf("%s: rate limited, waiting %s", label, wait.Round(time.Second))))
			time.Sleep(wait)
			continue
		default:
			lastErr = fmt.Errorf("http %d", res.StatusCode)
			time.Sleep(time.Duration(15*(attempt+1)) * time.Second)
		}
	}
	return fmt.Errorf("gave up: %v", lastErr)
}

// output opens a repository's JSONL file. With resume, it keeps what is
// there; otherwise it starts clean.
func openOut(base string, resume bool) (*os.File, *json.Encoder, error) {
	if !resume {
		os.Remove(base + ".jsonl")
	}
	f, err := os.OpenFile(base+".jsonl", os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, nil, err
	}
	return f, json.NewEncoder(f), nil
}

func windowFrac(t, since, until time.Time) int {
	// Share of the window covered, walking from newest to oldest, in 1/1000s.
	span := until.Sub(since)
	if span <= 0 {
		return 1000
	}
	f := until.Sub(t).Seconds() / span.Seconds()
	return int(max(0, min(1, f)) * 1000)
}

// --- -mode prs ----------------------------------------------------------------

const prQuery = `query($owner:String!,$name:String!,$after:String,$n:Int!){
  rateLimit{cost remaining resetAt}
  repository(owner:$owner,name:$name){pullRequests(first:$n,after:$after,states:[MERGED,CLOSED],orderBy:{field:CREATED_AT,direction:DESC}){
    pageInfo{hasNextPage endCursor}
    nodes{number state createdAt closedAt mergedAt baseRefName isDraft author{login} mergeCommit{oid}
      commits(last:1){nodes{commit{oid statusCheckRollup{state}}}}}}}}`

type PRLine struct {
	Repo      string  `json:"repo"`
	Number    int     `json:"number"`
	State     string  `json:"state"`
	CreatedAt string  `json:"created_at"`
	ClosedAt  string  `json:"closed_at"`
	MergedAt  *string `json:"merged_at"`
	Base      string  `json:"base"`
	Author    *string `json:"author"`
	// The commit repo-miner computes label_ci_fail on (merge_commit_sha).
	MergeCommit *string `json:"merge_commit_sha"`
	HeadOID     *string `json:"head_oid"`
	HeadCI      *string `json:"head_ci_state"`
	CapturedAt  string  `json:"captured_at"`
}

func (c *client) runPRs(worker int, repo string) {
	owner, name, _ := strings.Cut(repo, "/")
	base := filepath.Join(c.cfg.out, strings.ReplaceAll(repo, "/", "__"))
	if _, err := os.Stat(base + ".done"); err == nil {
		c.send(repoDoneMsg{repo: repo, commits: countLines(base + ".jsonl"), skipped: true})
		return
	}
	since, _ := time.Parse(time.RFC3339, c.cfg.since)
	until, _ := time.Parse(time.RFC3339, c.cfg.until)
	after := ""
	if b, err := os.ReadFile(base + ".cursor"); err == nil {
		after = strings.TrimSpace(string(b))
	}
	f, enc, err := openOut(base, after != "")
	if err != nil {
		c.send(repoDoneMsg{repo: repo, err: err})
		return
	}
	defer f.Close()
	done := 0
	if after != "" {
		done = countLines(base + ".jsonl")
	}

	sizes := []int{50, 25, 10}
	sizeIdx := 0
	for {
		var d struct {
			Repository *struct {
				PullRequests struct {
					PageInfo struct {
						HasNextPage bool
						EndCursor   string
					}
					Nodes []struct {
						Number                     int
						State, CreatedAt, ClosedAt string
						MergedAt                   *string
						BaseRefName                string
						Author                     *struct{ Login string }
						MergeCommit                *struct{ Oid string }
						Commits                    struct {
							Nodes []struct {
								Commit struct {
									Oid               string
									StatusCheckRollup *struct{ State string }
								}
							}
						}
					}
				}
			}
		}
		// Start at 50 and keep whatever size last worked for this repository.
		// Retrying 100 on every page of a giant (llvm, zed, vllm) timed out
		// again and again, which also tripped GitHub's secondary limit.
		var err error
		for ; sizeIdx < len(sizes); sizeIdx++ {
			vars := map[string]any{"owner": owner, "name": name, "n": sizes[sizeIdx]}
			if after != "" {
				vars["after"] = after
			}
			err = c.graphql(repo, prQuery, vars, &d)
			if !errors.Is(err, errTooHeavy) {
				break
			}
			c.send(eventMsg(fmt.Sprintf("%s: timed out at %d a page, using smaller pages", repo, sizes[sizeIdx])))
			time.Sleep(3 * time.Second)
		}
		if sizeIdx == len(sizes) {
			sizeIdx = len(sizes) - 1
			err = errors.New("times out even at 10 a page")
		}
		size := sizes[sizeIdx]
		if err != nil {
			c.send(repoDoneMsg{repo: repo, commits: done, err: err})
			return
		}
		if d.Repository == nil {
			c.send(repoDoneMsg{repo: repo, err: errors.New("repository not found")})
			return
		}
		prs := d.Repository.PullRequests
		now := time.Now().UTC().Format(time.RFC3339)
		oldest := until
		for _, p := range prs.Nodes {
			created, _ := time.Parse(time.RFC3339, p.CreatedAt)
			closed, _ := time.Parse(time.RFC3339, p.ClosedAt)
			if created.Before(oldest) {
				oldest = created
			}
			// Created newest-first; keep PRs closed inside the window.
			if closed.Before(since) || !closed.Before(until) {
				continue
			}
			l := PRLine{Repo: repo, Number: p.Number, State: p.State, CreatedAt: p.CreatedAt, ClosedAt: p.ClosedAt, MergedAt: p.MergedAt, Base: p.BaseRefName, CapturedAt: now}
			if p.Author != nil {
				l.Author = &p.Author.Login
			}
			if p.MergeCommit != nil {
				l.MergeCommit = &p.MergeCommit.Oid
			}
			if len(p.Commits.Nodes) > 0 {
				cm := p.Commits.Nodes[0].Commit
				l.HeadOID = &cm.Oid
				if cm.StatusCheckRollup != nil {
					l.HeadCI = &cm.StatusCheckRollup.State
				}
			}
			enc.Encode(l)
			done++
		}
		f.Sync()
		c.send(progressMsg{worker: worker, repo: repo, done: windowFrac(oldest, since, until), total: 1000, pageSize: size, added: 0, records: done})
		// Stop once PRs are created before the window starts, with a month's
		// margin for PRs that stayed open a long time before closing.
		if !prs.PageInfo.HasNextPage || oldest.Before(since.AddDate(0, -1, 0)) {
			break
		}
		after = prs.PageInfo.EndCursor
		os.WriteFile(base+".cursor", []byte(after), 0o644)
	}
	os.WriteFile(base+".done", []byte(time.Now().UTC().Format(time.RFC3339)+"\n"), 0o644)
	c.send(repoDoneMsg{repo: repo, commits: done})
}

// --- -mode runs ---------------------------------------------------------------

type RunLine struct {
	Repo       string  `json:"repo"`
	ID         int64   `json:"id"`
	Workflow   string  `json:"workflow"`
	WorkflowID int64   `json:"workflow_id"`
	Path       string  `json:"path"`
	HeadSHA    string  `json:"head_sha"`
	Branch     string  `json:"branch"`
	Event      string  `json:"event"`
	Status     string  `json:"status"`
	Conclusion *string `json:"conclusion"`
	Attempt    int     `json:"run_attempt"`
	RunNumber  int     `json:"run_number"`
	CreatedAt  string  `json:"created_at"`
	StartedAt  string  `json:"run_started_at"`
	UpdatedAt  string  `json:"updated_at"`
	CapturedAt string  `json:"captured_at"`
}

type runsPage struct {
	TotalCount   int `json:"total_count"`
	WorkflowRuns []struct {
		ID           int64   `json:"id"`
		Name         string  `json:"name"`
		WorkflowID   int64   `json:"workflow_id"`
		Path         string  `json:"path"`
		HeadSHA      string  `json:"head_sha"`
		HeadBranch   string  `json:"head_branch"`
		Event        string  `json:"event"`
		Status       string  `json:"status"`
		Conclusion   *string `json:"conclusion"`
		RunAttempt   int     `json:"run_attempt"`
		RunNumber    int     `json:"run_number"`
		CreatedAt    string  `json:"created_at"`
		RunStartedAt string  `json:"run_started_at"`
		UpdatedAt    string  `json:"updated_at"`
	} `json:"workflow_runs"`
}

func (c *client) runRuns(worker int, repo string) {
	base := filepath.Join(c.cfg.out, strings.ReplaceAll(repo, "/", "__"))
	if _, err := os.Stat(base + ".done"); err == nil {
		c.send(repoDoneMsg{repo: repo, commits: countLines(base + ".jsonl"), skipped: true})
		return
	}
	var meta struct {
		DefaultBranch string `json:"default_branch"`
	}
	if err := c.rest(repo, "/repos/"+repo, &meta); err != nil {
		c.send(repoDoneMsg{repo: repo, err: err})
		return
	}
	since, _ := time.Parse(time.RFC3339, c.cfg.since)
	until, _ := time.Parse(time.RFC3339, c.cfg.until)

	// Finished day-slices are recorded, so a restart skips them.
	doneSlices := map[string]bool{}
	if f, err := os.Open(base + ".slices"); err == nil {
		sc := bufio.NewScanner(f)
		for sc.Scan() {
			doneSlices[sc.Text()] = true
		}
		f.Close()
	}
	f, enc, err := openOut(base, len(doneSlices) > 0)
	if err != nil {
		c.send(repoDoneMsg{repo: repo, err: err})
		return
	}
	defer f.Close()
	sl, _ := os.OpenFile(base+".slices", os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	defer sl.Close()
	done := 0
	if len(doneSlices) > 0 {
		done = countLines(base + ".jsonl")
	}

	// Filtered run lists stop at 1,000 results: ask for the whole window, and
	// halve any slice with more, newest half first, until each fits.
	var walk func(from, to time.Time) error
	walk = func(from, to time.Time) error {
		key := from.Format(time.RFC3339) + ".." + to.Format(time.RFC3339)
		if doneSlices[key] {
			return nil
		}
		q := url.Values{}
		q.Set("branch", meta.DefaultBranch)
		q.Set("event", "push")
		q.Set("per_page", "100")
		q.Set("created", key)
		var first runsPage
		if err := c.rest(repo, "/repos/"+repo+"/actions/runs?"+q.Encode()+"&page=1", &first); err != nil {
			return err
		}
		if first.TotalCount > 1000 && to.Sub(from) > 30*time.Minute {
			mid := from.Add(to.Sub(from) / 2)
			if err := walk(mid, to); err != nil {
				return err
			}
			return walk(from, mid)
		}
		pages := []runsPage{first}
		for p := 2; (p-1)*100 < min(first.TotalCount, 1000); p++ {
			var next runsPage
			if err := c.rest(repo, "/repos/"+repo+"/actions/runs?"+q.Encode()+"&page="+strconv.Itoa(p), &next); err != nil {
				return err
			}
			pages = append(pages, next)
		}
		now := time.Now().UTC().Format(time.RFC3339)
		for _, pg := range pages {
			for _, r := range pg.WorkflowRuns {
				enc.Encode(RunLine{Repo: repo, ID: r.ID, Workflow: r.Name, WorkflowID: r.WorkflowID, Path: r.Path, HeadSHA: r.HeadSHA, Branch: r.HeadBranch, Event: r.Event, Status: r.Status, Conclusion: r.Conclusion, Attempt: r.RunAttempt, RunNumber: r.RunNumber, CreatedAt: r.CreatedAt, StartedAt: r.RunStartedAt, UpdatedAt: r.UpdatedAt, CapturedAt: now})
				done++
			}
		}
		f.Sync()
		fmt.Fprintln(sl, key)
		c.send(progressMsg{worker: worker, repo: repo, done: windowFrac(from, since, until), total: 1000, pageSize: 100, records: done})
		return nil
	}
	if err := walk(since, until); err != nil {
		c.send(repoDoneMsg{repo: repo, commits: done, err: err})
		return
	}
	os.WriteFile(base+".done", []byte(time.Now().UTC().Format(time.RFC3339)+"\n"), 0o644)
	c.send(repoDoneMsg{repo: repo, commits: done})
}
