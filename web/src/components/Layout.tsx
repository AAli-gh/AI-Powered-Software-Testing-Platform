import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, useSyncExternalStore } from "react";
import { NavLink, Outlet } from "react-router";

import { readToken, saveToken, subscribeToken } from "../api/client";

type Theme = "system" | "light" | "dark";
const THEME_KEY = "testmind.theme";

function storedTheme(): Theme {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function ThemeSelect() {
  const [theme, setTheme] = useState<Theme>(storedTheme);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // Storage may be unavailable (private mode); the choice then lasts for this page only.
    }
  }, [theme]);

  return (
    <label className="field">
      Theme
      <select
        value={theme}
        onChange={(event) => {
          setTheme(event.target.value as Theme);
        }}
      >
        <option value="system">System</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  );
}

function ForgetToken() {
  const client = useQueryClient();
  const present = useSyncExternalStore(subscribeToken, () => readToken() !== null);
  if (!present) return null;
  return (
    <button
      type="button"
      onClick={() => {
        saveToken(null);
        void client.resetQueries();
      }}
    >
      Forget API token
    </button>
  );
}

const LINKS = [
  { to: "/", label: "Overview", end: true },
  { to: "/projects", label: "Projects", end: false },
  { to: "/github", label: "Pull requests", end: false },
];

export function Layout() {
  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main">
        <NavLink to="/" className="brand">
          <img src="/favicon.svg" alt="" />
          TestMind
        </NavLink>
        {LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
          >
            {link.label}
          </NavLink>
        ))}
        <div className="sidebar-footer">
          <ThemeSelect />
          <ForgetToken />
          <a href="/docs" target="_blank" rel="noreferrer">
            API reference
          </a>
        </div>
      </nav>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
