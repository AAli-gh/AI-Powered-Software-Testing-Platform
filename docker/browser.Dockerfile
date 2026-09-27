# Sandbox image for browser tests (Playwright): the sandbox image plus a headless Chromium.
# Runner images of projects with Playwright tests are built on it; tests run with the same
# isolation as every sandbox run (no network: the browser only reaches the application the
# tests start on 127.0.0.1).
#   docker build -f docker/browser.Dockerfile -t testmind-sandbox-browser:local .
ARG SANDBOX_IMAGE=testmind-sandbox:local
FROM ${SANDBOX_IMAGE}

USER root
# Readable by the unprivileged sandbox user; the browser build matches this Playwright version.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN pip install "playwright==1.56.0" "pytest-playwright==0.7.1" \
    && playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /opt/playwright

USER 65534:65534
LABEL org.testmind.browser="1"
