# Connect a Facebook Page and Instagram to the Social Publisher — click by click

Written 2026-09-06 from Meta's own help pages (links at each part). Meta moves
labels around; when a label differs, look for the same meaning. Anything I
could not confirm on Meta's pages is marked **[verify]**.

**What you will have at the end**: a Facebook Page and a professional
Instagram account, both inside your business portfolio; your existing app
with the Page and Instagram permissions; one long-lived token in the root
`.env`; the two ids in `SOCIAL_PUBLISH_TARGETS`. Total time about one hour,
most of it waiting for pages to load.

Terms: *business portfolio* is what Meta now calls Business Manager /
"Business account". *Meta Business Suite* is business.facebook.com. The
*app* is the one you created for the ads agent.

---

## Part A — Create the Facebook Page (computer, 5 minutes)

Source: Meta Business Help Center, "How to create a Facebook Page".

1. Log in to Facebook with the profile that admins your business portfolio.
2. In the left column of the home feed click **Pages**. If it is not visible,
   click **See more** first.
3. Under **Your Pages and profiles** click **Create new Page**.
4. **Page name**: the agency's name (you can rename later).
   **Category**: type "Marketing agency" and pick it.
5. Click **Create Page**.
6. Optional now: **Bio**, profile picture, cover photo. Skip; the agent can
   post without them.

Keep it out of sight while testing **[verify]**: on the Page, open
**Settings** → **Privacy** → **Page and tagging** and look for **Page
visibility** → set **Page unpublished**. Meta's Pages API accepts posts to an
unpublished Page (developer docs, Pages API "Getting started"), and only the
admins see them. If you cannot find the setting, leave the Page published: it
has no followers, so a test post reaches nobody.

**Check**: the Page appears at facebook.com → Pages → Your Pages and profiles.

---

## Part B — Put the Page in your business portfolio (5 minutes)

1. Go to **business.facebook.com/settings** (Meta Business Suite → Settings).
   If asked, pick your business portfolio.
2. Left sidebar: **Accounts** → **Pages**.
3. Click **Add** → **Add a Page** (not "Request access").
4. Type the Page name, pick it, click **Add Page**. You are its admin, so it
   is added immediately.

**Check**: the Page is listed under Accounts → Pages.

---

## Part C — Instagram: professional account, connected to the Page (phone, 15 minutes)

Source: Meta Business Help Center, "Set up a professional Instagram account"
and "Connect or disconnect your professional Instagram account and a
Facebook Page".

### C1. Create the account
1. In the Instagram app, log out or use **Add account** → **Create new
   account**. Username: the agency name. Use your email.
2. Finish sign-up; skip contacts and suggestions.

### C2. Switch it to a professional account
1. Tap your **profile picture** (bottom right).
2. Tap **☰ More** (top right) to open **Settings and activity**.
3. Under **For professionals** tap **Account type and tools**.
4. Tap **Switch to professional account**.
5. Pick a category (e.g. "Marketing Agency"), tap **Switch to professional
   account**, choose **Business**, tap **Next**.
6. Contact details: tap **Don't use my contact info** (or fill them).
7. When asked to connect to Facebook: tap **Login to Facebook**, log in with
   the profile from Part A, pick the Page, confirm. If you skipped this, do
   C3.

Note from Meta: a private account becomes **public** when it turns
professional. Publishing through the API requires a professional account.

### C3. Connect to the Page (if not done in C2)
1. Profile → **Edit profile**.
2. Under **Public business information** tap **Page**.
3. Tap **Connect or create** → **Continue** → **Login to Facebook** →
   **Continue**.
4. Choose the Page from Part A → **Connect**.

### C4. Put the Instagram account in the same business portfolio
Meta requires the Instagram account and the Page to be in the **same**
portfolio.
1. business.facebook.com/settings → **Accounts** → **Instagram accounts**.
2. **Add** → log in to the new Instagram account in the pop-up → confirm.

**Check**: Accounts → Instagram accounts lists it, and on the Page's
settings the Instagram account shows as linked.

---

## Part D — Give the app the Page and Instagram permissions (10 minutes)

Source: Meta for Developers, "Customize a Meta app with the Manage everything
on your Page use case"; App Dashboard docs.

1. Go to **developers.facebook.com** → **My Apps** → open the app you made
   for the ads agent.
2. Left menu: **Use cases**. Click **Add use cases** (button near the top;
   on some dashboards it is **Add** next to the list).
3. Pick **Manage everything on your Page** → Add. It brings
   `business_management`, `pages_show_list` and `public_profile` by default.
4. Back on **Use cases**, click **Customize** on *Manage everything on your
   Page*. Open **Permissions and features**. Click **Add** next to:
   `pages_manage_posts`, `pages_read_engagement`, `pages_manage_metadata`.
   (`publish_video` too if it is listed; it is needed for video posts.)
5. Again **Use cases** → **Add use cases** → pick the **Instagram** use case
   (its name starts with "Instagram"). Then **Customize** it.
   Choose the API setup that logs in **with Facebook** (not "with Instagram
   login") **[verify exact label]**: this is the route for Instagram
   accounts connected to a Page. Click **Add** next to `instagram_basic` and
   `instagram_content_publish`.
6. Nothing to submit: for Pages and Instagram accounts your own business owns,
   these permissions work in **Development / Limited** access without App
   Review.

**Check**: on each use case's Customize page the permissions above show as
added (they may say "Ready for testing" or "Standard access").

---

## Part E — The token (system user, does not expire) (10 minutes)

Source: Meta Business Help Center, "Add system users to your business
portfolio".

1. business.facebook.com/settings → left sidebar **Users** → **System users**.
2. Click **Add new system user**. Name: `shivuk-publisher`. Role: **Admin**.
   Click **Create system user**.
3. With the new system user selected, click **Assign assets** (or **Add
   assets**).
4. **Pages** → tick your Page → turn on **Full control** (Manage Page) →
   **Save changes**.
5. **Assign assets** again → **Instagram accounts** → tick the account →
   **Full control** → **Save changes**.
6. **Assign assets** again → **Apps** → tick your app → **Full control**
   (Manage app) → **Save changes**.
7. Click **Generate new token**.
   - **App**: your app.
   - **Token expiration**: **Never** (if offered; otherwise 60 days, and
     put a reminder in your calendar).
   - Tick: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
     `pages_manage_metadata`, `instagram_basic`,
     `instagram_content_publish`, `business_management` (and
     `publish_video` if present). A permission that is missing from this list
     was not added in Part D.
   - Click **Generate token**. **Copy it now**; Meta shows it once.
8. Open `digital-marketing-tools\.env` and paste it on the line
   `SOCIAL_META_USER_TOKEN=`. Do not paste it anywhere else.

---

## Part F — Verify from your machine (free, 1 minute)

```powershell
cd <project folder>\social-agent
.venv\Scripts\python -m social_agent.check_setup
```

You should see: `Token OK`, every permission `ok`, and two targets, a
`facebook_page` and an `instagram`, each with its id. Copy the two ids into
the root `.env`:

```
SOCIAL_PUBLISH_TARGETS=<page id>,<instagram id>
```

Restart the hub (`Ctrl+C`, then `npm start`). On **Home**, "Facebook Page +
Instagram" turns green and the Social Publisher header says **live**.

---

## Part G — First real post (2 minutes, one approval)

1. Sidebar → **Social Publisher** → Chat.
2. Type: *Draft a one-line test post for the Page: "Testing our new
   publishing desk." Then publish it.*
3. An approval card appears. Read it, click **Approve**.
4. Open the Page in another tab: the post is there. Delete it from the Page
   if you like; the ledger and log keep the record.

Instagram posts need an image at a public URL, so the first Instagram test
waits for the R2 bucket (`SETUP.md`, part 4) or a public image link.

---

## If something fails

| You see | It means | Do |
|---|---|---|
| `Token rejected … 190` | expired or wrong token | Part E, step 7 again |
| a permission `MISSING` in the check | not added in the app, or token generated before adding it | Part D, then a **new** token (Part E 7) |
| `The token sees NO Pages` | system user not assigned the Page, or Page not in the portfolio | Part E step 4, Part B |
| Page listed, no `instagram` line | Instagram not professional, not connected to the Page, or in another portfolio | Part C |
| `(#200) … permission` when publishing | token lacks `pages_manage_posts` or `instagram_content_publish` | Part D + new token |
| `(#10) … Page Publishing Authorization` | Meta wants the Page admin to complete identity confirmation | Facebook → Page → Settings → follow the prompt, then retry |
