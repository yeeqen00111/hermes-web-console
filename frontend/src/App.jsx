import { useState } from "react";
import Chat from "./Chat.jsx";
import ModelConfigs from "./ModelConfigs.jsx";

export default function App() {
  const [view, setView] = useState("chat");

  return (
    <div className="app">
      <nav className="tabs" aria-label="主导航">
        <button className={view === "chat" ? "tab active" : "tab"} aria-pressed={view === "chat"} aria-controls="chat-view" onClick={() => setView("chat")}>
          对话
        </button>
        <button className={view === "models" ? "tab active" : "tab"} aria-pressed={view === "models"} onClick={() => setView("models")}>
          模型配置
        </button>
      </nav>
      <div id="chat-view" className="chat-view" hidden={view !== "chat"}>
        <Chat active={view === "chat"} />
      </div>
      {view === "models" && <ModelConfigs />}
    </div>
  );
}