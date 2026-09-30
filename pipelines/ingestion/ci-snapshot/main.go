// ci-snapshot saves the combined CI result of every default-branch commit in
// the dataset's repositories, before GitHub's 90-day retention for check
// runs, workflow runs and commit statuses on public repositories (from
// 1 October 2026) deletes the older ones.
//
// For each repository it walks the default branch's history between -since
// and -until with the GraphQL API and writes one JSON line per commit:
// commit, date, PR number (from the "(#N)" in the headline), and
// statusCheckRollup.state. It is resumable (a cursor file per repository),
// rate-limit aware (it sleeps until the quota resets), shrinks the page size
// when a large history times out, and walks the largest repositories first.
//
//	go run . -repos repos.txt -out ~/proyecto/riffle-data/ci-snapshot/pass1
//
// The token comes from GITHUB_TOKEN, or from `gh auth token`.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	tea "github.com/charmbracelet/bubbletea"
)

const query = `query($owner:String!,$name:String!,$since:GitTimestamp!,$until:GitTimestamp!,$after:String,$n:Int!){
  rateLimit{cost remaining resetAt}
  repository(owner:$owner,name:$name){defaultBranchRef{name target{... on Commit{
    history(first:$n,since:$since,until:$until,after:$after){totalCount pageInfo{hasNextPage endCursor}
      nodes{oid committedDate messageHeadline statusCheckRollup{state}}}}}}}}`

// The PR number a merge put in the commit headline: squash and rebase merges
// end with "(#123)"; merge commits start "Merge pull request #123 from".
var prNumber = regexp.MustCompile(`\(#(\d+)\)\s*$|^Merge pull request #(\d+)`)

type config struct {
	repos, out, since, until, token, mode, from string
	sample                                      int
	shardI, shardN                              int
	workers                                     int
	plain                                       bool
}

// Line is one commit in the output: the combined CI result as GitHub
// reported it on the capture date.
type Line struct {
	Repo        string  `json:"repo"`
	Branch      string  `json:"branch"`
	OID         string  `json:"oid"`
	CommittedAt string  `json:"committed_at"`
	PR          *int    `json:"pr"`
	Headline    string  `json:"headline"`
	CIState     *string `json:"ci_state"`
	CapturedAt  string  `json:"captured_at"`
}

type page struct {
	Branch  string
	Total   int
	Next    string
	HasNext bool
	Lines   []Line
	Missing bool
}

// GraphQL response shapes.
type branchRef struct {
	Name   string
	Target struct {
		History struct {
			TotalCount int
			PageInfo   struct {
				HasNextPage bool
				EndCursor   string
			}
			Nodes []struct {
				Oid               string
				CommittedDate     string
				MessageHeadline   string
				StatusCheckRollup *struct{ State string }
			}
		}
	}
}

type response struct {
	Data struct {
		RateLimit struct {
			Remaining int
			ResetAt   time.Time
		}
		Repository *struct{ DefaultBranchRef *branchRef }
	}
	Errors []struct{ Message, Type string }
}

// --- GitHub client, shared by the workers ---------------------------------

type client struct {
	cfg   config
	http  *http.Client
	mu    sync.Mutex
	rem   int
	reset time.Time
	// REST has its own quota.
	restRem   int
	restReset time.Time
	send      func(tea.Msg)
}

var errTooHeavy = errors.New("query too heavy")

func (c *client) waitForQuota() {
	c.mu.Lock()
	rem, reset := c.rem, c.reset
	c.mu.Unlock()
	if rem >= 100 || reset.IsZero() {
		return
	}
	wait := time.Until(reset) + 5*time.Second
	if wait > 0 {
		c.send(pausedMsg{until: reset.Add(5 * time.Second)})
		time.Sleep(wait)
		c.send(pausedMsg{})
	}
}

func (c *client) fetch(repo, after string, n int) (*page, error) {
	owner, name, _ := strings.Cut(repo, "/")
	vars := map[string]any{"owner": owner, "name": name, "since": c.cfg.since, "until": c.cfg.until, "n": n}
	if after != "" {
		vars["after"] = after
	}
	body, _ := json.Marshal(map[string]any{"query": query, "variables": vars})

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
			return nil, errTooHeavy
		case res.StatusCode == 403 || res.StatusCode == 429:
			// Secondary rate limit: honour Retry-After, else back off.
			wait := time.Minute * time.Duration(attempt+1)
			if s := res.Header.Get("Retry-After"); s != "" {
				var secs int
				fmt.Sscan(s, &secs)
				wait = time.Duration(secs+2) * time.Second
			}
			c.send(eventMsg(fmt.Sprintf("%s: secondary limit, waiting %s", repo, wait.Round(time.Second))))
			time.Sleep(wait)
			continue
		case res.StatusCode >= 500:
			lastErr = fmt.Errorf("http %d", res.StatusCode)
			time.Sleep(time.Duration(15*(attempt+1)) * time.Second)
			continue
		case res.StatusCode != 200:
			return nil, fmt.Errorf("http %d: %s", res.StatusCode, strings.TrimSpace(string(raw))[:min(200, len(raw))])
		}

		var d response
		if err := json.Unmarshal(raw, &d); err != nil {
			lastErr = err
			continue
		}
		if len(d.Errors) > 0 {
			msg := d.Errors[0].Type + " " + d.Errors[0].Message
			if strings.Contains(strings.ToLower(msg), "rate") {
				c.send(eventMsg(repo + ": rate limited, waiting 2m"))
				time.Sleep(2 * time.Minute)
				continue
			}
			if strings.Contains(strings.ToLower(msg), "timeout") || strings.Contains(strings.ToLower(msg), "went wrong") {
				return nil, errTooHeavy
			}
			return nil, errors.New(msg)
		}

		c.mu.Lock()
		c.rem, c.reset = d.Data.RateLimit.Remaining, d.Data.RateLimit.ResetAt
		c.mu.Unlock()
		c.send(quotaMsg{remaining: d.Data.RateLimit.Remaining, reset: d.Data.RateLimit.ResetAt})

		var ref *branchRef
		if d.Data.Repository != nil {
			ref = d.Data.Repository.DefaultBranchRef
		}
		if ref == nil {
			return &page{Missing: true}, nil
		}
		h := ref.Target.History
		now := time.Now().UTC().Format(time.RFC3339)
		p := &page{Branch: ref.Name, Total: h.TotalCount, Next: h.PageInfo.EndCursor, HasNext: h.PageInfo.HasNextPage}
		for _, node := range h.Nodes {
			l := Line{Repo: repo, Branch: ref.Name, OID: node.Oid, CommittedAt: node.CommittedDate, Headline: node.MessageHeadline, CapturedAt: now}
			if m := prNumber.FindStringSubmatch(node.MessageHeadline); m != nil {
				var n int
				fmt.Sscan(m[1]+m[2], &n)
				l.PR = &n
			}
			if node.StatusCheckRollup != nil {
				s := node.StatusCheckRollup.State
				l.CIState = &s
			}
			p.Lines = append(p.Lines, l)
		}
		return p, nil
	}
	return nil, fmt.Errorf("gave up: %v", lastErr)
}

// fetchAdaptive tries 100 commits a page, then smaller pages for histories
// heavy enough to time out.
func (c *client) fetchAdaptive(repo, after string) (*page, int, error) {
	for _, n := range []int{100, 50, 25, 10} {
		p, err := c.fetch(repo, after, n)
		if errors.Is(err, errTooHeavy) {
			c.send(eventMsg(fmt.Sprintf("%s: timed out at %d a page, trying smaller", repo, n)))
			time.Sleep(3 * time.Second)
			continue
		}
		return p, n, err
	}
	return nil, 0, fmt.Errorf("times out even at 10 commits a page")
}

// --- One repository ---------------------------------------------------------

func countLines(path string) int {
	f, err := os.Open(path)
	if err != nil {
		return 0
	}
	defer f.Close()
	n := 0
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		n++
	}
	return n
}

func (c *client) run(worker int, repo string) {
	base := filepath.Join(c.cfg.out, strings.ReplaceAll(repo, "/", "__"))
	if _, err := os.Stat(base + ".done"); err == nil {
		c.send(repoDoneMsg{repo: repo, commits: countLines(base + ".jsonl"), skipped: true})
		return
	}
	after := ""
	if b, err := os.ReadFile(base + ".cursor"); err == nil {
		after = strings.TrimSpace(string(b))
	}
	done := 0
	if after != "" {
		done = countLines(base + ".jsonl")
	} else {
		os.Remove(base + ".jsonl") // no cursor: start the file clean
	}
	f, err := os.OpenFile(base+".jsonl", os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		c.send(repoDoneMsg{repo: repo, err: err})
		return
	}
	defer f.Close()
	enc := json.NewEncoder(f)

	for {
		p, n, err := c.fetchAdaptive(repo, after)
		if err != nil {
			c.send(repoDoneMsg{repo: repo, commits: done, err: err})
			return
		}
		if p.Missing {
			c.send(repoDoneMsg{repo: repo, err: errors.New("repository or default branch not found")})
			return
		}
		for _, l := range p.Lines {
			enc.Encode(l)
		}
		f.Sync()
		done += len(p.Lines)
		c.send(progressMsg{worker: worker, repo: repo, done: done, total: p.Total, pageSize: n, added: len(p.Lines)})
		if !p.HasNext {
			break
		}
		after = p.Next
		os.WriteFile(base+".cursor", []byte(after), 0o644)
	}
	os.WriteFile(base+".done", []byte(time.Now().UTC().Format(time.RFC3339)+"\n"), 0o644)
	c.send(repoDoneMsg{repo: repo, commits: done})
}

// totals asks each repository for its commit count in the window (1 point
// each), for the progress bars and to walk the largest first.
func (c *client) totals(repos []string) map[string]int {
	out := map[string]int{}
	var mu sync.Mutex
	var wg sync.WaitGroup
	sem := make(chan struct{}, 8)
	for _, r := range repos {
		wg.Add(1)
		go func(r string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			p, err := c.fetch(r, "", 1)
			mu.Lock()
			defer mu.Unlock()
			if err == nil && !p.Missing {
				out[r] = p.Total
			}
		}(r)
	}
	wg.Wait()
	return out
}

// --- main -------------------------------------------------------------------

func main() {
	home, _ := os.UserHomeDir()
	cfg := config{}
	flag.StringVar(&cfg.repos, "repos", "repos.txt", "file with one owner/name per line")
	flag.StringVar(&cfg.mode, "mode", "commits", "commits (default-branch CI per commit), prs (CI on each PR's last commit), runs (Actions runs on default-branch pushes, REST), or checks (per-check results for sampled failed commits, REST)")
	flag.IntVar(&cfg.sample, "sample", 50, "checks mode: failed commits sampled per repository")
	flag.StringVar(&cfg.from, "from", "", "checks mode: the commits mode's output directory (default ~/proyecto/riffle-data/ci-snapshot/pass1)")
	flag.StringVar(&cfg.out, "out", "", "output directory (default ~/proyecto/riffle-data/ci-snapshot/<pass1|prs|runs>)")
	shard := flag.String("shard", "1/1", "i/n: take every n-th repository, starting at i, to split the work across machines")
	flag.StringVar(&cfg.since, "since", "2025-08-26T00:00:00Z", "oldest commit date to capture")
	flag.StringVar(&cfg.until, "until", "2026-07-02T00:00:00Z", "newest commit date to capture (pass 1: older than 90 days)")
	flag.IntVar(&cfg.workers, "workers", 4, "repositories walked at once")
	flag.BoolVar(&cfg.plain, "plain", false, "plain log output instead of the progress screen")
	flag.Parse()
	if _, err := fmt.Sscanf(*shard, "%d/%d", &cfg.shardI, &cfg.shardN); err != nil || cfg.shardN < 1 || cfg.shardI < 1 || cfg.shardI > cfg.shardN {
		fmt.Fprintln(os.Stderr, "-shard must look like 1/2")
		os.Exit(1)
	}
	if cfg.mode != "commits" && cfg.mode != "prs" && cfg.mode != "runs" && cfg.mode != "checks" {
		fmt.Fprintln(os.Stderr, "-mode must be commits, prs, runs or checks")
		os.Exit(1)
	}
	if cfg.out == "" {
		dir := map[string]string{"commits": "pass1", "prs": "prs", "runs": "runs", "checks": "checks"}[cfg.mode]
		cfg.out = filepath.Join(home, "proyecto", "riffle-data", "ci-snapshot", dir)
	}
	if cfg.from == "" {
		cfg.from = filepath.Join(home, "proyecto", "riffle-data", "ci-snapshot", "pass1")
	}

	cfg.token = os.Getenv("GITHUB_TOKEN")
	if cfg.token == "" {
		if b, err := exec.Command("gh", "auth", "token").Output(); err == nil {
			cfg.token = strings.TrimSpace(string(b))
		}
	}
	if cfg.token == "" {
		fmt.Fprintln(os.Stderr, "no token: set GITHUB_TOKEN or log in with `gh auth login`")
		os.Exit(1)
	}
	raw, err := os.ReadFile(cfg.repos)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	var repos []string
	for _, l := range strings.Split(string(raw), "\n") {
		if l = strings.TrimSpace(l); l != "" && !strings.HasPrefix(l, "#") {
			repos = append(repos, l)
		}
	}
	if err := os.MkdirAll(cfg.out, 0o755); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	logf, _ := os.OpenFile(filepath.Join(cfg.out, "run.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	defer logf.Close()
	logLine := func(s string) { fmt.Fprintf(logf, "%s %s\n", time.Now().Format("15:04:05"), s) }

	m := newModel(cfg, len(repos))
	var prog *tea.Program
	c := &client{cfg: cfg, http: &http.Client{Timeout: 120 * time.Second}, restRem: -1}
	if cfg.plain {
		c.send = func(msg tea.Msg) {
			if s := describe(msg); s != "" {
				fmt.Println(time.Now().Format("15:04:05"), s)
				logLine(s)
			}
			if _, ok := msg.(finishedMsg); ok {
				os.Exit(0)
			}
		}
	} else {
		prog = tea.NewProgram(m)
		c.send = func(msg tea.Msg) {
			if s := describe(msg); s != "" {
				logLine(s)
			}
			prog.Send(msg)
		}
	}

	go func() {
		c.send(eventMsg(fmt.Sprintf("mode %s, shard %d/%d: sizing %d repositories, %s to %s", cfg.mode, cfg.shardI, cfg.shardN, len(repos), cfg.since[:10], cfg.until[:10])))
		tot := c.totals(repos)
		sum := 0
		for _, n := range tot {
			sum += n
		}
		sort.SliceStable(repos, func(i, j int) bool { return tot[repos[i]] > tot[repos[j]] })
		// Sharding after sorting deals the largest repositories out evenly.
		if cfg.shardN > 1 {
			var mine []string
			sum = 0
			for i, r := range repos {
				if i%cfg.shardN == cfg.shardI-1 {
					mine = append(mine, r)
					sum += tot[r]
				}
			}
			repos = mine
		}
		if cfg.mode != "commits" {
			sum = 0 // the commit count is no measure of PRs or runs
		}
		if cfg.mode == "runs" {
			// Runs vary wildly (pytorch has ~2,000 a day): smallest first, so
			// the most repositories are covered in the time there is.
			for i, j := 0, len(repos)-1; i < j; i, j = i+1, j-1 {
				repos[i], repos[j] = repos[j], repos[i]
			}
		}
		c.send(totalsMsg{perRepo: tot, total: sum, repos: len(repos)})

		jobs := make(chan string)
		var wg sync.WaitGroup
		for w := 0; w < cfg.workers; w++ {
			wg.Add(1)
			go func(w int) {
				defer wg.Done()
				for r := range jobs {
					c.send(startMsg{worker: w, repo: r})
					switch cfg.mode {
					case "prs":
						c.runPRs(w, r)
					case "runs":
						c.runRuns(w, r)
					case "checks":
						c.runChecks(w, r)
					default:
						c.run(w, r)
					}
				}
			}(w)
		}
		if cfg.mode == "checks" {
			// Hand out repositories as the commits run finishes them, in
			// whatever order that is, instead of queueing behind the giants.
			pending := append([]string(nil), repos...)
			for len(pending) > 0 {
				var rest []string
				for _, r := range pending {
					_, done := os.Stat(filepath.Join(cfg.from, strings.ReplaceAll(r, "/", "__")+".done"))
					if done == nil {
						jobs <- r
					} else {
						rest = append(rest, r)
					}
				}
				if pending = rest; len(pending) > 0 {
					time.Sleep(20 * time.Second)
				}
			}
		} else {
			for _, r := range repos {
				jobs <- r
			}
		}
		close(jobs)
		wg.Wait()
		c.send(finishedMsg{})
	}()

	if cfg.plain {
		select {} // the finishedMsg handler exits
	}
	if _, err := prog.Run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
