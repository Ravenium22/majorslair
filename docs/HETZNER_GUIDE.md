# Running the Major's Lair bot and admin website on Hetzner (no technical background needed)

This guide takes you from nothing to a Discord bot and a private admin website that run 24/7 on
a small Hetzner server. Budget about 90 minutes the first time. Nothing here is dangerous: if a
step goes wrong you can simply redo it.

You will type commands into a black "terminal" window. Type them exactly as shown and press
Enter. Copy and paste works; on Windows, paste into the terminal with a right-click.

What you end up with:

- The bot online in Discord with `/link-twitter`, `/leaderboard`, `/check-engagement` and so on.
- A website at `https://your-domain` where admins sign in with Discord and manage members,
  scoring rules, tracked posts, scans, and the leaderboard cycle.
- A database on the server that is backed up automatically every night.

---

## Part 0 - For Ravenium: before handing this over

The code lives at <https://github.com/Ravenium22/majorslair>. If that repository is private,
either make it public (there are no secrets in the code) or create a fine-grained personal
access token with read-only "Contents" permission and give Major a clone URL of the form
`https://<TOKEN>@github.com/Ravenium22/majorslair.git` to use in Part 3, step 3.

Whoever creates the Discord application, the twitterapi.io account, and the domain owns the
billing and the keys. This guide assumes Major does.

---

## Part 1 - Accounts and keys (do this on your normal computer)

You need five things before touching the server. Write each one down in a private note; you
will paste them into the server later.

### 1A. A domain name for the admin website

The website needs a name like `lair.example.com` so that browsers trust it (HTTPS) and so
Discord can send admins back to it after login. Pick one:

**Option 1 - you already own a domain.** In your domain provider's DNS settings you will add an
"A record" later (Part 2, step 10) pointing a subdomain such as `lair` at the server. Your
`DOMAIN` value will be `lair.yourdomain.com`.

**Option 2 - free subdomain from DuckDNS (no purchase).**

1. Go to <https://www.duckdns.org/> and sign in with any of the offered accounts.
2. Under "sub domain", type a name such as `majorslair` and click **add domain**. Your
   `DOMAIN` value will be `majorslair.duckdns.org`.
3. Leave the page open; you will paste the server's IP into the "current ip" box in Part 2.

### 1B. Discord application: bot token and OAuth

1. Go to <https://discord.com/developers/applications> and log in.
2. Click **New Application**, name it `Major's Lair Tracker`, click **Create**.
3. In the left menu click **Bot**.
4. Click **Reset Token**, confirm, then **Copy**. This is your `DISCORD_TOKEN`. Treat it like
   a password. It is shown only once; if you lose it, click Reset Token again.
5. Scroll down to **Privileged Gateway Intents**. Leave all three switches **off**.
6. In the left menu click **OAuth2**.
   - Copy the **Client ID**. This is `DISCORD_CLIENT_ID`.
   - Click **Reset Secret**, confirm, and copy it. This is `DISCORD_CLIENT_SECRET`.
   - Under **Redirects** click **Add Redirect** and enter
     `https://YOUR-DOMAIN/auth/callback` using the domain from step 1A, for example
     `https://majorslair.duckdns.org/auth/callback`. Click **Save Changes** at the bottom.
     This must match exactly, or the website login will fail.
7. Still on the OAuth2 page, under **OAuth2 URL Generator** tick `bot` and
   `applications.commands`. In **Bot Permissions** tick `View Channels`, `Send Messages`,
   `Embed Links`, `Read Message History`.
8. Copy the **Generated URL** at the bottom, open it in your browser, choose the Major's Lair
   server, click **Continue** and **Authorise**. The bot now appears in the member list
   (offline for now).

### 1C. Discord IDs (server, audit channel, admin role)

1. In Discord, open **User Settings > Advanced** and switch on **Developer Mode**.
2. Right-click the server icon and click **Copy Server ID**. This is `DISCORD_GUILD_ID`.
3. Create a private text channel called `#audit-log-engagement` that only admins can see.
   Make sure the bot can see it too (edit the channel, **Permissions**, add the bot with
   View Channel and Send Messages). Right-click the channel and click **Copy Channel ID**.
   This is `DISCORD_AUDIT_CHANNEL_ID`.
4. Optional: to let a role other than server administrators use admin commands and the
   website, open **Server Settings > Roles**, right-click the role and click **Copy Role ID**.
   This is `ADMIN_ROLE_IDS`. The server owner and administrators are always allowed, so you may
   leave this empty.

### 1D. twitterapi.io key

1. Go to <https://twitterapi.io/dashboard> and create an account.
2. Add a small amount of credit (10 USD lasts a long time with daily scans).
3. Copy the **API key** from the dashboard. This is `TWITTERAPI_IO_KEY`.

You now have: a domain, the Discord token, client ID, client secret, server ID, audit channel
ID, and the twitterapi.io key.

---

## Part 2 - Create the server on Hetzner

1. Go to <https://console.hetzner.cloud/>, create an account and a project (any name).
2. Click **Add Server**.
3. **Location**: pick any (Falkenstein or Nuremberg are fine).
4. **Image**: choose **Ubuntu 24.04**.
5. **Type**: choose **Shared vCPU > x86 > CX22** (the cheapest, about 4 EUR per month).
6. **Networking**: leave IPv4 and IPv6 ticked.
7. **SSH keys**: skip this unless you already know what it is. Hetzner will email you a root
   password instead.
8. Leave everything else at the default. Name the server `majorbot` and click
   **Create & Buy now**.
9. Note the server's **IP address** shown on the server page (four numbers with dots, for
   example `95.217.10.42`) and check your email for the root password.
10. Point your domain at that IP:
    - **DuckDNS**: on the DuckDNS page, paste the IP into the "current ip" box next to your
      subdomain and click **update ip**.
    - **Own domain**: in your DNS provider, add an **A record** with name `lair` (or whatever
      subdomain you chose) and value = the server IP. It can take up to an hour to become active.

---

## Part 3 - Connect and install

### 3.1 Open a terminal on your computer

- **Windows**: press the Windows key, type `Terminal` (or `PowerShell`) and open it.
- **Mac**: open **Terminal** from Applications > Utilities.

### 3.2 Connect to the server

Type the following, replacing the numbers with your server's IP, and press Enter:

```
ssh root@95.217.10.42
```

- The first time it asks "Are you sure you want to continue connecting?" Type `yes`, Enter.
- It asks for the password. Paste the root password from the Hetzner email. **Nothing appears
  while you type the password; that is normal.** Press Enter.
- Ubuntu will force you to choose a new password: enter the emailed password once more, then
  type a new password twice. Save the new password somewhere safe.

You are now "inside" the server. The prompt looks like `root@majorbot:~#`.

### 3.3 Run the installer

Paste this single line and press Enter:

```
curl -fsSL https://raw.githubusercontent.com/Ravenium22/majorslair/main/deploy/install.sh | bash -s -- https://github.com/Ravenium22/majorslair.git
```

It installs Docker and the bot. It takes 5 to 10 minutes and prints a lot of text. At the end
it prints "Installation finished" and a list of next steps.

From now on, everything is done with the `majorbot` command.

### 3.4 Fill in the settings

```
majorbot edit-config
```

A simple text editor (nano) opens the settings file. Use the arrow keys to move. Fill in the
values after the `=` signs:

```
DOMAIN=majorslair.duckdns.org
DISCORD_TOKEN=paste-the-discord-token-here
DISCORD_GUILD_ID=paste-the-server-id-here
DISCORD_CLIENT_ID=paste-the-client-id-here
DISCORD_CLIENT_SECRET=paste-the-client-secret-here
DISCORD_AUDIT_CHANNEL_ID=paste-the-audit-channel-id-here
ADMIN_ROLE_IDS=
TWITTERAPI_IO_KEY=paste-the-twitterapi-key-here
POSTGRES_PASSWORD=(already filled in by the installer, leave it)
LOG_LEVEL=INFO
```

Leave everything under "Advanced" empty.

To save: press **Ctrl+O**, then **Enter**. To leave the editor: press **Ctrl+X**.

### 3.5 Test everything

```
majorbot check
```

This checks the database, the Discord token, the server ID and the twitterapi.io key, and
prints `OK` lines. It also prints the exact OAuth redirect address; compare it with what you
entered in Part 1B step 6. If something is wrong, it prints a plain-language explanation of
what to fix. Fix it with `majorbot edit-config`, then run the check again.

### 3.6 Start everything

```
majorbot start
```

Then watch the log:

```
majorbot logs
```

Within about a minute you should see a line containing `Connected as Major's Lair Tracker`.
Press **Ctrl+C** to stop watching the log (the bot keeps running).

Now open `https://YOUR-DOMAIN` in your browser. The first visit can take up to a minute while
the free HTTPS certificate is issued. You should see the "Engagement control center" login
page. Click **Continue with Discord**, approve, and the dashboard opens. Only the server owner,
administrators, and the roles from `ADMIN_ROLE_IDS` can get in.

In Discord, type `/` in any channel: the bot's commands (`/leaderboard`, `/link-twitter`,
`/check-engagement`, ...) should appear.

You can close the terminal now. Everything keeps running, and it restarts itself automatically
if it crashes or if the server reboots.

---

## Part 4 - Day-to-day

Everything normal happens in Discord and on the website:

- Members link their X account with `/link-twitter @handle`.
- Admins run scans from the website's Overview page (24 hours daily, 7 days weekly) or with
  `/refresh-engagement` in Discord. Each scan reports its own cost estimate.
- Scoring rules are edited on the website's **Scoring rules** page. Saving recalculates the
  current cycle immediately.
- **Reset the leaderboard** on the Scoring rules page or with `/reset-leaderboard`.
- The **Audit trail** page shows who changed what.

When you do need the server, connect the same way (`ssh root@YOUR-IP`) and use:

| Command | What it does |
|---|---|
| `majorbot status` | Shows whether everything is running |
| `majorbot logs` | Shows the live bot log; Ctrl+C to leave |
| `majorbot logs-web` | Shows the HTTPS proxy log (domain or certificate problems) |
| `majorbot restart` | Restarts the bot and website (needed after `majorbot edit-config`) |
| `majorbot stop` / `majorbot start` | Stops or starts everything |
| `majorbot update` | Downloads the newest version and restarts it |
| `majorbot backup` | Saves a database backup now (one is also made every night at 04:00) |
| `majorbot restore FILE` | Restores a backup; replaces the current data |
| `majorbot check` | Tests the settings without starting anything |
| `majorbot help` | Lists these commands |

---

## Part 5 - When something goes wrong

**The bot is offline in Discord.**
Run `majorbot status`. If a container says restarting or exited, run `majorbot logs` and read
the last lines. The bot prints plain-language messages for the common problems.

**"Discord rejected DISCORD_TOKEN".**
Developer Portal > Bot > Reset Token, copy the new token, `majorbot edit-config`, replace the
token, save, then `majorbot restart`.

**The website does not load or shows a certificate warning.**
Run `majorbot logs-web`. Usually the domain does not point at the server yet (check the A
record or DuckDNS IP) or `DOMAIN` in `.env` has a typo. DNS changes can take up to an hour.

**Login says "Invalid OAuth state" or Discord shows "Invalid redirect_uri".**
The redirect in the Developer Portal must be exactly `https://YOUR-DOMAIN/auth/callback`.
`majorbot check` prints the exact value to use.

**Login says "Engagement admin access required".**
The account is not the server owner, not an administrator, and not in `ADMIN_ROLE_IDS`.

**"twitterapi.io says the account balance is empty".**
Add credit at <https://twitterapi.io/dashboard>.

**A command in Discord says "The command failed unexpectedly".**
Run `majorbot logs` and send the last 30 lines to Ravenium.

**I lost the server password.**
In the Hetzner console open the server, click **Rescue > Reset root password**.

**The server was rebooted or Hetzner did maintenance.**
Nothing to do. Docker starts everything automatically.

---

## Part 6 - Costs and safety

- Hetzner CX22: about 4 EUR per month. The database lives on the server, so do not delete the
  server without taking a backup first (`majorbot backup`, then download the file from
  `/opt/majorbot/backups`).
- twitterapi.io: pay as you go, roughly 0.15 USD per 1000 items scanned. Daily scans for a
  mid-sized community cost a few USD per month.
- The secrets (Discord token, client secret, twitterapi.io key, database password) are stored
  only on the server in a file that only root can read. Never paste them into Discord.
- Keep the server updated occasionally: connect and run
  `apt-get update && apt-get upgrade -y` then `reboot`. Everything comes back on its own.
