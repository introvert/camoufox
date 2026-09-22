# Copyright (c) Microsoft Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, Generator, List

import pytest

from playwright.async_api import (
    Browser,
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    Selectors,
    async_playwright,
)

from .utils import Utils
from .utils import utils as utils_object


@pytest.fixture
def utils() -> Generator[Utils, None, None]:
    yield utils_object


# Tests this suite cannot answer for, with the reason each was checked against.
#
# Everything here was run against the Playwright-bundled Firefox -- the suite
# falls back to it when CAMOUFOX_EXECUTABLE_PATH is unset -- and fails there
# identically. They are a vendored snapshot of playwright-python's tests that
# the pinned Playwright has since moved past, not defects in this fork, and
# leaving them failing hides the ones that would be. Re-check the list whenever
# the suite is re-vendored or the Playwright pin moves: a test that starts
# passing on stock should come back.
KNOWN_STALE = {
    "test_navigation.py::test_wait_for_load_state_should_wait_for_load_state_of_empty_url_popup":
        "expects an empty-url popup to report readyState 'uninitialized'; current Firefox does not",
    "test_page_add_locator_handler.py::test_should_wait_for_hidden_by_default_2":
        "the handler runs but the interstitial stays visible on Firefox, upstream behaviour",
    "test_page_clock.py::TestWhileRunning::test_should_pause":
        "asserts a 1000ms clock bound with no tolerance; measures 1005ms on an unloaded machine",
    "test_tracing.py::test_should_display_wait_for_load_state_even_if_did_not_wait_for_it":
        "trace expectations predate the pinned Playwright's tracing output",
    "test_tracing.py::test_should_work_with_playwright_context_managers":
        "trace expectations predate the pinned Playwright's tracing output",
    "test_websocket.py::test_should_emit_error_event":
        "the suite's own ws endpoint answers 404, so the error text is 'Not Found: 404'",
}


# Will mark all the tests as async
def pytest_collection_modifyitems(items: List[pytest.Item]) -> None:
    for item in items:
        item.add_marker(pytest.mark.asyncio)
        for test, reason in KNOWN_STALE.items():
            if item.nodeid.endswith(test) or f"{test}[" in item.nodeid:
                item.add_marker(pytest.mark.skip(reason=f"Fails on stock Firefox too: {reason}"))
                break


@pytest.fixture(scope="session")
async def playwright() -> AsyncGenerator[Playwright, None]:
    async with async_playwright() as playwright_object:
        yield playwright_object


@pytest.fixture(scope="session")
def browser_type(playwright: Playwright, browser_name: str) -> BrowserType:
    if browser_name == "chromium":
        return playwright.chromium
    if browser_name == "firefox":
        return playwright.firefox
    if browser_name == "webkit":
        return playwright.webkit
    raise Exception(f"Invalid browser_name: {browser_name}")


@pytest.fixture(scope="session")
async def browser_factory(
    launch_arguments: Dict, browser_type: BrowserType
) -> AsyncGenerator[Callable[..., Awaitable[Browser]], None]:
    browsers = []

    async def launch(**kwargs: Any) -> Browser:
        browser = await browser_type.launch(**launch_arguments, **kwargs)
        browsers.append(browser)
        return browser

    yield launch
    for browser in browsers:
        await browser.close()


@pytest.fixture(scope="session")
async def browser(
    browser_factory: "Callable[..., asyncio.Future[Browser]]",
) -> AsyncGenerator[Browser, None]:
    browser = await browser_factory()
    yield browser
    await browser.close()


@pytest.fixture(scope="session")
async def browser_version(browser: Browser) -> str:
    return browser.version


@pytest.fixture
async def context_factory(
    browser: Browser,
) -> AsyncGenerator["Callable[..., Awaitable[BrowserContext]]", None]:
    contexts = []

    async def launch(**kwargs: Any) -> BrowserContext:
        context = await browser.new_context(**kwargs)
        contexts.append(context)
        return context

    yield launch
    for context in contexts:
        await context.close()


@pytest.fixture(scope="session")
async def default_same_site_cookie_value(browser_name: str, is_linux: bool) -> str:
    if browser_name == "chromium":
        return "Lax"
    if browser_name == "firefox":
        return "None"
    if browser_name == "webkit" and is_linux:
        return "Lax"
    if browser_name == "webkit" and not is_linux:
        return "None"
    raise Exception(f"Invalid browser_name: {browser_name}")


@pytest.fixture
async def context(
    context_factory: "Callable[..., asyncio.Future[BrowserContext]]",
) -> AsyncGenerator[BrowserContext, None]:
    context = await context_factory()
    yield context
    await context.close()


@pytest.fixture
async def page(context: BrowserContext) -> AsyncGenerator[Page, None]:
    page = await context.new_page()
    yield page
    await page.close()


@pytest.fixture(scope="session")
def selectors(playwright: Playwright) -> Selectors:
    return playwright.selectors
