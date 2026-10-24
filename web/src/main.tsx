import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AuthProvider } from "./auth";
import { AuthGate } from "./AuthGate";
import { FeedbackLayer } from "./ui/feedback";
import { initAnalytics, pageView } from "./analytics";
import "./index.css";

initAnalytics();
const sendView = () => pageView(`/app/${location.hash.replace(/^#\/?/, "")}`);
sendView();
window.addEventListener("hashchange", sendView);

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
