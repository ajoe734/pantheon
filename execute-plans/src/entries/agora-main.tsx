import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AskPersonas } from "@/agora/pages/AskPersonas";

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root not found");

createRoot(root).render(
  <StrictMode>
    <AskPersonas />
  </StrictMode>,
);
