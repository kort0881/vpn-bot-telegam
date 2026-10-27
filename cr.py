import asyncio, sys, time, collections, datetime
sys.path.insert(0, '.')
from database import Database

async def main():
    db = Database()
    await db.connect()
    due = await db.users_due_for_report(30 * 24 * 3600 * 1000)
    act = await db.get_active_users()
    print("всего активных:", len(act))
    print("к отправке (30+ дней):", len(due))
    now = int(time.time() * 1000)
    buckets = collections.Counter()
    for u in due:
        lr = u.get("last_report_at") or u["created_at"]
        days = int((now - lr) / (24 * 3600 * 1000))
        buckets[days] += 1
    top = sorted(buckets.items(), reverse=True)[:8]
    print("дней с последнего отчёта -> юзеров:", dict(top))
    if due:
        u = due[0]
        print("пример:", u["telegram_id"],
              "used_bytes=", u.get("used_bytes"),
              "expiry=", datetime.datetime.fromtimestamp(u["expiry_at"] / 1000).strftime("%d.%m.%Y"))
    await db.close()

asyncio.run(main())
