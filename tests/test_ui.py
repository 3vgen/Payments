"""UI tests in a real (headless) Firefox."""
import time
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect, sync_playwright

TODAY = date.today()
CAT = "Сервисы и подписки"


def d(days=0):
    return (TODAY + timedelta(days=days)).isoformat()


def sub(name, next_date, cycle="month", amount=100, currency="RUB", **extra):
    return dict(name=name, amount=amount, currency=currency, cycle=cycle, nextDate=next_date,
                anchorDay=int(next_date[-2:]), category=CAT, **extra)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.firefox.launch()
        yield b
        b.close()


@pytest.fixture
def open_page(browser, server):
    pages = []

    def _open(width=1200, height=900, dark=False):
        ctx = browser.new_context(viewport={"width": width, "height": height},
                                  color_scheme="dark" if dark else "light")
        page = ctx.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and page.errors.append(m.text))
        page.goto(server.base + "/")
        page.wait_for_selector("#list .empty, #list .group")
        pages.append(page)
        return page

    yield _open
    for p in pages:
        assert p.errors == [], p.errors
        p.context.close()


def row(page, name):
    return page.locator("article.item", has_text=name)


def wait_for(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met")


# ---------- first load ----------

def test_overlay_and_toast_hidden_on_load(open_page):
    page = open_page()
    expect(page.locator("#overlay")).to_be_hidden()
    expect(page.locator("#toast")).to_be_hidden()
    expect(page.locator("#notice")).to_be_hidden()
    expect(page.locator("#list .empty")).to_contain_text("Пока нет ни одного платежа")
    # the add button is clickable, i.e. nothing covers it
    page.click("#add-btn", timeout=2000)
    expect(page.locator("#overlay")).to_be_visible()


# ---------- form ----------

def test_add_payment_through_form(open_page, server):
    page = open_page()
    page.click("#add-btn")
    page.fill("#f-name", "Кинопоиск")
    page.fill("#f-amount", "1 499,90")
    page.select_option("#f-cycle", "month")
    page.fill("#f-date", d(3))
    page.fill("#f-method", "Карта *4421")
    page.click("#f-save")
    expect(page.locator("#overlay")).to_be_hidden()
    expect(row(page, "Кинопоиск")).to_contain_text("через 3 дня")
    expect(row(page, "Кинопоиск")).to_contain_text("1 499,9")
    saved = list(server.subs().values())
    assert len(saved) == 1
    assert saved[0]["amount"] == 1499.9 and saved[0]["nextDate"] == d(3) and saved[0]["method"] == "Карта *4421"


@pytest.mark.parametrize("name,amount,error", [
    ("", "100", "Укажите название"),
    ("Тест", "abc", "Сумма должна быть"),
    ("Тест", "0", "Сумма должна быть"),
])
def test_form_validation(open_page, server, name, amount, error):
    page = open_page()
    page.click("#add-btn")
    page.fill("#f-name", name)
    page.fill("#f-amount", amount)
    page.click("#f-save")
    expect(page.locator("#f-err")).to_contain_text(error)
    expect(page.locator("#overlay")).to_be_visible()
    assert server.subs() == {}


def test_cancel_and_escape_close_form(open_page):
    page = open_page()
    page.click("#add-btn")
    page.click("#f-cancel")
    expect(page.locator("#overlay")).to_be_hidden()
    page.click("#add-btn")
    page.keyboard.press("Escape")
    expect(page.locator("#overlay")).to_be_hidden()


def test_edit_payment(open_page, server):
    server.put("e1", **sub("Связь", d(5), amount=650))
    page = open_page()
    row(page, "Связь").get_by_role("button", name="Изменить").click()
    expect(page.locator("#f-name")).to_have_value("Связь")
    page.fill("#f-amount", "700")
    page.click("#f-save")
    expect(row(page, "Связь")).to_contain_text("700")
    assert server.subs()["e1"]["amount"] == 700


# ---------- "Оплачено" ----------

def pay(page, name):
    row(page, name).get_by_role("button", name="Оплачено").click()


def test_pay_monthly_moves_one_month(open_page, server):
    server.put("m1", **sub("Квартплата", "2026-10-25"))
    page = open_page()
    pay(page, "Квартплата")
    wait_for(lambda: server.subs()["m1"]["nextDate"] == "2026-11-25")
    s = server.subs()["m1"]
    assert s["lastPaid"] == TODAY.isoformat()
    assert s["history"][-1]["due"] == "2026-10-25"
    expect(page.locator("#toast")).to_contain_text("25 ноя")


def test_pay_keeps_end_of_month(open_page, server):
    server.put("m31", **sub("Кредит", "2027-01-31"))
    page = open_page()
    pay(page, "Кредит")
    wait_for(lambda: server.subs()["m31"]["nextDate"] == "2027-02-28")
    expect(row(page, "Кредит")).to_contain_text("28")
    pay(page, "Кредит")
    wait_for(lambda: server.subs()["m31"]["nextDate"] == "2027-03-31")


@pytest.mark.parametrize("cycle,start,expected", [
    ("week", "2026-12-29", "2027-01-05"),
    ("quarter", "2026-11-30", "2027-02-28"),
    ("year", "2028-02-29", "2029-02-28"),
])
def test_pay_other_cycles(open_page, server, cycle, start, expected):
    server.put("c1", **sub("Цикл", start, cycle=cycle))
    page = open_page()
    pay(page, "Цикл")
    wait_for(lambda: server.subs()["c1"]["nextDate"] == expected)


def test_pay_once_removes_and_undo_restores(open_page, server):
    server.put("o1", **sub("Штраф", d(1), cycle="once"))
    page = open_page()
    pay(page, "Штраф")
    wait_for(lambda: "o1" not in server.subs())
    expect(row(page, "Штраф")).to_have_count(0)
    page.locator("#toast").get_by_role("button", name="Отменить").click()
    wait_for(lambda: "o1" in server.subs())
    expect(row(page, "Штраф")).to_have_count(1)


def test_pay_undo_restores_date(open_page, server):
    server.put("u1", **sub("Музыка", d(2)))
    page = open_page()
    pay(page, "Музыка")
    wait_for(lambda: server.subs()["u1"]["nextDate"] != d(2))
    page.locator("#toast").get_by_role("button", name="Отменить").click()
    wait_for(lambda: server.subs()["u1"]["nextDate"] == d(2))


# ---------- delete ----------

def test_delete_asks_inline_then_deletes(open_page, server):
    server.put("x1", **sub("Старая подписка", d(10)))
    page = open_page()
    r = row(page, "Старая подписка")
    r.get_by_role("button", name="Удалить Старая подписка").click()
    r.get_by_role("button", name="Нет").click()
    assert "x1" in server.subs()
    r.get_by_role("button", name="Удалить Старая подписка").click()
    r.get_by_role("button", name="Да").click()
    wait_for(lambda: "x1" not in server.subs())
    expect(page.locator("#list .empty")).to_be_visible()


# ---------- grouping, stats, calendar ----------

def test_groups_and_stats(open_page, server):
    server.put("g1", **sub("Просроченный", d(-2), amount=300))
    server.put("g2", **sub("Сегодняшний", d(0), amount=200))
    server.put("g3", **sub("Через месяц", d(20), amount=1000))
    server.put("g4", **sub("Годовой", d(100), cycle="year", amount=1200))
    server.put("g5", **sub("В долларах", d(4), amount=20, currency="USD"))
    page = open_page()
    titles = page.locator(".group h2").evaluate_all("hs => hs.map(h => h.firstChild.textContent)")
    assert titles == ["Просрочено", "На этой неделе", "В ближайший месяц", "Позже"]
    expect(page.locator(".group").first.locator("article.item")).to_have_count(1)
    expect(row(page, "Просроченный")).to_contain_text("просрочено на 2 дня")
    expect(row(page, "Сегодняшний")).to_contain_text("сегодня")
    stats = page.locator("#stats .stat")
    expect(stats.nth(0)).to_contain_text("300 ₽")
    expect(stats.nth(1)).to_contain_text("200 ₽")
    expect(stats.nth(1)).to_contain_text("$20")
    # monthly: 300+200+1000 + 1200/12 = 1600 ₽ and $20
    expect(stats.nth(2)).to_contain_text("1 600 ₽")
    expect(stats.nth(3)).to_contain_text("19 200 ₽")


def test_category_filter(open_page, server):
    server.put("f1", **sub("Ютуб", d(3)))
    server.put("f2", **dict(sub("Свет", d(4)), category="ЖКХ"))
    page = open_page()
    page.locator("#filter").get_by_role("button", name="ЖКХ").click()
    expect(row(page, "Свет")).to_have_count(1)
    expect(row(page, "Ютуб")).to_have_count(0)
    page.locator("#filter").get_by_role("button", name="Все").click()
    expect(row(page, "Ютуб")).to_have_count(1)


def test_calendar_marks_payment_day(open_page, server):
    target = TODAY.replace(day=min(TODAY.day + 1, 28)) if TODAY.day < 28 else TODAY
    server.put("k1", **sub("Календарный", target.isoformat()))
    page = open_page()
    cell = page.locator("#cal .c.has", has_text=str(target.day))
    expect(cell.first).to_be_visible()
    expect(page.locator("#month-sum")).to_contain_text("Календарный")
    page.click("#next-m")
    expect(page.locator("#month-sum")).to_contain_text("Календарный")  # projected next month


def test_examples_cleanup(open_page, server):
    server.put("ex1", **sub("Пример", d(3), example=True))
    server.put("r1", **sub("Настоящий", d(3)))
    page = open_page()
    page.click("#clear-ex")
    wait_for(lambda: list(server.subs()) == ["r1"])
    expect(page.locator("#clear-ex")).to_be_hidden()


def test_sync_from_another_device(open_page, server):
    page = open_page()
    server.put("s1", **sub("С телефона", d(3)))
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(row(page, "С телефона")).to_have_count(1)


def test_server_down_shows_notice(open_page, server):
    page = open_page()
    server.stop()
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.locator("#notice")).to_contain_text("Сервер не отвечает")
    page.errors.clear()  # the failed fetch is expected here


# ---------- layout ----------

@pytest.mark.parametrize("dark", [False, True])
def test_phone_width_no_horizontal_scroll(open_page, server, dark):
    server.put("p1", **sub("Очень длинное название подписки на онлайн-кинотеатр", d(1), amount=12999.5))
    page = open_page(width=375, height=800, dark=dark)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == ("rgb(14, 16, 32)" if dark else "rgb(242, 243, 247)")
    page.click("#add-btn")
    box = page.locator("form.sheet").bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 375
