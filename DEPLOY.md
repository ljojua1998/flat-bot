# სერვერზე გაშვება — ნაბიჯ-ნაბიჯ (DigitalOcean + Docker + CI/CD)

ეს გზამკვლევი ბოტს უშვებს შენს DigitalOcean სერვერზე Docker-ით, და აყენებს
pipeline-ს ისე, რომ ყოველი `git push` სერვერზე თავისით გადავიდეს.

---

## 0. რა გვჭირდება
- DigitalOcean ანგარიში (ბარათი)
- SSH გასაღები (ქვემოთ შევქმნით)
- GitHub რეპო `ljojua1998/flat-bot` (უკვე გვაქვს)

---

## 1. SSH გასაღების შექმნა (შენს კომპიუტერზე)

PowerShell-ში:

    ssh-keygen -t ed25519 -C "flat-bot" -f $HOME\.ssh\flatbot

- **ამ გასაღებს პაროლს ნუ დაუყენებ** (უბრალოდ Enter) — თორემ CI ვერ გამოიყენებს.
- შეიქმნება ორი ფაილი:
  - `flatbot`     → **privat** გასაღები (არავის აჩვენო)
  - `flatbot.pub` → **public** გასაღები (ამას სერვერს ვაძლევთ)

public გასაღების ტექსტის სანახავად:

    Get-Content $HOME\.ssh\flatbot.pub

---

## 2. Droplet-ის შექმნა (DigitalOcean)
1. Create → **Droplets**
2. Region: **Frankfurt** (ახლოსაა)
3. Image: **Ubuntu 24.04 LTS**
4. Size: ყველაზე იაფი — **Basic / Regular / $4–6**
5. Authentication: **SSH Key** → **Add New SSH Key** → ჩასვი `flatbot.pub`-ის ტექსტი
6. Create Droplet. მიიღებ **IP მისამართს** (მაგ. `164.92.x.x`)

---

## 3. პირველი შესვლა სერვერზე

    ssh -i $HOME\.ssh\flatbot root@SHENI_IP

(`SHENI_IP` ჩაანაცვლე რეალურით). პირველ ჯერზე „yes“ დაწერე.

---

## 4. Docker-ის დაყენება სერვერზე

    curl -fsSL https://get.docker.com | sh

შემოწმება:

    docker --version
    docker compose version

---

## 5. კოდის ჩამოტანა და .env

    mkdir -p /opt && cd /opt
    git clone https://github.com/ljojua1998/flat-bot.git
    cd flat-bot

`.env` ფაილის შექმნა (ტოკენი **მხოლოდ აქ**, git-ში არასდროს):

    cat > .env <<'EOF'
    TELEGRAM_BOT_TOKEN=შენი_ტოკენი
    ADMIN_CHAT_IDS=1810261069
    POLL_SECONDS=600
    DEFAULT_MAX_URBANS=4
    EOF

---

## 6. პირველი გაშვება

    docker compose up -d --build

შემოწმება:

    docker compose logs -f

უნდა დაინახო: `bot @flat_tracker_ge_bot running`.
(გამოსვლა ლოგებიდან: `Ctrl+C` — ბოტი მაინც მუშაობს ფონში.)

---

## 7. CI/CD — push-ზე ავტომატური გადასვლა

GitHub რეპოში: **Settings → Secrets and variables → Actions → New repository secret**.
დაამატე სამი:

| Name | Value |
|------|-------|
| `DROPLET_HOST` | სერვერის IP |
| `DROPLET_USER` | `root` |
| `DROPLET_SSH_KEY` | **privat** გასაღების სრული ტექსტი (`Get-Content $HOME\.ssh\flatbot`) |

ამის მერე ყოველი `git push origin main` → GitHub Actions სერვერზე შედის,
`git pull` + `docker compose up -d --build`. ხელით აღარაფერი.

ხელით გასაშვებადაც: რეპო → **Actions → deploy → Run workflow**.

---

## 8. სხვა ბოტების დამატება იმავე სერვერზე

თითო ბოტი — თავისი საქაღალდე `/opt`-ში, თავისი `docker-compose.yml`:

    cd /opt
    git clone https://github.com/ljojua1998/other-bot.git
    cd other-bot
    docker compose up -d --build

კონტეინერები ერთმანეთისგან იზოლირებულია — ერთი მეორეს ხელს არ უშლის.
ყველას ერთად სანახავად: `docker ps`.

---

## 9. ხშირი ბრძანებები

| რა | ბრძანება |
|----|----------|
| სტატუსი | `docker ps` |
| ლოგები | `docker compose logs -f` (საქაღალდიდან) |
| გადატვირთვა | `docker compose restart` |
| გაჩერება | `docker compose down` |
| განახლება ხელით | `git pull && docker compose up -d --build` |
| ადგილის გასუფთავება | `docker image prune -f` |

---

## შენიშვნა
- ბოტის ერთი ტოკენი ერთ ადგილას უნდა მუშაობდეს. სანამ სერვერზე გაუშვებ,
  შენს კომპიუტერზე და Railway-ზე (თუ გაშვებულია) გააჩერე.
- ბაზა სერვერზე `/opt/flat-bot/data/` საქაღალდეშია — სწორედ ეს უნდა დაიბექაპო.
