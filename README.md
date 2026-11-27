# GridGPU device registration

The contributor software runs on Windows/Linux GPU PCs. Firebase Cloud Functions hosts the private registration backend. This checkout is a development workspace: no live Firebase enrollment, actual email delivery, or contributor installation has been performed here.

## Agreed order

1. A participant runs the installer wizard. It auto-detects the GPU, asks whether they want to join, and collects their name, email, availability and location. On submit, the backend immediately emails a 15-minute, single-use **6-digit code** from the application's generic sender address. Owner approval is not needed to receive it.
2. The participant types the 6-digit code into the installer. The wizard submits the code, stable device ID, private installation nonce and hardware report (plus the already-entered name/availability/location, carried from the email request).
3. The backend validates and consumes the token in a Firestore transaction, creating devices/{id} with status=pending_approval. It saves the verified email, reported GPUs and suggested tier. credentialHash is null and enabled is false.
4. The owner reviews the pending device's email and hardware, then approves or rejects it. Approval creates its device credential hash and changes status to approved. Rejection changes status to rejected, with no credential.
5. The agent polls its approval endpoint using its device ID and private installation nonce. Only an approved, enabled device receives a credential. The agent saves it locally and starts heartbeats.

Participants have no accounts/passwords and no Firestore permissions. Anti-cheat and job scheduling are future cycles. Device-reported GPU hardware is trusted for now; it is not a remote hardware proof. This cycle has no job execution API.

## Install (no Python, no clone)

Standalone installers bundle everything — contributors do **not** need Python or the source checkout. Download the installer for your OS from the [Releases page](https://github.com/Sidd11145/GPUHive/releases), then run it. After installing, run `gridgpu join` to share your GPU.

- **Windows:** download `GPUHive-Setup-<version>.exe` and run it. The wizard installs GPUHive and adds a Start-Menu "Join GPUHive" shortcut. The binary is unsigned, so SmartScreen may warn — click **More info → Run anyway**.
- **macOS:** download `GPUHive-<version>.pkg` and open it. The binary is unsigned/un-notarized, so Gatekeeper may block it — **right-click the .pkg → Open**, then confirm. Installs `gridgpu` to `/usr/local/bin`. (GitHub's macOS runner is Apple-silicon, so this is an arm64 build.)
- **Linux:** download `gridgpu-<version>-linux-x86_64.tar.gz`, extract it, and run `./install-linux.sh` (copies `gridgpu` to `/usr/local/bin`, may prompt for sudo).

These installers are produced automatically by `.github/workflows/build-installers.yml` when a `v*` tag is pushed (or via a manual workflow run). All GPU detection still shells out to host tools (`nvidia-smi`, `lspci`, `system_profiler`) that must already be present on the machine.

## Install from source (developers)

Python 3.11+ and internet access are required. These are Python setup scripts, not standalone Windows executables. Download or copy dist/gridgpu-device-installer.zip to the GPU computer, extract it, and open a terminal in the gridgpu-device folder.

Windows, local hardware detection:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
~~~

Linux, local hardware detection:

~~~sh
bash install.sh
~~~

To register, supply the owner's deployed registration API URL:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -ApiUrl https://europe-west1-YOUR_PROJECT.cloudfunctions.net/registration_api
~~~

~~~sh
bash install.sh --api-url https://europe-west1-YOUR_PROJECT.cloudfunctions.net/registration_api
~~~

The wizard detects the hardware, asks whether to join, collects name/email/availability/location, sends the 6-digit code email immediately, accepts the typed code, submits the pending device and waits for your approval. It does not heartbeat while waiting. The owner can already inspect the submitted details and GPUs in the admin view.

The scripts install the agent in .venv-agent and expose system Python packages for runtime detection. They do not download CUDA, PyTorch or model weights. Windows/Linux installation still requires validation on actual contributor PCs.

## Resume a participant installation

The Python package also exposes these commands:

~~~sh
python -m gridgpu join --api-url https://europe-west1-YOUR_PROJECT.cloudfunctions.net/registration_api
python -m gridgpu inspect
python -m gridgpu request --api-url https://europe-west1-YOUR_PROJECT.cloudfunctions.net/registration_api --email participant@example.com --name "Jordan Doe"
python -m gridgpu register
python -m gridgpu status
python -m gridgpu run
~~~

Use the installer environment's Python: Windows .venv-agent\Scripts\python.exe; Linux .venv-agent/bin/python. join is the one-shot wizard the installer runs: detect GPU, collect name/email/availability/location, email the 6-digit code, accept it, submit the device and wait for approval. register prompts for the emailed 6-digit code, submits the device and waits for approval. run resumes a pending device's approval polling, or starts heartbeats for an approved installation. status checks device approval and saves the credential privately if it has become available. It never prints the credential. Add --once to join/register/run for one approval check and, only if approved, one heartbeat.

You can close the terminal while waiting and later resume with run. Keep the heartbeat agent's terminal open while contributing. Rejected devices stop waiting and do not heartbeat. Device ID, private enrollment nonce and eventual device credential persist under ~/.gridgpu/registration.json, with owner-only file permissions on POSIX. On Windows use the private user profile. Emailed codes are not saved locally.

The backend URL must use HTTPS. Plain HTTP is permitted only for loopback development URLs with --allow-local-http. Redirects are disabled. A submitted/registered installation cannot change its backend URL and send its identity to another service. The 6-digit code is single-use and expires in 15 minutes; per-device/IP rate-limiting of `/devices/register` against code guessing is deliberately deferred to a future cycle.

## Hardware detection

All NVIDIA GPUs exposed by nvidia-smi are enumerated with total/free VRAM and driver version. Windows CIM and Linux lspci provide name-only fallback detection when NVIDIA detection is unavailable; dedicated VRAM remains unknown. PyTorch's actual CUDA runtime and the driver's supported CUDA version are reported separately. Runtime inspection applies to the agent's Python environment.

Provisional advisory tiers per GPU: A = 4–<8 GiB, B = 8–<12 GiB, C = 12+ GiB. Smaller/unknown GPUs remain unclassified. The device summary uses the largest GPU, never summed VRAM. The backend recomputes the summary from the submitted report. Tiers do not guarantee model compatibility. speedScore remains null until a future benchmark.

## Configure the backend

functions/main.py exposes registration_api in europe-west1. Backend dependencies are in functions/requirements.txt; contributor PCs do not install the Firebase SDK. The function uses its managed service identity with Firestore data access and access to its three bound secrets.

Copy functions/.env.example to functions/.env. Configure an SMTP provider and one permitted generic sender address using SMTP_HOST, SMTP_PORT (587 or 465), SMTP_USER and SMTP_FROM. That address sends every participant's token email; the participant's entered email is the recipient. The sender is configured once for the application. TLS is used for mail delivery.

Create three distinct Secret Manager values through Firebase's interactive prompts:

~~~sh
firebase functions:secrets:set GRIDGPU_ADMIN_SECRET --project YOUR_PROJECT
firebase functions:secrets:set GRIDGPU_DEVICE_ISSUER_SECRET --project YOUR_PROJECT
firebase functions:secrets:set GRIDGPU_SMTP_PASSWORD --project YOUR_PROJECT
~~~

Admin and issuer secrets must be random values of at least 32 characters. Keep the issuer secret stable across deployments; it reconstructs approved credentials from per-device salts while only credential hashes are stored in Firestore. The SMTP secret is the mail provider's credential. Do not commit secrets or pass them in command arguments.

Default approval polling is 30 seconds with client jitter. Approved devices heartbeat approximately every five minutes with jitter; offline status follows 15 minutes without a valid heartbeat. Those intervals and the advisory tier thresholds are configurable in functions/.env. Match installer tier thresholds if you change the backend's advisory thresholds.

Prepare the function environment in your chosen backend deployment environment:

~~~sh
python3 -m venv functions/venv
functions/venv/bin/python -m pip install -r functions/requirements.txt
~~~

On Windows, use functions\venv\Scripts\python.exe for the second command. Enable Firestore and a supported billing plan for Cloud Functions. Deployment is separate and has not been run:

~~~sh
firebase deploy --project YOUR_PROJECT --only functions:registration,firestore:rules
~~~

The supplied Firestore rules deny every client read/write. Only the backend's managed IAM identity accesses Firestore. Review these rules before using a project that also hosts other applications.

## Owner approval view

Open URL/admin for the owner view. Enter your admin secret and select Load pending devices. Each card shows the submitted name, email, availability, location, hardware report, suggested tier and approval/rejection buttons. Approving sends no second email: the waiting agent retrieves its credential through nonce-protected polling. Rejected devices remain recorded and blocked. The owner can disable/re-enable approved devices; enabling a pending/rejected device is rejected.

Every admin data/action endpoint verifies your secret. The page holds it only in memory, never in HTML, a URL or browser storage. Clear it when finished. Neither admin lists nor registration responses contain usable credentials.

## State and credential rules

- status transitions from pending_approval to approved or rejected. Only approved and enabled devices can heartbeat. Repeating the same owner decision is idempotent; a conflicting decision is rejected.
- A pending device has no credential, cannot update lastSeenAt, and is reported offline. Only a correct device ID plus private nonce can poll its approval. Another installation's nonce cannot fetch its credential.
- Registration consumes the email token and creates the pending device atomically. Concurrent token use can submit only one device. A retry bound to the same ID/nonce recovers the same submission after a lost response, even after token expiry; registration never returns the credential.
- Credential hashes are created only at approval. The backend can reconstruct the credential for authenticated polling after a lost poll response or process restart. It is never emailed.
- Every heartbeat checks approved status, credential hash and enabled state before a partial server-time update of lastSeenAt. A nonce is not a heartbeat credential. Client timestamps and online flags are rejected.
- SMTP failure revokes the candidate token and permits requesting another one. If an unused token expires, request a new email token; approval is not needed to do so.

## Firestore schema

| Collection | Main fields |
|---|---|
| registrationTokens/{tokenHash} | email, name, availability, location, createdAt, expiresAt, usedAt, revoked, emailStatus, deviceId, registrationNonceHash |
| devices/{deviceId} | email, name, availability, location, emailVerifiedAt, hardware, suggestedTier, hardwareVerification, speedScore, status, enabled, registrationNonceHash, credentialHash, credentialSalt, registeredAt, reviewedAt, lastSeenAt, telemetry |
| registrationRequestLimits/{emailHash} | lastRequestedAt, tokenHash; at most one new token request per email per minute |

There is no email approval queue or participant-account collection. hardwareVerification is self_reported. Raw tokens, private nonces and usable device credentials are never persisted in Firestore. Expiry is enforced by the backend, independently of database cleanup timing.

## HTTP interface

Paths below are relative to the function URL. Admin routes use Authorization: Bearer <admin secret>. Approval polling uses Authorization: Bearer <installation nonce>. Heartbeats use Authorization: Bearer <device credential>.

| Method / path | Purpose |
|---|---|
| POST /requests | Submit {email, name, availability, location}; immediately email a 6-digit code |
| GET /admin | Owner approval page shell |
| GET /admin/devices?status=pending_approval | Owner's pending-device queue and hardware reports |
| POST /admin/devices/{id}/approve | Approve and issue device credential hash |
| POST /admin/devices/{id}/reject | Reject with no credential |
| GET /admin/devices | Owner's device/presence view |
| POST /admin/devices/{id}/disable or /enable | Change enabled state of approved devices |
| POST /devices/register | Submit code, deviceId, registrationNonce, hardware; pending-device response only |
| GET /devices/{id}/approval | Nonce-authenticated status; credential only when approved and enabled |
| POST /devices/{id}/heartbeat | Submit {telemetry} with approved device credential |

There is no browser registration page; participants join through the installer wizard. Current admin lists return up to 100 records; pagination is future work. A durable mail outbox is also future work; if a function dies during email delivery, the unused code may remain emailStatus=sending until it expires and the participant requests a new one.

## Verify and package

~~~sh
python3 -m pip install '.[test]'
python3 -m pytest -q
python3 -m compileall -q gridgpu functions
python3 tools/build_device_bundle.py
~~~

The bundle includes only contributor code, installers and its setup guide. No backend source, Firebase settings or secrets are included. Tests use fake mail delivery and a transactional memory store through the production registration service/Flask routes. They cover immediate email, expiry, concurrent submission, approval/rejection, nonce isolation, credential recovery, pending agent restart, polling, heartbeat gating, disabling and hardware detection. They do not contact live Firebase, send actual email, or benchmark physical GPUs. The Firebase SDK/deployment and live Firestore/SMTP integration require checks in the configured backend environment.
