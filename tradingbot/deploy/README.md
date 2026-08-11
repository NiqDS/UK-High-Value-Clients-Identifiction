# Deploying the bot to an always-on server

This runs the bot 24/7 on a cloud VM so it never sleeps or drops off Wi-Fi.
Works on any Ubuntu 24.04 server (Yandex Cloud, VK Cloud, Hetzner, a UK VPS,
etc.). The scripts here do the heavy lifting; the steps below are the runbook.

---

## ⚠️ Read first: which provider / where the server lives

The bot connects to **Bybit**, whose account is UK-based. Exchanges geo-fence
and flag logins from unexpected regions.

- **Paper / data-collection run (`config.bybit-test-active.yaml`, `dry_run: true`):**
  low risk — no real orders leave the box. A Russian-cloud IP (Yandex/VK) is
  fine for *collecting data*.
- **Real money (`config.bybit-live.yaml`, `dry_run: false`):** a Russian-cloud
  IP connecting to a UK Bybit account can get the account **restricted or
  frozen**. For live trading, prefer a server in a region consistent with your
  account (a UK/EU VPS), or check Bybit's stance for your situation first.

Everything here is provider-agnostic, so you are not locked in — you can start
data collection on Yandex/VK now and move the live run elsewhere later.

**VM size:** the bot is tiny. **2 vCPU / 2 GB RAM / 20 GB disk** is plenty.
Pick **Ubuntu 24.04 LTS** (ships Python 3.12, the version the bot is validated
on — avoids the 3.14 breakage seen locally).

---

## 1. Create the VM (console)

### Yandex Cloud
1. Management console → **Compute Cloud** → **Create VM**.
2. Name it (e.g. `tradingbot`), pick an availability zone.
3. **Image:** Ubuntu 24.04 LTS from the Marketplace.
4. **Resources:** 2 vCPU, 2 GB RAM; disk 20 GB (SSD is fine).
5. **Access:** add your **SSH public key** (see step 2) and set login as
   `yc-user` (Yandex's default), or your own username.
6. Create. Note the assigned **public IP**.

### VK Cloud
Nearly identical (OpenStack/Horizon dashboard): **Cloud Servers → Create**,
choose **Ubuntu 24.04**, flavor with 2 vCPU / 2 GB, add your **SSH key pair**,
create, note the **Floating IP**. Default login user is usually `ubuntu`.

> If you don't already have an SSH key, create one on your own machine:
> `ssh-keygen -t ed25519` → the public half is `~/.ssh/id_ed25519.pub`.
> Paste that file's contents into the provider's "SSH key" field.

## 2. Connect

```bash
ssh <login-user>@<server-ip>        # e.g. ssh yc-user@158.160.x.x  (Yandex)
                                    #      ssh ubuntu@<floating-ip> (VK)
```

## 3. Provision (one command)

On the server, download and run the setup script. Either clone first, or grab
just the script:

```bash
# clone, then run the provisioner:
git clone https://github.com/NiqDS/UK-High-Value-Clients-Identifiction.git
cd UK-High-Value-Clients-Identifiction/tradingbot
bash deploy/setup-server.sh
```

This installs Python/venv, pulls the branch, creates the virtualenv, and
installs the bot.

## 4. Add your Bybit key (local to the server — never commit it)

```bash
cd ~/UK-High-Value-Clients-Identifiction/tradingbot
cp .env.example .env
nano .env      # fill ONLY:
               #   EXCHANGE_API_KEY=...
               #   EXCHANGE_API_SECRET=...
```

The key must be **TRADE + READ, withdrawals DISABLED, spot enabled**. The
`.env` is gitignored and stays on this server only.

Sanity-check (expect `creds present`, `dry_run True`):

```bash
./.venv/bin/python -m tradingbot --config config.bybit-test-active.yaml check-config
```

## 5. Turn it into an always-on service

```bash
bash deploy/install-service.sh
```

That installs a **systemd** service that starts the bot now, restarts it if it
crashes, and starts it again automatically after a reboot.

## 6. Monitor

```bash
journalctl -u tradingbot -f          # live process logs
systemctl status tradingbot          # is it running?
tail -f ~/UK-High-Value-Clients-Identifiction/tradingbot/data/test_active.log

# DB progress (trades/decisions/win-rate/per-coin):
cd ~/UK-High-Value-Clients-Identifiction/tradingbot
./.venv/bin/python -m tradingbot --config config.bybit-test-active.yaml db-stats
```

## 7. Update to the latest code later

```bash
cd ~/UK-High-Value-Clients-Identifiction/tradingbot
git pull origin claude/wonderful-ride-dzjenn
./.venv/bin/pip install -e .          # only if dependencies changed
sudo systemctl restart tradingbot
```

---

## Notes

- **Don't run the live config on two machines against the same account** — they
  fight over the balance. Paper runs are harmless but keep **separate local
  DBs**, so their data doesn't merge automatically.
- **Back up the data:** the collected DB lives at `data/tradingbot_test_active.db`.
  To pull it to your laptop: `scp <user>@<ip>:~/UK-High-Value-Clients-Identifiction/tradingbot/data/tradingbot_test_active.db .`
- **Firewall:** you only need inbound SSH (port 22). The bot makes **outbound**
  connections to Bybit; no inbound ports required. Lock the security group to
  SSH-only.
