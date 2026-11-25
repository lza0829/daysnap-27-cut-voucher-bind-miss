import React, { useEffect, useState } from "react";
import { api } from "../api.js";

function 短凭证(编号) {
  const s = String(编号 || "");
  return s.length > 18 ? `${s.slice(0, 10)}…${s.slice(-6)}` : s || "—";
}

export default function 变动流水() {
  const [夹具, set夹具] = useState([]);
  const [选, set选] = useState("切点前后夹具.json");
  const [行, set行] = useState([]);
  const [凭证, set凭证] = useState(null);
  const [审计, set审计] = useState([]);
  const [切点输入, set切点输入] = useState("23:00");
  const [讯, set讯] = useState("");

  const 刷新 = async () => {
    const [流水行, 夹具单, 当前凭证, 审计行] = await Promise.all([
      api.流水(),
      api.夹具(),
      api.当前切点(),
      api.切点审计(),
    ]);
    set行(流水行);
    set夹具(夹具单);
    set凭证(当前凭证);
    set审计(审计行);
    if (当前凭证?.切点时刻) set切点输入(String(当前凭证.切点时刻).slice(0, 5));
  };

  useEffect(() => {
    刷新().catch((e) => set讯(String(e.message || e)));
  }, []);

  const 装载选中夹具 = () => {
    api
      .装载夹具(选)
      .then((r) =>
        set讯(
          `装载完成：入账=${r.入账 ?? 0} 旁置=${r.旁置 ?? 0} 幂等拒绝=${r.幂等拒绝 ?? 0}；口径=${r.架构 || "乙·冻结列永冻"}`
        )
      )
      .then(刷新)
      .catch((e) => set讯(String(e.message || e)));
  };

  const 切换 = () => {
    api
      .切换切点({ 切点时刻: 切点输入, 时区: 凭证?.时区 || "UTC", 原因: "流水页手工切换" })
      .then((r) => set讯(`切点凭证已切换：${r.凭证编号} → ${r.切点时刻}；旧流水归属仍冻结不变`))
      .then(刷新)
      .catch((e) => set讯(String(e.message || e)));
  };

  return (
    <div>
      <h1 className="题">变动流水</h1>
      <p className="述">
        装入账户增减流水。全局执行乙口径：装载时按切点凭证计算并永久冻结「归属日」，抽取与回放只读取该列，切点切换不改变旧行归属。
      </p>

      <div className="提示条">
        当前切点凭证：<b>{凭证?.凭证编号 || "—"}</b>
        {"　"}切点 <b>{凭证?.切点时刻 || "—"}</b>（{凭证?.时区 || "UTC"}）
        {"　"}架构 <b>{凭证?.架构 || "乙-冻结列永冻"}</b>
        {"　"}归属可变性 <b>不可变 / valid 列见下表</b>
      </div>

      <div className="行">
        <select value={选} onChange={(e) => set选(e.target.value)}>
          {夹具.map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
        </select>
        <button onClick={装载选中夹具}>装载夹具</button>
        <input
          aria-label="新切点时刻"
          value={切点输入}
          onChange={(e) => set切点输入(e.target.value)}
          style={{ width: 90 }}
        />
        <button className="次" onClick={切换}>切换切点凭证</button>
        <button className="次" onClick={() => 刷新().catch((e) => set讯(String(e.message || e)))}>刷新</button>
      </div>
      {讯 ? <p className="讯">{讯}</p> : null}

      {!行.length ? (
        <div className="空态">暂无流水</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>流水号</th>
              <th>账户键</th>
              <th>归属日（冻结）</th>
              <th>申报日</th>
              <th>发生时</th>
              <th>变动额</th>
              <th>切点凭证</th>
              <th>valid</th>
              <th>归属可变</th>
              <th>摘要</th>
              <th>命运</th>
            </tr>
          </thead>
          <tbody>
            {行.map((r) => {
              const valid = r.valid ?? (Boolean(r.归属日) && Number(r.归属冻结 ?? 1) === 1 && r.命运 === "已入账");
              return (
                <tr key={r.流水号}>
                  <td>{r.流水号}</td>
                  <td>{r.账户键}</td>
                  <td><b>{r.归属日 || r.营业日}</b></td>
                  <td>{r.申报营业日 || "—"}</td>
                  <td>{r.发生时}</td>
                  <td>{r.变动额}</td>
                  <td title={r.切点凭证编号 || ""}>{短凭证(r.切点凭证编号)}</td>
                  <td>{valid ? "true" : "false"}</td>
                  <td>false（永冻）</td>
                  <td>{r.摘要}</td>
                  <td>{r.命运}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      )}

      <h2 className="题" style={{ marginTop: 24, fontSize: 18 }}>切点重放审计</h2>
      <p className="述">同流水号在切点切换后重放时，记录冻结归属日与按当前凭证本应重算的日期；乙口径下实际归属日不变。</p>
      {!审计.length ? (
        <div className="空态">暂无重放审计</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>流水号</th>
              <th>冻结归属日</th>
              <th>当前凭证重算日</th>
              <th>重放凭证</th>
              <th>处置</th>
              <th>时刻</th>
            </tr>
          </thead>
          <tbody>
            {审计.slice(0, 50).map((a) => (
              <tr key={a.编号}>
                <td>{a.流水号}</td>
                <td><b>{a.冻结归属日}</b></td>
                <td>{a.凭证当前重算归属日 || "—"}</td>
                <td title={a.重放时凭证编号}>{短凭证(a.重放时凭证编号)}</td>
                <td>{a.处置}</td>
                <td>{a.时刻}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
