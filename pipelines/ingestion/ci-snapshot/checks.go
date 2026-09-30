package main

// -mode checks: per-check results for a sample of failed commits.
//
// The combined CI result says a commit was red, not why: one flaky or
// optional job turns it red too. For up to -sample failed commits per
// repository, taken from the commits mode's output and spread evenly across
// the window, this records every check run (name, app, conclusion, timings;
// for Actions, each check run is a job) and every commit status (third-party
// CI such as Prow). From these, each repository's chronically failing
// checks can be told apart from real failures, and the ci_fail label
// cleaned across all commits. REST, so it runs beside the GraphQL modes.

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type CheckRun struct {
	Name        string  `json:"name"`
	App         string  `json:"app"`
	Status      string  `json:"status"`
	Conclusion  *string `json:"conclusion"`
	StartedAt   *string `json:"started_at"`
	CompletedAt *string `json:"completed_at"`
}

type StatusCtx struct {
	Context   string `json:"context"`
	State     string `json:"state"`
	CreatedAt string `json:"created_at"`
}

type ChecksLine struct {
	Repo        string      `json:"repo"`
	OID         string      `json:"oid"`
	CommittedAt string      `json:"committed_at"`
	PR          *int        `json:"pr"`
	CIState     *string     `json:"ci_state"`
	CheckRuns   []CheckRun  `json:"check_runs"`
	Statuses    []StatusCtx `json:"statuses"`
	CapturedAt  string      `json:"captured_at"`
}

func readLines[T any](path string) []T {
	var out []T
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 8<<20)
	for sc.Scan() {
		var v T
		if json.Unmarshal(sc.Bytes(), &v) == nil {
			out = append(out, v)
		}
	}
	return out
}

func (c *client) runChecks(worker int, repo string) {
	name := strings.ReplaceAll(repo, "/", "__")
	base := filepath.Join(c.cfg.out, name)
	if _, err := os.Stat(base + ".done"); err == nil {
		c.send(repoDoneMsg{repo: repo, commits: countLines(base + ".jsonl"), skipped: true})
		return
	}
	// Wait for the commits mode to finish this repository.
	src := filepath.Join(c.cfg.from, name)
	for waited := false; ; waited = true {
		if _, err := os.Stat(src + ".done"); err == nil {
			break
		}
		if !waited {
			c.send(eventMsg(repo + ": waiting for the commits run to finish it"))
		}
		c.send(progressMsg{worker: worker, repo: repo, total: 1000})
		time.Sleep(30 * time.Second)
	}

	var failed []Line
	for _, l := range readLines[Line](src + ".jsonl") {
		if l.CIState != nil && (*l.CIState == "FAILURE" || *l.CIState == "ERROR") {
			failed = append(failed, l)
		}
	}
	sort.Slice(failed, func(i, j int) bool { return failed[i].CommittedAt < failed[j].CommittedAt })
	// An even spread across the window, not just the newest.
	sample := failed
	if n := c.cfg.sample; len(failed) > n {
		sample = make([]Line, 0, n)
		for i := 0; i < n; i++ {
			sample = append(sample, failed[i*len(failed)/n])
		}
	}

	have := map[string]bool{}
	for _, l := range readLines[ChecksLine](base + ".jsonl") {
		have[l.OID] = true
	}
	f, err := os.OpenFile(base+".jsonl", os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		c.send(repoDoneMsg{repo: repo, err: err})
		return
	}
	defer f.Close()
	enc := json.NewEncoder(f)
	done := len(have)

	for i, l := range sample {
		if have[l.OID] {
			continue
		}
		out := ChecksLine{Repo: repo, OID: l.OID, CommittedAt: l.CommittedAt, PR: l.PR, CIState: l.CIState, CheckRuns: []CheckRun{}, Statuses: []StatusCtx{}}
		// Check runs: at most 3 pages of 100 (pytorch has hundreds of jobs).
		for page := 1; page <= 3; page++ {
			var cr struct {
				TotalCount int `json:"total_count"`
				CheckRuns  []struct {
					Name        string  `json:"name"`
					Status      string  `json:"status"`
					Conclusion  *string `json:"conclusion"`
					StartedAt   *string `json:"started_at"`
					CompletedAt *string `json:"completed_at"`
					App         *struct {
						Slug string `json:"slug"`
					} `json:"app"`
				} `json:"check_runs"`
			}
			if err := c.rest(repo, fmt.Sprintf("/repos/%s/commits/%s/check-runs?filter=latest&per_page=100&page=%d", repo, l.OID, page), &cr); err != nil {
				c.send(repoDoneMsg{repo: repo, commits: done, err: err})
				return
			}
			for _, r := range cr.CheckRuns {
				app := ""
				if r.App != nil {
					app = r.App.Slug
				}
				out.CheckRuns = append(out.CheckRuns, CheckRun{Name: r.Name, App: app, Status: r.Status, Conclusion: r.Conclusion, StartedAt: r.StartedAt, CompletedAt: r.CompletedAt})
			}
			if page*100 >= cr.TotalCount {
				break
			}
		}
		var st struct {
			Statuses []struct {
				Context   string `json:"context"`
				State     string `json:"state"`
				CreatedAt string `json:"created_at"`
			} `json:"statuses"`
		}
		if err := c.rest(repo, fmt.Sprintf("/repos/%s/commits/%s/status?per_page=100", repo, l.OID), &st); err != nil {
			c.send(repoDoneMsg{repo: repo, commits: done, err: err})
			return
		}
		for _, s := range st.Statuses {
			out.Statuses = append(out.Statuses, StatusCtx{Context: s.Context, State: s.State, CreatedAt: s.CreatedAt})
		}
		out.CapturedAt = time.Now().UTC().Format(time.RFC3339)
		enc.Encode(out)
		f.Sync()
		done++
		c.send(progressMsg{worker: worker, repo: repo, done: (i + 1) * 1000 / len(sample), total: 1000, pageSize: 100, records: done})
	}
	os.WriteFile(base+".done", []byte(time.Now().UTC().Format(time.RFC3339)+"\n"), 0o644)
	c.send(repoDoneMsg{repo: repo, commits: done})
}
