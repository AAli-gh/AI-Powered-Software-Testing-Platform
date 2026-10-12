"""Crawl and exercise a website like a user: pages, routes, buttons and forms, with evidence.

Deterministic, no LLM. Runs a headless browser (Playwright) in the sandbox, whose network is
opened only to the target's allowed hosts (network_policy.py). See docs/web-testing.md.
"""
