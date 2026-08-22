# Deployment — systemd

Run the PDF Redactor API as a system service on the VM. The service starts
at boot, restarts on crash, and keeps running when no user is logged in.

Artifacts:

- [`pdf-redactor.service`](pdf-redactor.service) — the systemd unit.
- [`pdf-redactor.env`](pdf-redactor.env) — env vars (HOST, PORT) sourced by
  the unit; edit the installed copy at `/etc/default/pdf-redactor` without
  touching the unit file.
- [`ollama-restart.conf`](ollama-restart.conf) — optional drop-in that
  forces `Restart=always` on `ollama.service`. Only install if Ollama's
  current unit doesn't already have it.

## Install

Run from the repo root on the VM.

### 1. (Optional) Ensure Ollama auto-restarts

```bash
systemctl show ollama -p Restart -p RestartUSec
```

If the output already shows `Restart=always`, skip to step 2. Otherwise:

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo install -m 644 deploy/ollama-restart.conf \
  /etc/systemd/system/ollama.service.d/restart.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

### 2. Install the PDF Redactor unit

```bash
sudo install -m 644 deploy/pdf-redactor.service /etc/systemd/system/pdf-redactor.service
sudo install -m 644 deploy/pdf-redactor.env     /etc/default/pdf-redactor
sudo systemctl daemon-reload
sudo systemctl enable --now pdf-redactor.service
```

## Verify

On the VM:

```bash
sudo systemctl status pdf-redactor
sudo ss -tlnp | grep :8000            # expect 0.0.0.0:8000 (python)
curl -sf http://127.0.0.1:8000/health
journalctl -u pdf-redactor -f         # tail logs
```

From a VPN-connected Windows client (PowerShell):

```powershell
Test-NetConnection -ComputerName 10.222.56.20 -Port 8000
Invoke-WebRequest -Uri http://10.222.56.20:8000/health -UseBasicParsing
```

Open `http://10.222.56.20:8000/docs` in a browser for the Swagger UI.

Crash-restart, boot, and SSH-disconnect tests:

```bash
sudo systemctl kill -s KILL pdf-redactor      # API should be back in ~5s
sudo systemctl kill -s KILL ollama            # Ollama should be back in ~5s
sudo reboot                                   # both units auto-start
```

## Change env vars

```bash
sudoedit /etc/default/pdf-redactor
sudo systemctl restart pdf-redactor
```

No `daemon-reload` needed for env-file changes.

## Update the unit file

After editing `deploy/pdf-redactor.service` in the repo:

```bash
sudo install -m 644 deploy/pdf-redactor.service /etc/systemd/system/pdf-redactor.service
sudo systemctl daemon-reload
sudo systemctl restart pdf-redactor
```

## Uninstall

```bash
sudo systemctl disable --now pdf-redactor.service
sudo rm /etc/systemd/system/pdf-redactor.service
sudo rm /etc/default/pdf-redactor
# Only if the Ollama drop-in was installed in step 1:
sudo rm -f /etc/systemd/system/ollama.service.d/restart.conf
sudo systemctl daemon-reload
```
