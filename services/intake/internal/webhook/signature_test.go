package webhook

import (
	"errors"
	"strings"
	"testing"
)

func TestVerify(t *testing.T) {
	secret := []byte("test-secret")
	body := []byte(`{"action":"opened"}`)
	good := Sign(secret, body)

	cases := []struct {
		name   string
		secret []byte
		body   []byte
		header string
		ok     bool
	}{
		{"valid", secret, body, good, true},
		{"wrong secret", []byte("other"), body, good, false},
		{"body changed", secret, []byte(`{"action":"closed"}`), good, false},
		{"missing header", secret, body, "", false},
		{"sha1 header", secret, body, "sha1=" + strings.TrimPrefix(good, "sha256="), false},
		{"not hex", secret, body, "sha256=zz", false},
		{"too short", secret, body, good[:len(good)-2], false},
		{"upper-case hex", secret, body, "sha256=" + strings.ToUpper(strings.TrimPrefix(good, "sha256=")), true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			err := Verify(c.secret, c.body, c.header)
			if c.ok && err != nil {
				t.Fatalf("want valid, got %v", err)
			}
			if !c.ok && !errors.Is(err, ErrBadSignature) {
				t.Fatalf("want ErrBadSignature, got %v", err)
			}
		})
	}
}

func TestVerifyRefusesAnEmptySecret(t *testing.T) {
	// An empty key would make any request signed with an empty key pass.
	if err := Verify(nil, []byte("x"), Sign(nil, []byte("x"))); err == nil {
		t.Fatal("an empty secret must never verify")
	}
}
