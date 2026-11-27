// Package webhook checks that a delivery really comes from GitHub.
package webhook

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strings"
)

// ErrBadSignature covers every way a signature can fail; the caller answers 401 and the
// reason is only logged, so a caller learns nothing about which check failed.
var ErrBadSignature = errors.New("webhook signature does not match")

const prefix = "sha256="

// Verify checks the X-Hub-Signature-256 header: "sha256=" and the hex HMAC-SHA256 of the
// exact request body, keyed with the App's webhook secret. The comparison takes the same
// time whatever the input, so the secret cannot be guessed byte by byte.
func Verify(secret []byte, body []byte, header string) error {
	if len(secret) == 0 {
		return errors.New("webhook secret is empty")
	}
	if !strings.HasPrefix(header, prefix) {
		return ErrBadSignature
	}
	got, err := hex.DecodeString(strings.TrimPrefix(header, prefix))
	if err != nil || len(got) != sha256.Size {
		return ErrBadSignature
	}
	mac := hmac.New(sha256.New, secret)
	mac.Write(body)
	if !hmac.Equal(got, mac.Sum(nil)) {
		return ErrBadSignature
	}
	return nil
}

// Sign returns the header value GitHub would send for body. For tests and for sending test
// webhooks without the GitHub App.
func Sign(secret []byte, body []byte) string {
	mac := hmac.New(sha256.New, secret)
	mac.Write(body)
	return prefix + hex.EncodeToString(mac.Sum(nil))
}
