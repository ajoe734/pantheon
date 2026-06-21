import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import AgoraApp from "@/agora/AgoraApp";

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root not found");

createRoot(root).render(
  <StrictMode>
    <AgoraApp />
  </StrictMode>,
);
