package main

import (
	"fmt"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/progress"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// Messages from the workers to the screen.
type (
	totalsMsg struct {
		perRepo      map[string]int
		total, repos int
	}
	startMsg struct {
		worker int
		repo   string
	}
	progressMsg struct {
		worker, done, total, pageSize, added, records int
		repo                                          string
	}
	repoDoneMsg struct {
		repo    string
		commits int
		skipped bool
		err     error
	}
	quotaMsg struct {
		remaining int
		reset     time.Time
	}
	pausedMsg   struct{ until time.Time }
	eventMsg    string
	finishedMsg struct{}
	tickMsg     time.Time
)

// describe turns a message into a log line (and the -plain output).
func describe(msg tea.Msg) string {
	switch m := msg.(type) {
	case totalsMsg:
		if m.total == 0 {
			return fmt.Sprintf("%d repositories to walk", m.repos)
		}
		return fmt.Sprintf("%d commits to capture across %d repositories", m.total, m.repos)
	case repoDoneMsg:
		switch {
		case m.err != nil:
			return fmt.Sprintf("FAILED %s after %d commits: %v", m.repo, m.commits, m.err)
		case m.skipped:
			return fmt.Sprintf("skip %s (already done, %d commits)", m.repo, m.commits)
		default:
			return fmt.Sprintf("done %s: %d records", m.repo, m.commits)
		}
	case pausedMsg:
		if m.until.IsZero() {
			return "quota reset, resuming"
		}
		return "quota nearly used, pausing until " + m.until.Local().Format("15:04:05")
	case eventMsg:
		return string(m)
	case finishedMsg:
		return "all repositories finished"
	}
	return ""
}

type worker struct {
	repo              string
	done, total, page int
}

type model struct {
	cfg              config
	repos, reposDone int
	failed           []string
	perRepo          map[string]int
	records          map[string]int
	total, captured  int
	workers          map[int]*worker
	remaining        int
	reset, paused    time.Time
	start            time.Time
	events           []string
	finished         bool
	width            int
	bar              progress.Model
}

func newModel(cfg config, repos int) *model {
	return &model{
		cfg: cfg, repos: repos, workers: map[int]*worker{}, records: map[string]int{}, remaining: -1, start: time.Now(), width: 100,
		bar: progress.New(progress.WithGradient("#0f7a4f", "#7dd3a0"), progress.WithoutPercentage()),
	}
}

func tick() tea.Cmd { return tea.Tick(time.Second, func(t time.Time) tea.Msg { return tickMsg(t) }) }

func (m *model) Init() tea.Cmd { return tick() }

func (m *model) event(s string) {
	m.events = append(m.events, time.Now().Format("15:04:05")+"  "+s)
	if len(m.events) > 7 {
		m.events = m.events[len(m.events)-7:]
	}
}

func (m *model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch v := msg.(type) {
	case tea.KeyMsg:
		if v.String() == "q" || v.String() == "ctrl+c" {
			return m, tea.Quit // cursors are on disk; rerun to resume
		}
	case tea.WindowSizeMsg:
		m.width = v.Width
	case tickMsg:
		return m, tick()
	case totalsMsg:
		m.perRepo, m.total, m.repos = v.perRepo, v.total, v.repos
		m.event(describe(v))
	case startMsg:
		total := m.perRepo[v.repo]
		if m.cfg.mode != "commits" {
			total = 1000 // share of the date window, in 1/1000s
		}
		m.workers[v.worker] = &worker{repo: v.repo, total: total}
	case progressMsg:
		w := m.workers[v.worker]
		if w == nil {
			w = &worker{}
			m.workers[v.worker] = w
		}
		w.repo, w.done, w.page = v.repo, v.done, v.pageSize
		if v.total > 0 {
			w.total = v.total
		}
		m.captured += v.added
		if v.records > 0 {
			m.records[v.repo] = v.records
		}
	case repoDoneMsg:
		m.reposDone++
		if v.skipped {
			if m.cfg.mode == "commits" {
				m.captured += v.commits
			} else {
				m.records[v.repo] = v.commits
			}
		}
		if v.err != nil {
			m.failed = append(m.failed, v.repo)
		}
		m.event(describe(v))
	case quotaMsg:
		m.remaining, m.reset = v.remaining, v.reset
	case pausedMsg:
		m.paused = v.until
		m.event(describe(v))
	case eventMsg:
		m.event(string(v))
	case finishedMsg:
		m.finished = true
		m.event(describe(v))
		return m, tea.Quit
	}
	return m, nil
}

var (
	title = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("#f2f1f4")).Background(lipgloss.Color("#0f7a4f")).Padding(0, 1)
	dim   = lipgloss.NewStyle().Foreground(lipgloss.Color("#867e8e"))
	bold  = lipgloss.NewStyle().Bold(true)
	warn  = lipgloss.NewStyle().Foreground(lipgloss.Color("#d97706")).Bold(true)
	bad   = lipgloss.NewStyle().Foreground(lipgloss.Color("#dc2626")).Bold(true)
	good  = lipgloss.NewStyle().Foreground(lipgloss.Color("#16a34a")).Bold(true)
)

func dur(d time.Duration) string {
	d = d.Round(time.Second)
	if d >= time.Hour {
		return fmt.Sprintf("%dh%02dm", int(d.Hours()), int(d.Minutes())%60)
	}
	return fmt.Sprintf("%dm%02ds", int(d.Minutes()), int(d.Seconds())%60)
}

func (m *model) View() string {
	var b strings.Builder
	barW := min(max(m.width-30, 20), 70)
	m.bar.Width = barW

	b.WriteString(title.Render("Riffle CI snapshot · "+m.cfg.mode) + "  " + dim.Render(fmt.Sprintf("shard %d/%d  ·  %s → %s  ·  %s", m.cfg.shardI, m.cfg.shardN, m.cfg.since[:10], m.cfg.until[:10], m.cfg.out)) + "\n\n")

	// Overall
	captured := m.captured
	for _, n := range m.records {
		captured += n
	}
	frac := 0.0
	if m.total > 0 {
		frac = min(1, float64(m.captured)/float64(m.total))
	} else if m.repos > 0 {
		frac = float64(m.reposDone) / float64(m.repos)
	}
	b.WriteString(m.bar.ViewAs(frac) + fmt.Sprintf("  %s %5.1f%%\n", bold.Render(""), frac*100))
	elapsed := time.Since(m.start)
	eta := "…"
	if frac > 0.01 && frac < 1 {
		eta = dur(time.Duration(elapsed.Seconds()/frac*(1-frac)) * time.Second)
	}
	totalS := "counting…"
	if m.total > 0 {
		totalS = fmt.Sprintf("%d", m.total)
	} else if m.perRepo != nil {
		totalS = "?"
	}
	unit := map[string]string{"commits": "commits", "prs": "PRs", "runs": "runs"}[m.cfg.mode]
	b.WriteString(fmt.Sprintf("%s %s  %s / %s    %s repos  %d / %d    %s %s    %s %s\n",
		dim.Render("captured"), unit, bold.Render(fmt.Sprint(captured)), totalS,
		dim.Render("·"), m.reposDone, m.repos,
		dim.Render("elapsed"), dur(elapsed), dim.Render("eta"), eta))

	// Quota
	q := "quota  …"
	if m.remaining >= 0 {
		style := good
		if m.remaining < 1000 {
			style = warn
		}
		api := "GraphQL"
		if m.cfg.mode == "runs" {
			api = "REST"
		}
		q = fmt.Sprintf("%s %s  %s / 5000   %s %s", dim.Render("quota"), api, style.Render(fmt.Sprint(m.remaining)), dim.Render("resets in"), dur(time.Until(m.reset)))
	}
	if !m.paused.IsZero() && time.Now().Before(m.paused) {
		q += "   " + warn.Render("PAUSED until "+m.paused.Local().Format("15:04:05")+" ("+dur(time.Until(m.paused))+")")
	}
	b.WriteString(q + "\n")
	if len(m.failed) > 0 {
		b.WriteString(bad.Render(fmt.Sprintf("failed %d: %s", len(m.failed), strings.Join(m.failed, ", "))) + "\n")
	}
	b.WriteString("\n")

	// Workers
	ids := make([]int, 0, len(m.workers))
	for id := range m.workers {
		ids = append(ids, id)
	}
	sort.Ints(ids)
	small := progress.New(progress.WithSolidFill("#7dd3a0"), progress.WithoutPercentage(), progress.WithWidth(24))
	for _, id := range ids {
		w := m.workers[id]
		f := 0.0
		if w.total > 0 {
			f = min(1, float64(w.done)/float64(w.total))
		}
		if m.cfg.mode == "commits" {
			b.WriteString(fmt.Sprintf("  %-40s %s  %7d / %-7d %s\n", truncate(w.repo, 40), small.ViewAs(f), w.done, w.total, dim.Render(fmt.Sprintf("%d/page", w.page))))
		} else {
			b.WriteString(fmt.Sprintf("  %-40s %s  %5.1f%% of window  %7d %s\n", truncate(w.repo, 40), small.ViewAs(f), f*100, m.records[w.repo], dim.Render(unit)))
		}
	}

	// Events
	b.WriteString("\n")
	for _, e := range m.events {
		b.WriteString(dim.Render("  "+e) + "\n")
	}
	if m.finished {
		b.WriteString("\n" + good.Render("Finished.") + " " + dim.Render("Output in "+m.cfg.out) + "\n")
	} else {
		b.WriteString("\n" + dim.Render("q to stop  ·  progress is saved; rerun the same command to resume") + "\n")
	}
	return b.String()
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n-1] + "…"
}

var _ = os.Stdout
