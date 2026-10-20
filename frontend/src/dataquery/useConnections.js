import { useEffect, useState } from "react";
import { api } from "../api.js";

/** 数据源名字清单 —— 知识库 / SQL 模板的「所属」下拉和过滤器都要用。
 *
 *  和 `DataSources.jsx` 共用同一个接口；这里只要名字，失败就当空列表（页面自己会有错误提示）。
 */
export function useConnectionNames() {
  const [names, setNames] = useState([]);
  useEffect(() => {
    let alive = true;
    api("/api/db-connections")
      .then((r) => (r.ok ? r.json() : { connections: [] }))
      .then((d) => { if (alive) setNames((d.connections || []).map((c) => c.name)); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  return names;
}

/** `'*'` 在 UI 上叫「全局」——它对所有数据源都生效。 */
export const GLOBAL = "*";
