import { useState } from "react";
import Chat from "./Chat.jsx";
import ModelConfigs from "./ModelConfigs.jsx";

export default function App() {
  const [view, setView] = useState("chat");

  return (
    <div className="app">
      <nav className="tabs">
        <button className={view === "chat" ? "tab active" : "tab"} onClick={() => setView("chat")}>
          对话
        </button>
        <button className={view === "models" ? "tab active" : "tab"} onClick={() => setView("models")}>
          模型配置
        </button>
      </nav>
      {view === "chat" ? <Chat /> : <ModelConfigs />}
    </div>
  );
}