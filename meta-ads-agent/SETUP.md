# Meta setup (one-time)

The agent needs a Meta developer app and an ad account it may touch. These
steps create accounts and secrets, so they have to be done by you, in the
browser. Nothing in this file has to be done before trying the agent: with an
empty `.env` it runs in **dry run** against fake data.

Facts below were checked on developers.facebook.com on 2026-09-05. Menu labels
in Meta's UI change often; the *names of the objects* do not.

## What you need

- A Business Portfolio (Business Manager).
- An ad account of your own inside it.
- A developer app with the Marketing API, and its sandbox ad account.

## What access you get without App Review

Meta's Marketing API has two tiers:

| Tier | App Review | Whose ad accounts | Rate limit |
|---|---|---|---|
| Limited Access (default, "Development") | not needed | only accounts your Business owns / you admin | heavy, dev only |
| Full Access ("Standard") | required | any account that grants you `ads_read` / `ads_management` | light |

To be approved for Full Access the app must already have made **at least 500
Marketing API calls in the last 15 days with under 15% errors**. In other
words: build and use it on your own account first, then apply. Client
accounts come after that.

Source: <https://developers.facebook.com/docs/marketing-api/overview/authorization>

## Steps

### 1. Create your own ad account (in the Business Portfolio)

Business Settings → Accounts → Ad accounts → Add → *Create a new ad account*.
Currency ILS, time zone Jerusalem. You do **not** need to add a payment method
for the agent to read it; you do need one only when a real campaign is meant
to deliver.

Write down the id. In the API it is used as `act_<digits>`.

### 2. Register as a developer and create an app

1. <https://developers.facebook.com/> → register as a Meta Developer with your
   personal Facebook login (the one that admins the Business Portfolio).
2. My Apps → Create App (<https://developers.facebook.com/apps/creation/>).
   Enter name + contact email → Next. On the **Use cases** screen pick
   **"Create & manage ads with Marketing API"** (add "Measure ad performance
   data with Marketing API" too if offered). Connect your Business Portfolio
   when asked. → Go to dashboard.
3. Newer "use case" apps have no **+ Add Product** button. Instead: left
   menu **Build your app → Use cases → Add use cases** (or **Customize** on an
   existing one) → pick **"Create & manage ads with Marketing API"**. Adding
   the use case attaches the Marketing API product and its permissions
   automatically; the Marketing API *Quickstart / Tools / Settings* pages then
   appear inside that use case's Customize view. Older "app type" apps keep a
   **Products** section with **+ Add Product**.
4. **App ID** and **App Secret**: left menu → **App settings → Basic**.
   The secret is hidden behind a **Show** button and asks for your Facebook
   password.

Source: <https://developers.facebook.com/docs/marketing-api/get-started>

### 3. Create the sandbox ad account (this is where the agent may write)

Two places in the app dashboard lead there:

- **Marketing API → Quickstart → "Create an Ad Campaign" → "Create Sandbox
  Ad Account"** (the same wizard has a **"Generate Access Token"** button,
  good for a first short-lived token), or
- **Marketing API → Tools → Sandbox Mode**.

- One sandbox account per app.
- Full Marketing API surface, but **ads never deliver**: no impressions, no
  spend, no funding source needed. Insights are empty.
- Its id also looks like `act_<digits>`. This id goes in `META_WRITE_ACCOUNT_IDS`.

Sources: <https://developers.facebook.com/blog/post/2023/06/21/marketing-api-sandbox-capability-now-re-enabled/>,
<https://developers.facebook.com/ads/blog/post/v2/2016/10/19/sandbox-ad-accounts/>

### 4. Get an access token that does not expire every hour

Preferred: a **System User** token.

1. Business Settings → Users → System users → Add. Role: Admin.
2. Add Assets → give the system user access to (a) the app, (b) your ad
   account (read is enough for now), (c) the sandbox ad account (manage).
3. Generate New Token → pick the app → permissions `ads_read`,
   `ads_management`, `business_management` → choose "never expires" if offered.

Quick alternative for a first test: the Graph API Explorer
(<https://developers.facebook.com/tools/explorer/>) issues a short-lived user
token with the same permissions. It dies after about an hour. Fine for
`check_setup`, not for daily use.

**Do not paste the token into chat or into any file other than `.env`.**

### 5. Fill in the hub's `.env` and check

All tools share one file: the hub's root `.env` (copy `.env.example`
next to it if it does not exist yet). Add:

```
META_APP_ID=<app id>
META_APP_SECRET=<app secret>
META_ACCESS_TOKEN=<system user token>
META_WRITE_ACCOUNT_IDS=act_<sandbox id>
```

Leave `META_READ_ACCOUNT_IDS` empty to read every account the token can see,
or list ids to restrict it. Then run the free check (no Claude call):

```bash
.venv/bin/python -m meta_agent.check_setup      # Windows: .venv\Scripts\python ...
```

It prints the accounts the token sees and flags the sandbox as the only
writable one.

## Later: reaching client accounts

Two routes, both after the 500-call threshold above:

1. **Partner access.** The client's Business Portfolio grants your Business
   access to their ad account. Your system user then sees it. Standard agency
   route.
2. **Facebook Login for Business** in your app, so a client can authorise it
   themselves. More work, more review.

Either way the agent stays read-only on client accounts until you decide
otherwise (`META_WRITE_ACCOUNT_IDS`).
