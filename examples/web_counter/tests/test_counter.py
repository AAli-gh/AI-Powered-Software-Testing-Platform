from playwright.sync_api import Page, expect


def test_page_title(page: Page):
    page.goto("/")
    expect(page).to_have_title("Counter")


def test_clicking_increments_the_counter(page: Page):
    page.goto("/")
    page.click("#increment")
    page.click("#increment")
    expect(page.locator("#count")).to_have_text("2")
