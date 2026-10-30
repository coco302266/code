"use client";

import { setTutorConsent, useTutorConsent } from "@/lib/consent";

export default function WelcomeModal() {
  const consent = useTutorConsent();

  const handleAgree = () => {
    setTutorConsent(true);
  };

  const handleDecline = () => {
    setTutorConsent(false);
  };

  if (consent !== null) return null;

  return (
     <div className="fixed inset-0 bg-blue bg-opacity-5 backdrop-blur-sm flex items-center justify-center z-50">
      <div className="bg-white dark:bg-gray-800 rounded-lg max-w-md w-full p-6 shadow-xl">
        <h2 className="text-xl font-bold mb-4">👋 Welcome to the AI Tutor Demo</h2>
        <p className="text-gray-700 dark:text-gray-300 mb-4">
          Hello user! This is the AI Tutor demo. It is designed to help with CS‑related questions,
          but feel free to try as you wish – even try to break it if you manage.
        </p>
        <p className="text-gray-700 dark:text-gray-300 mb-6">
          If you agree, at the end before you leave, use the <strong>Send Report</strong> button
          to send me the conversation you had with the chatbot. This helps me debug and continue developing.
        </p>
        <div className="flex justify-end gap-3">
          <button
            onClick={handleDecline}
            className="px-4 py-2 rounded border border-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            No, thanks
          </button>
          <button
            onClick={handleAgree}
            className="px-4 py-2 rounded bg-blue-600 text-white hover:bg-blue-700"
          >
            I agree
          </button>
        </div>
      </div>
    </div>
  );
}
