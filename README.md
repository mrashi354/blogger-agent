# Daily Blogger Post Agent

Ek Python script jo roz ek naya blog post likhta hai (Grok se), uske liye
Pixabay se image laata hai, aur Blogger pe publish kar deta hai.
Sab kuch GitHub Actions se apne aap chalta hai.

---

## 1. Local setup

```bash
git clone https://github.com/<username>/<repo>.git
cd blogger-agent
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

Ab `.env` file ko kholo aur keys bhardo.

## 2. `.env` mein kya daalna hai

| Variable | Kahan se milega |
|---|---|
| `LLM_API_KEY` | https://console.groq.com/keys se free key (no card), ya https://console.x.ai se xAI key |
| `LLM_MODEL` | Model ka naam: Groq ke liye `openai/gpt-oss-120b`, xAI ke liye `grok-4.7` |
| `LLM_BASE_URL` | Groq ke liye `https://api.groq.com/openai/v1`, xAI ke liye khali chhod dein |
| `PIXABAY_API_KEY` | https://pixabay.com/api/docs/ se free key |
| `GOOGLE_CLIENT_ID` | Google Cloud -> OAuth client ID (Desktop app) |
| `GOOGLE_CLIENT_SECRET` | Same screen pe client secret |
| `GOOGLE_REFRESH_TOKEN` | `python auth_setup.py` se milega (step 3) |
| `BLOG_ID` | Blog URL ka last part, jaise `1234567890123456789` |
| `DRAFT_MODE` | `true` = draft me publish, `false` = live |
| `BLOG_NICHE` | Jaise `Personal Finance & Money` |
| `BLOG_LANGUAGE` | `Hinglish` / `English` / `Hindi` |
| `GITHUB_REPOSITORY` | `username/repo`, CI me apne aap set ho jata hai |

## 3. Google OAuth setup (ek hi baar)

1. https://console.cloud.google me project banao
2. **APIs & Services -> Library** -> **Blogger API** enable karo
3. **OAuth consent screen** -> apna email add karo (test user bhi add kar do)
4. **Credentials -> Create Credentials -> OAuth client ID -> Desktop app**
5. Client ID aur secret `.env` me daalo
6. Terminal me chalao:

```bash
python auth_setup.py
```

Browser khulega, **Allow** dabao. Script ek refresh token print karega.
Use copy karke:
- `.env` me `GOOGLE_REFRESH_TOKEN=` ke baad paste karo
- GitHub Secret me bhi same value daalo (step 5)

> Ye token bahut sensitive hai. Kabhi commit ya share mat karo.

## 4. Test karo (sabse pehle)

```bash
python main.py --dry-run    # sirf print, kuch publish nahi hota
```

Sahi lag raha hai? phir pehla real test hamesha draft me:

```bash
DRAFT_MODE=true python main.py --draft
```

Blogger me draft post dikhega. Theek lage toh `DRAFT_MODE=false` karke live karein.

## 5. GitHub Secrets

Repo -> **Settings -> Secrets and variables -> Actions -> New repository secret**

Ye 11 secrets add karo:

```
LLM_API_KEY
LLM_MODEL
LLM_BASE_URL
PIXABAY_API_KEY
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REFRESH_TOKEN
BLOG_ID
DRAFT_MODE
BLOG_NICHE
BLOG_LANGUAGE
```

## 6. Repo public karo

**Bahut zaroori hai.** Images ka CDN link
`https://cdn.jsdelivr.net/gh/<username>/<repo>@main/images/<file>.jpg`
kaam karta hai sirf tab jab repo **public** ho. Private repo me link fail hoga.

Repo -> **Settings -> General -> Danger Zone -> Change visibility -> Public**

## 7. Code push karo

```bash
git init
git add .
git commit -m "initial commit: daily blogger agent"
git branch -M main
git remote add origin https://github.com/<username>/<repo>.git
git push -u origin main
```

## 8. Workflow manually chalao

Repo -> **Actions** -> **Daily Blog Post** -> **Run workflow**
`draft` ko `true` rakhein. Green tick aaye toh Blogger me draft post mil jayega.

---

## Schedule

Workflow roz `03:30 UTC` chalta hai jo **9:00 AM IST** hai
(IST = UTC+5:30, isliye 09:00 - 05:30 = 03:30 UTC).
GitHub hamesha UTC use karta hai, isliye cron me `30 3 * * *` likha hai.

---

## Files

| File | Kaam |
|---|---|
| `main.py` | Poora flow: topic -> post -> image -> publish -> log |
| `config.py` | `.env` padhta hai, saari settings yahan |
| `content_generator.py` | Grok se JSON post banata hai, validate karta hai |
| `image_handler.py` | Pixabay search + download + CDN URL |
| `blogger_client.py` | Blogger API v3 publish |
| `auth_setup.py` | One-time refresh token nikalne ka script |
| `git_sync.py` | CI me image publish se pehle push karta hai |
| `retry_helper.py` | Exponential backoff ke saath retry |
| `topics.json` | Topic ideas (naye topics yahan daalo) |
| `posted_log.json` | Kya-kya post ho chuka hai, duplicate rokta hai |
| `logs/` | Daily log files (git me nahi jaata) |

## Naye topics kaise daalein

`topics.json` me strings add kar do. Script wahi use karegi.
Topic list khatam ho jaye toh error aayega, phir aur add kar do.

## Common problems

**Image load nahi ho rahi post me** -> repo public nahi hai. Step 6 dekho.
**`Access blocked: has not completed verification`** -> Google consent screen
me apne email ko test user add karo.
**`Refresh token invalid`** -> https://myaccount.google.com/permissions pe
jao, app hatao, `auth_setup.py` dubara chalao.
**Workflow `Invalid ref` ya 403** -> Actions me repo me permissions ON nahi
hain. Settings -> Actions -> General -> Workflow permissions -> Read and write.