"use client";

import { useSyncExternalStore } from "react";

const CONSENT_KEY = "tutorConsent";
const CONSENT_EVENT = "tutor-consent-change";

function subscribe(callback: () => void) {
  window.addEventListener("storage", callback);
  window.addEventListener(CONSENT_EVENT, callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(CONSENT_EVENT, callback);
  };
}

function getSnapshot(): boolean | null {
  const value = localStorage.getItem(CONSENT_KEY);
  return value === null ? null : value === "true";
}

function getServerSnapshot(): null {
  return null;
}

export function useTutorConsent(): boolean | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function setTutorConsent(value: boolean): void {
  localStorage.setItem(CONSENT_KEY, String(value));
  window.dispatchEvent(new Event(CONSENT_EVENT));
}
