"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import TutorChat from "../components/TutorChat";

function ChatWithParams() {
  const searchParams = useSearchParams();
  const prefill = searchParams.get("q") || "";
  const provider = searchParams.get("provider") === "gemini" ? "gemini" : "groq";

  return <TutorChat initialMessage={prefill} provider={provider} />;
}

export default function ChatPage() {
  return (
    <Suspense>
      <ChatWithParams />
    </Suspense>
  );
}
