# Opencode — Solana Meme Coin AI Trading Bot v2

AI-powered Solana মেমে কয়েন ট্র্যাকার যা **মাইগ্রেশনের পর ৩x+ পাম্প করা কয়েন থেকে অটো লার্ন** করে এবং **লঞ্চের ১০ মিনিটের মধ্যে** সিগন্যাল দেয়।

---

## 🚀 ফিচারসমূহ

- **Pre-Migration সিগন্যাল** — PumpPortal WebSocket দিয়ে মাইগ্রেশনের আগে ধরা
- **Post-Migration ট্র্যাকিং** — DexScreener দিয়ে ১০ মিনিটের মধ্যে সিগন্যাল
- **অটো লার্নিং** — ৩x+ পাম্প ভেরিফাই হলে প্যাটার্ন শেখে
- **AI স্কোরিং** — Z-score ভিত্তিক নর্মালাইজেশন, success-rate per hour
- **Telegram কন্ট্রোল** — `/pump`, `/dump`, `/threshold`, `/health`, `/config`
- **গ্রেসফুল শাটডাউন** — SIGINT/SIGTERM হ্যান্ডলিং
- **Async GitHub সিঙ্ক** — `asyncio.subprocess` ব্যবহার
- **Rate-Limited API** — DexScreener exponential backoff
- **বর্ধিত রিস্ক চেক** — Honeypot, high tax, single holder
- **Backtesting System** — ৩০ দিনের historical data দিয়ে AI validate, স্বয়ংক্রিয় report

---

## 📦 ইনস্টলেশন

```bash
git clone https://github.com/bappix25-hub/Opencode.git
cd Opencode
pip install -r requirements.txt
cp .env.example .env
# .env এ BOT_TOKEN, CHAT_ID, HELIUS_API_KEY সেট করুন
```

---

## 🏃 রান

```bash
python meme_bot.py
```

---

## 🎮 টেলিগ্রাম কমান্ড

| কমান্ড | কাজ |
|--------|-----|
| `/start` | বট শুরু, কমান্ড লিস্ট |
| `/pump ADDRESS` | পাম্প শেখান (ভেরিফাই সহ) |
| `/forcepump ADDRESS` | ফোর্স পাম্প (ভেরিফাই ছাড়া) |
| `/dump ADDRESS` | ডাম্প শেখান |
| `/threshold 50` | AI থ্রেশোল্ড সেট (১-১০০) |
| `/health` | বটের স্বাস্থ্য পরীক্ষা |
| `/config` | কনফিগারেশন দেখুন |
| `/backtest 30` | ৩০ দিনের backtest (ডিফল্ট) |
| `/backtest 7` | ৭ দিনের backtest |
| `/lastbacktest` | শেষ backtest রিপোর্ট দেখাও |

### কীবোর্ড বাটন
- 📊 স্ট্যাটাস / 📈 পারফরম্যান্স / 🏆 ট্রেন / ⚙️ সেটিংস
- ✅ অন / ❌ অফ

---

## 📁 ফাইল স্ট্রাকচার

```
Opencode/
├── config.py              # সেন্ট্রাল কনফিগ
├── bot_state.py           # Thread-safe state
├── utils.py               # হেল্পার
├── dex_client.py          # DexScreener (rate-limited)
├── rugcheck_client.py     # Rugcheck (বর্ধিত)
├── helius_client.py       # Helius RPC
├── pumpportal_ws.py       # WebSocket
├── learner.py             # AI ইঞ্জিন
├── github_sync.py         # Async Git
├── backtest.py            # Backtesting engine
├── telegram_bot.py        # কমান্ড হ্যান্ডলার
├── meme_bot.py            # মেইন অর্কেস্ট্রেটর
├── backtest_reports/      # Backtest JSON রিপোর্ট
├── backtest_summary.md    # Latest backtest summary
├── tests/
│   ├── test_learner.py
│   └── test_backtest.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 🧠 AI লজিক

1. **প্যাটার্ন এক্সট্রাকশন** — mcap, liquidity, volume, buys, age, price change
2. **সফলতা-ভিত্তিক ওজন** — Hourly success rate (raw count নয়)
3. **এজ উইন্ডো** — শুধু সফল পাম্প (multiplier ≥ 3x) থেকে
4. **অটো থ্রেশোল্ড টিউনিং** — accuracy < 40% হলে threshold বাড়ায়, > 70% হলে কমায়
5. **ডাম্প ম্যাচ** — যদি কয়েন ডাম্প প্যাটার্নের সাথে মিলে তবে score কমায়

---

## 📊 সিগন্যাল ক্যাটেগরি

| টাইপ | উৎস | উইন্ডো | রিস্ক |
|-------|------|--------|-------|
| **Pre-Migration** | PumpPortal WS | লঞ্চের ৩০s+ | High |
| **Early Post-Migration** | DexScreener | ০-১০ মিনিট | Medium |
| **History Learn** | DexScreener | ১ ঘণ্টা পর পর | Auto |

---

## ⚙️ Environment Variables

পূর্ণ লিস্টের জন্য `.env.example` দেখুন। মূল ভেরিয়েবল:

```bash
BOT_TOKEN=...           # Required
CHAT_ID=...             # Required
HELIUS_API_KEY=...      # Required
AI_THRESHOLD=0.50       # 0.0 - 1.0
PUMP_MULTIPLIER=3.0     # পাম্প ভেরিফিকেশন
ENABLE_PRE_MIGRATION=true
ENABLE_GITHUB_SYNC=true
```

---

## 🧪 টেস্ট

```bash
python -m pytest tests/
```

---

## 📝 লাইসেন্স

MIT
