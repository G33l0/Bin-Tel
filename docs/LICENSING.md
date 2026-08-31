# Licensing

A licence in Bin-Tel is a **signed token**, not a flag in a file. Editing the
stored row, or the token, cannot promote a plan — the signature decides, and the
client holds only a public key.

---

## The pieces

| Component | Responsibility |
|---|---|
| `LicenseManager` | Reads the stored licence, derives its state, and owns activate / revalidate / deactivate |
| `ActivationService` | Talks to a licensing client; the only thing that goes to the network |
| `LicenseClient` | The interface. `HttpLicenseClient` for production, `LocalLicenseServer` for development |
| `DeviceManager` | This installation's identity |
| `EntitlementService` | Answers "may I?" — see [MONETIZATION.md](MONETIZATION.md) |

---

## The token

```
bintel.<base64url(payload)>.<base64url(ed25519 signature)>
```

The payload carries `license_id`, `plan`, `status`, `subject`, `issued_at`,
`expires_at`, `device_id`, `device_limit`, optional explicit `features` and
`limits`, `grace_days` and `edition`.

`LicenseToken.parse(token, verifying_key)` returns `None` — never a partially
trusted object — when the token is malformed, the signature does not verify, or
the payload does not validate. Nothing downstream can act on an unverified
payload because there is no unverified payload to act on.

Signing is Ed25519 (RFC 8032), implemented in `app/licensing/signing.py` and
checked against the specification's own test vectors. The desktop application
holds the **public** key only; it can verify a licence and can never mint one.

Explicit `features` and `limits` in a signed payload *replace* the plan matrix.
That is how a bespoke enterprise entitlement is granted without shipping a new
build.

---

## States

`LicenseManager._derive()` is a pure function of the verified payload, the last
successful validation time, and this device's identity.

| State | When | Entitlements |
|---|---|---|
| `NOT_ACTIVATED` / `FREE` | No licence stored | Free plan |
| `PRO` / `BUSINESS` / `ENTERPRISE` | Verified, current, this device | That plan |
| `OFFLINE_GRACE` | Not revalidated recently, but inside the grace window | **That plan** — an outage must not cost you what you paid for |
| `EXPIRED` | Past `expires_at`, or past the grace window | Free plan |
| `SUSPENDED` | Status is suspended, revoked or cancelled | Free plan |
| `INVALID` | Issued for a different device, or the token does not verify | Free plan |

Falling back always means **Free**, never broken. Your database, your lookups,
your saved work and your exports are unaffected; the paid surfaces show an
upgrade prompt and the rest of the application carries on.

`snapshot.plan` is the plan the licence names. `LicenseManager.plan` is the plan
whose entitlements actually apply. The interface only ever asks the entitlement
service, which reads the latter.

---

## Offline grace

`grace_days` (14 by default) starts from the last *successful* validation. Past
half the window the state becomes `OFFLINE_GRACE` and the licence page says so
plainly, with the date the plan would lapse. Past the whole window the plan
falls back to Free.

Revalidation failure is not licence failure: `OfflineError` and `NetworkError`
leave the stored state standing. Only an explicit rejection from the service
suspends a licence.

---

## Devices

`DeviceManager.device_id` is `sha256(APP_ID + install_id)` truncated to 32
characters, where `install_id` is a random UUID4 generated on first run and
resettable by the user.

This is deliberately **not** a hardware fingerprint. No MAC address, no serial
number, no disk id, nothing that identifies the machine itself or that could be
correlated with this user across unrelated software. `device_name` defaults to
the hostname purely so a person can tell their own devices apart in a list, and
it is editable.

Device limits come from the plan (Free 1, Pro 3, Business 10, Enterprise
unlimited) and are enforced *server-side* — the client cannot grant itself a
seat. Activating past the limit raises `DeviceLimitReached` with a message that
says what to do about it.

---

## Configuration

| Setting | Meaning |
|---|---|
| `license.service_mode` | `production` (HTTP) or `development` (local signing server) |
| `license.api_url` | Licensing service base URL; also `BINTEL_LICENSE_API` |
| `license.verifying_key` | Base64 Ed25519 public key; falls back to the bundled one |
| `license.revalidate_on_startup` | Re-check shortly after launch |

### Development mode

`LocalLicenseServer` is a real signing service that runs on this machine. It
generates its own key pair beside the application data, issues genuinely signed
licences and enforces the same device limits — so the whole flow can be
developed and tested without a hosted backend. It is never used unless
explicitly selected.

Its demo keys are `BINTEL-DEV-PRO`, `BINTEL-DEV-BUSINESS` and
`BINTEL-DEV-ENTERPRISE`.

---

## What is never stored

No payment-card details, no billing credentials, no payment tokens. The desktop
application does not process payments and holds nothing that could be used to
make one — see [MONETIZATION.md](MONETIZATION.md) for where that boundary sits.
The licence key itself is displayed masked (`BINT••••••••NESS`).
