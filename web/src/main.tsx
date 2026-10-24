import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AuthProvider } from "./auth";
import { AuthGate } from "./AuthGate";
import { FeedbackLayer } from "./ui/feedback";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider>
      <AuthGate>
        <App />
      </AuthGate>
      <FeedbackLayer />
    </AuthProvider>
  </StrictMode>,
);
