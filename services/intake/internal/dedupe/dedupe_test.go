package dedupe

import (
	"context"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

func newStore(t *testing.T) (*Store, *miniredis.Miniredis) {
	t.Helper()
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { rdb.Close() })
	return New(rdb, 5*time.Minute, 72*time.Hour), mr
}

func claim(t *testing.T, s *Store, id string) Outcome {
	t.Helper()
	o, err := s.Claim(context.Background(), id)
	if err != nil {
		t.Fatal(err)
	}
	return o
}

func TestAFirstClaimWinsAndASecondSeesItInFlight(t *testing.T) {
	s, _ := newStore(t)
	if got := claim(t, s, "d1"); got != Claimed {
		t.Fatalf("first claim: %v", got)
	}
	if got := claim(t, s, "d1"); got != InFlight {
		t.Fatalf("second claim while publishing: %v", got)
	}
}

func TestADoneDeliveryIsADuplicateForThreeDays(t *testing.T) {
	s, mr := newStore(t)
	ctx := context.Background()
	claim(t, s, "d1")
	if err := s.Done(ctx, "d1"); err != nil {
		t.Fatal(err)
	}
	if got := claim(t, s, "d1"); got != Duplicate {
		t.Fatalf("after done: %v", got)
	}
	mr.FastForward(71 * time.Hour)
	if got := claim(t, s, "d1"); got != Duplicate {
		t.Fatalf("after 71 h: %v", got)
	}
	mr.FastForward(2 * time.Hour)
	if got := claim(t, s, "d1"); got != Claimed {
		t.Fatalf("after 73 h the mark has expired: %v", got)
	}
}

func TestReleaseLetsARedeliveryThrough(t *testing.T) {
	s, _ := newStore(t)
	claim(t, s, "d1")
	if err := s.Release(context.Background(), "d1"); err != nil {
		t.Fatal(err)
	}
	if got := claim(t, s, "d1"); got != Claimed {
		t.Fatalf("after release: %v", got)
	}
}

func TestReleaseNeverRemovesADoneMark(t *testing.T) {
	s, _ := newStore(t)
	ctx := context.Background()
	claim(t, s, "d1")
	_ = s.Done(ctx, "d1")
	_ = s.Release(ctx, "d1")
	if got := claim(t, s, "d1"); got != Duplicate {
		t.Fatalf("a late release removed the done mark: %v", got)
	}
}

func TestACrashedClaimExpiresOnItsOwn(t *testing.T) {
	s, mr := newStore(t)
	claim(t, s, "d1")
	mr.FastForward(6 * time.Minute)
	if got := claim(t, s, "d1"); got != Claimed {
		t.Fatalf("a stale pending claim blocked the delivery: %v", got)
	}
}

func TestRedisDownIsAnErrorNotADuplicate(t *testing.T) {
	s, mr := newStore(t)
	mr.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	o, err := s.Claim(ctx, "d1")
	if err == nil || o != Claimed {
		t.Fatalf("want an error and Claimed (publish anyway), got %v, %v", o, err)
	}
}
