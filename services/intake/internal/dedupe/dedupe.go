// Package dedupe makes sure one GitHub delivery is published once.
//
// A delivery is claimed before publishing and marked done after. The claim expires on its
// own, so a crash between the two never blocks a redelivery for long; a failed publish
// releases it at once, so GitHub's redelivery is published, not dropped as a duplicate.
package dedupe

import (
	"context"
	"time"

	"github.com/redis/go-redis/v9"
)

// Outcome of a claim.
type Outcome int

const (
	// Claimed: this request owns the delivery and should publish it.
	Claimed Outcome = iota
	// Duplicate: the delivery was already published.
	Duplicate
	// InFlight: another request is publishing it right now.
	InFlight
)

func (o Outcome) String() string {
	return [...]string{"claimed", "duplicate", "in_flight"}[o]
}

const (
	pending = "pending"
	done    = "done"
)

// release deletes the key only while it is still this pending claim, so it never removes a
// "done" mark written meanwhile.
var release = redis.NewScript(`if redis.call("GET", KEYS[1]) == ARGV[1] then return redis.call("DEL", KEYS[1]) end return 0`)

// Store keeps claims in Redis.
type Store struct {
	rdb        redis.UniversalClient
	pendingTTL time.Duration
	doneTTL    time.Duration
}

// New returns a Store. pendingTTL bounds how long a crashed request blocks its delivery;
// doneTTL is how long a published delivery is remembered (GitHub redelivers for days).
func New(rdb redis.UniversalClient, pendingTTL, doneTTL time.Duration) *Store {
	return &Store{rdb: rdb, pendingTTL: pendingTTL, doneTTL: doneTTL}
}

func key(delivery string) string { return "riffle:intake:delivery:" + delivery }

// Claim tries to take the delivery. An error means Redis could not answer: the caller
// publishes anyway (the scorer is idempotent on delivery_id too), because dropping a PR is
// worse than scoring it twice.
func (s *Store) Claim(ctx context.Context, delivery string) (Outcome, error) {
	ok, err := s.rdb.SetNX(ctx, key(delivery), pending, s.pendingTTL).Result()
	if err != nil {
		return Claimed, err
	}
	if ok {
		return Claimed, nil
	}
	state, err := s.rdb.Get(ctx, key(delivery)).Result()
	switch {
	case err == redis.Nil:
		// Expired between the two calls: take it.
		return s.Claim(ctx, delivery)
	case err != nil:
		return Claimed, err
	case state == done:
		return Duplicate, nil
	default:
		return InFlight, nil
	}
}

// Done marks the delivery published.
func (s *Store) Done(ctx context.Context, delivery string) error {
	return s.rdb.Set(ctx, key(delivery), done, s.doneTTL).Err()
}

// Release gives the claim back after a failed publish.
func (s *Store) Release(ctx context.Context, delivery string) error {
	return release.Run(ctx, s.rdb, []string{key(delivery)}, pending).Err()
}

// Ping reports whether Redis answers.
func (s *Store) Ping(ctx context.Context) error {
	return s.rdb.Ping(ctx).Err()
}
